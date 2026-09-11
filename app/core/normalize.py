"""성분명 정규화 및 매칭.

공공데이터의 성분명은 표기가 제각각이다.
    "와파린나트륨", "Warfarin Sodium", "와르파린", "와파린(나트륨)"
사용자 입력은 더 심하다. 이 모듈은 이런 표기를 하나의 표준 성분명으로 모은다.

3단계로 매칭한다.
    1) exact   — 정규화 후 표준명과 그대로 일치
    2) synonym — 동의어 사전(별칭/영문명/오탈자)에 등록된 표기
    3) fuzzy   — difflib 유사도가 cutoff 이상인 후보 (오탈자 구제용)
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# 염(salt) 형태 접미사. 약효 성분이 같으므로 떼어내고 비교한다.
# 주의: 마그네슘·칼슘 등 그 자체가 성분인 미네랄명은 넣지 않는다.
_SALT_SUFFIXES_KO = (
    "염산염",
    "브롬화수소산염",
    "황산염",
    "인산염",
    "질산염",
    "말레산염",
    "타르타르산염",
    "구연산염",
    "시트르산염",
    "푸마르산염",
    "아세트산염",
    "숙신산염",
    "메실산염",
    "베실산염",
    "수화물",
    "무수물",
    "나트륨",
    "칼륨",
)

# 위 접미사로 끝나지만 그 자체가 하나의 성분인 이름. 절대 자르지 않는다.
# (자르면 "탄산수소나트륨" → "탄산수소" 처럼 엉뚱한 성분이 된다.)
_SALT_EXCEPTIONS = frozenset(
    {
        "탄산수소나트륨",
        "탄산나트륨",
        "염화나트륨",
        "염화칼륨",
        "구연산나트륨",
        "sodiumbicarbonate",
        "sodiumchloride",
        "potassiumchloride",
    }
)
_SALT_SUFFIXES_EN = (
    "hydrochloride",
    "hydrobromide",
    "sulfate",
    "sulphate",
    "phosphate",
    "nitrate",
    "maleate",
    "tartrate",
    "citrate",
    "fumarate",
    "acetate",
    "succinate",
    "mesylate",
    "besylate",
    "monohydrate",
    "dihydrate",
    "hydrate",
    "anhydrous",
    "sodium",
    "potassium",
)

# 괄호와 그 안의 내용, 용량 표기 제거용
_PAREN_RE = re.compile(r"[（(\[][^）)\]]*[）)\]]")
# NFKC 이후의 텍스트에 적용된다는 점이 중요하다.
#   ㎎ → "mg", ㎖ → "ml" 로 풀리므로 그 리터럴을 따로 넣을 필요가 없다.
#   반면 ㎍ 는 "μg"(그리스 문자 뮤)로 풀린다. 'ug' 만 넣어두면 여기에 걸리지 않아
#   "25μg" 가 살아남고, 뒤이어 μ 만 제거되면서 "25g" 라는 찌꺼기가 성분명에 붙는다.
# 긴 단위를 먼저 시도하도록 순서를 잡는다.
_DOSE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mcg|μg|µg|mg|ml|ug|cc|iu|g|정|캡슐|포|알)\b",
    re.IGNORECASE,
)
_NONWORD_RE = re.compile(r"[^0-9a-z가-힣]+")

# 정규화 결과가 이보다 짧아지면 접미사를 떼지 않는다 ("나트륨" 자체가 입력인 경우 등)
_MIN_STEM_LEN = 2

# fuzzy 매칭에서 살펴볼 후보 수와, 1·2위가 이 차이 안이면 애매하다고 보는 기준
_FUZZY_CANDIDATES = 5
_AMBIGUITY_MARGIN = 0.05


def normalize(name: str) -> str:
    """성분명을 비교 가능한 형태로 정규화한다.

    >>> normalize("와파린나트륨 5mg (정제)")
    '와파린'
    >>> normalize("Warfarin  Sodium")
    'warfarin'
    """
    if not name:
        return ""

    # 전각/호환 문자를 반각으로 (ＷＡＲＦＡＲＩＮ → WARFARIN)
    text = unicodedata.normalize("NFKC", name).lower()
    text = _PAREN_RE.sub(" ", text)
    text = _DOSE_RE.sub(" ", text)
    text = _NONWORD_RE.sub("", text)

    return _strip_salt(text)


def _strip_salt(text: str) -> str:
    """염 형태 접미사를 반복적으로 떼어낸다. '와파린나트륨수화물' → '와파린'"""
    changed = True
    while changed:
        if text in _SALT_EXCEPTIONS:
            break
        changed = False
        for suffix in (*_SALT_SUFFIXES_KO, *_SALT_SUFFIXES_EN):
            if text.endswith(suffix) and len(text) - len(suffix) >= _MIN_STEM_LEN:
                text = text[: -len(suffix)]
                changed = True
                break
    return text


@dataclass(frozen=True)
class Match:
    """매칭 결과 한 건."""

    input: str
    canonical: str | None
    confidence: float
    method: str  # exact | synonym | fuzzy | none

    @property
    def ok(self) -> bool:
        return self.canonical is not None


class IngredientIndex:
    """표준 성분명 + 동의어 사전을 담고, 임의의 입력을 표준명으로 해석한다."""

    def __init__(self, synonyms: dict[str, str], fuzzy_cutoff: float = 0.8) -> None:
        """
        Args:
            synonyms: {별칭 → 표준 성분명}. 표준명 자기 자신도 포함되어야 한다.
            fuzzy_cutoff: 유사도 매칭 임계값. 낮출수록 오탈자를 잘 잡지만 오매칭이 는다.
                한글 5글자 성분명에서 오타 1글자면 유사도가 0.8까지 떨어지므로
                0.85 이상으로 두면 흔한 오타를 놓친다. 대신 _AMBIGUITY_MARGIN 으로
                후보가 갈릴 때는 매칭을 포기해 오매칭을 막는다.
        """
        self.fuzzy_cutoff = fuzzy_cutoff
        self._lookup: dict[str, str] = {}
        self._canonicals: set[str] = set()

        for alias, canonical in synonyms.items():
            canonical = canonical.strip()
            if not canonical:
                continue
            self._canonicals.add(canonical)
            self._lookup.setdefault(normalize(alias), canonical)

        # 표준명 자체도 항상 찾을 수 있게 등록
        for canonical in self._canonicals:
            self._lookup.setdefault(normalize(canonical), canonical)

        self._keys = list(self._lookup)

    @classmethod
    def from_csv(
        cls,
        path: Path,
        fuzzy_cutoff: float = 0.85,
        extra_canonicals: Iterable[str] = (),
    ) -> IngredientIndex:
        """alias,canonical 두 컬럼짜리 CSV에서 사전을 읽는다.

        `extra_canonicals` 는 동의어 사전에는 없지만 상호작용 데이터에는
        등장하는 성분명이다. 이걸 넣지 않으면 데이터에 1,000쌍이 있어도
        사전에 있는 몇십 개 성분으로만 조회가 되고 나머지는 닿지 않는다.
        (실제로 DUR 데이터를 처음 넣었을 때 성분 478개 중 31개만 잡혔다.)

        별칭이 없으므로 이 성분들은 표기가 정확히 맞거나 유사도로만 잡힌다.
        자주 쓰이는 성분은 ingredient_synonyms.csv 에 별칭을 채워야 한다.
        """
        return cls.from_csvs([path], fuzzy_cutoff, extra_canonicals)

    @classmethod
    def from_csvs(
        cls,
        paths: Iterable[Path],
        fuzzy_cutoff: float = 0.85,
        extra_canonicals: Iterable[str] = (),
    ) -> IngredientIndex:
        """여러 별칭 파일을 합쳐 읽는다. **앞선 파일이 이긴다.**

        손으로 쓴 사전(seed)과 자동 생성물(processed)을 함께 쓰기 위한 것이다.
        같은 별칭이 양쪽에 있으면 손으로 쓴 쪽을 남긴다 — 사람이 일부러 넣은
        매핑을 기계가 덮어쓰면 고쳐도 다음 생성 때 되돌아간다.
        """
        pairs: dict[str, str] = {}
        for path in paths:
            if not Path(path).exists():
                continue
            df = pd.read_csv(path, dtype=str).fillna("")
            for alias, canonical in zip(df["alias"], df["canonical"]):
                pairs.setdefault(alias, canonical)
        for name in extra_canonicals:
            name = name.strip()
            if name:
                pairs.setdefault(name, name)
        return cls(pairs, fuzzy_cutoff=fuzzy_cutoff)

    @property
    def canonicals(self) -> set[str]:
        return set(self._canonicals)

    def resolve(self, raw: str) -> Match:
        """입력 문자열 하나를 표준 성분명으로 해석한다."""
        key = normalize(raw)
        if not key:
            return Match(raw, None, 0.0, "none")

        hit = self._lookup.get(key)
        if hit is not None:
            method = "exact" if normalize(hit) == key else "synonym"
            return Match(raw, hit, 1.0, method)

        return self._fuzzy(raw, key)

    def _fuzzy(self, raw: str, key: str) -> Match:
        """오탈자 구제. 단, 서로 다른 성분이 비슷하게 걸리면 매칭하지 않는다.

        약 이름은 한 글자 차이로 다른 약이 되는 경우가 많다. 애매한 상태에서
        아무거나 고르는 것보다 "모르겠다"고 답하고 사용자에게 되묻는 편이 안전하다.
        """
        candidates = difflib.get_close_matches(
            key, self._keys, n=_FUZZY_CANDIDATES, cutoff=self.fuzzy_cutoff
        )
        if not candidates:
            return Match(raw, None, 0.0, "none")

        scored = [
            (difflib.SequenceMatcher(None, key, c).ratio(), self._lookup[c])
            for c in candidates
        ]
        scored.sort(key=lambda s: -s[0])
        best_score, best_canonical = scored[0]

        for score, canonical in scored[1:]:
            if canonical != best_canonical and best_score - score < _AMBIGUITY_MARGIN:
                return Match(raw, None, 0.0, "none")

        return Match(raw, best_canonical, round(best_score, 3), "fuzzy")

    def resolve_many(self, raws: list[str]) -> list[Match]:
        return [self.resolve(r) for r in raws]
