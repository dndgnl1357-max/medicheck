"""상호작용 데이터 저장소.

data/processed/interactions.csv 가 있으면 그걸 쓰고, 없으면 data/seed 의
개발용 시드 데이터로 폴백한다. 실제 공공데이터를 넣기 전에도 API 가 돌아가게
하기 위한 구조이며, 폴백 상태는 응답과 로그에 그대로 드러난다.

CSV 스키마 (scripts/build_ddi_matrix.py 가 만들어내는 형식):
    ingredient_a, ingredient_b, severity, mechanism, advice,
    min_interval_hours, source
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["ingredient_a", "ingredient_b", "severity"]
OPTIONAL_COLUMNS = ["mechanism", "advice", "min_interval_hours", "source"]


@dataclass(frozen=True)
class Interaction:
    ingredient_a: str
    ingredient_b: str
    severity: int
    mechanism: str = ""
    advice: str = ""
    min_interval_hours: float | None = None
    source: str = ""


def _pair_key(a: str, b: str) -> tuple[str, str]:
    """순서에 상관없이 같은 키가 나오도록 정렬한다."""
    return (a, b) if a <= b else (b, a)


class InteractionRepository:
    def __init__(
        self,
        interactions: list[Interaction],
        *,
        is_seed: bool = False,
        supplementary_pairs: int = 0,
    ) -> None:
        self.is_seed = is_seed
        self.supplementary_pairs = supplementary_pairs
        self._by_pair: dict[tuple[str, str], Interaction] = {}
        for item in interactions:
            key = _pair_key(item.ingredient_a, item.ingredient_b)
            # 같은 쌍이 여러 출처에서 들어오면 가장 심각한 등급을 남긴다.
            existing = self._by_pair.get(key)
            if existing is None or item.severity > existing.severity:
                self._by_pair[key] = item

    @staticmethod
    def read_rows(path: Path) -> list[Interaction]:
        """CSV 한 장을 Interaction 목록으로 읽는다."""
        df = pd.read_csv(path, dtype=str).fillna("")

        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"{path.name} 에 필수 컬럼이 없습니다: {missing}")

        for col in OPTIONAL_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        items: list[Interaction] = []
        for row in df.itertuples(index=False):
            a, b = row.ingredient_a.strip(), row.ingredient_b.strip()
            if not a or not b or a == b:
                continue
            interval = row.min_interval_hours.strip()
            items.append(
                Interaction(
                    ingredient_a=a,
                    ingredient_b=b,
                    severity=max(1, min(5, int(float(row.severity)))),
                    mechanism=row.mechanism.strip(),
                    advice=row.advice.strip(),
                    min_interval_hours=float(interval) if interval else None,
                    source=row.source.strip(),
                )
            )
        return items

    @classmethod
    def from_csv(cls, path: Path, *, is_seed: bool = False) -> InteractionRepository:
        return cls(cls.read_rows(path), is_seed=is_seed)

    @classmethod
    def load(cls, processed_dir: Path, seed_dir: Path) -> InteractionRepository:
        """정제 데이터를 우선 쓰되, 시드가 채우던 빈칸은 남긴다.

        정제 데이터(DUR 병용금기)에는 영양제·건강기능식품 상호작용이 거의 없다.
        와파린 × 오메가3 같은 조합이 그렇다. 그런데 그게 이 서비스의 차별점이라,
        실제 데이터가 들어왔다고 시드를 통째로 버리면 기능이 후퇴한다.

        그래서 정제 데이터를 1순위로 깔고 시드를 보조로 병합한다. 같은 쌍이
        양쪽에 있으면 __init__ 의 규칙대로 더 심각한 등급이 남는다. 각 상호작용의
        출처는 `source` 필드에 그대로 실려 나가므로, 어디서 온 정보인지는
        응답에서 확인할 수 있다.
        """
        processed = processed_dir / "interactions.csv"
        seed = seed_dir / "interactions_seed.csv"

        if not processed.exists():
            logger.warning(
                "정제된 상호작용 데이터(%s)가 없어 개발용 시드 데이터로 동작합니다. "
                "scripts/build_ddi_matrix.py 를 실행해 실제 데이터를 생성하세요.",
                processed,
            )
            return cls.from_csv(seed, is_seed=True)

        primary = cls.read_rows(processed)
        if not seed.exists():
            return cls(primary)

        primary_keys = {
            _pair_key(i.ingredient_a, i.ingredient_b) for i in primary
        }
        supplementary = cls.read_rows(seed)
        added = {
            _pair_key(i.ingredient_a, i.ingredient_b) for i in supplementary
        } - primary_keys

        if added:
            logger.info(
                "정제 데이터 %d쌍에 시드 %d쌍을 보조로 병합했습니다 "
                "(정제 데이터에 없던 조합).",
                len(primary_keys),
                len(added),
            )
        return cls(primary + supplementary, supplementary_pairs=len(added))

    def __len__(self) -> int:
        return len(self._by_pair)

    @property
    def ingredients(self) -> set[str]:
        return {name for pair in self._by_pair for name in pair}

    def get(self, a: str, b: str) -> Interaction | None:
        return self._by_pair.get(_pair_key(a, b))

    def find_all(self, ingredients: list[str]) -> list[Interaction]:
        """성분 목록에서 나올 수 있는 모든 쌍을 검사한다. 심각한 순으로 정렬."""
        unique = list(dict.fromkeys(ingredients))  # 입력 순서 유지 + 중복 제거
        found = [
            hit for a, b in combinations(unique, 2) if (hit := self.get(a, b)) is not None
        ]
        return sorted(found, key=lambda i: -i.severity)

    def all(self) -> list[Interaction]:
        return list(self._by_pair.values())
