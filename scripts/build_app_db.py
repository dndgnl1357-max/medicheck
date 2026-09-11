"""앱에 내장할 SQLite DB 생성.

오프라인 우선 구조에서 앱은 서버 없이 이 DB 하나로 성분 매칭과 상호작용 조회를
전부 처리한다. Python 쪽 CSV(정제 데이터 또는 시드)를 읽어 앱이 바로 쓸 수 있는
형태로 굽는다.

사용법:
    python scripts/build_app_db.py
    python scripts/build_app_db.py --output ..\\medicheck_app\\assets\\db\\medicheck.db

핵심 설계:
  - alias_norm 을 **미리 계산해서** 저장한다. 앱(Dart)은 입력만 정규화하면 되고
    염 접미사 처리 같은 복잡한 로직을 성분 사전 전체에 다시 돌릴 필요가 없다.
  - 상호작용의 성분 쌍은 항상 (작은 id, 큰 id) 순으로 저장한다. 순서에 상관없이
    한 번의 조회로 찾기 위함.
  - normalize() 는 Dart 로 포팅해야 하는 유일한 로직이라, 두 구현이 어긋나지
    않도록 테스트 픽스처(JSON)를 함께 뽑는다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.normalize import normalize  # noqa: E402
from app.core.repository import InteractionRepository  # noqa: E402

SCHEMA_VERSION = 1

SCHEMA = """
DROP TABLE IF EXISTS meta;
DROP TABLE IF EXISTS interactions;
DROP TABLE IF EXISTS aliases;
DROP TABLE IF EXISTS ingredients;

CREATE TABLE ingredients (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,   -- 표준 성분명 (화면에 보여줄 이름)
    name_norm TEXT NOT NULL
);

CREATE TABLE aliases (
    alias         TEXT NOT NULL,       -- 원래 표기 (자동완성 등에 쓸 수 있음)
    alias_norm    TEXT NOT NULL,       -- 미리 정규화해 둔 조회 키
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id)
);
CREATE INDEX idx_aliases_norm ON aliases(alias_norm);

CREATE TABLE interactions (
    ingredient_a_id    INTEGER NOT NULL REFERENCES ingredients(id),
    ingredient_b_id    INTEGER NOT NULL REFERENCES ingredients(id),
    severity           INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 5),
    mechanism          TEXT NOT NULL DEFAULT '',
    advice             TEXT NOT NULL DEFAULT '',
    min_interval_hours REAL,
    source             TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (ingredient_a_id, ingredient_b_id)
);

-- key/value 로 둔 이유: 앱이 DB를 열자마자 스키마 버전과 시드 여부를 확인해
-- "개발용 데이터입니다" 경고를 띄울지 결정한다.
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def load_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str).fillna("")


def build(output: Path, data_dir: Path) -> None:
    seed_dir = data_dir / "seed"

    # 서버와 **같은** 적재 규칙을 쓴다. 여기서 CSV 를 따로 고르면 병합 규칙이
    # 두 벌이 되고, 실제로 그렇게 두었더니 앱 DB 만 시드 병합을 놓쳐서
    # 영양제 상호작용이 통째로 빠졌다. 규칙은 repository 한 곳에만 둔다.
    repo = InteractionRepository.load(data_dir / "processed", seed_dir)
    is_seed = repo.is_seed
    if is_seed:
        print("경고: 정제 데이터가 없어 개발용 시드로 DB를 만듭니다.")
    print(f"상호작용 {len(repo):,}쌍 (시드 보조 {repo.supplementary_pairs}쌍)")

    synonyms = load_csv(seed_dir / "ingredient_synonyms.csv")
    interactions = pd.DataFrame(
        [
            {
                "ingredient_a": i.ingredient_a,
                "ingredient_b": i.ingredient_b,
                "severity": str(i.severity),
                "mechanism": i.mechanism,
                "advice": i.advice,
                "min_interval_hours": (
                    "" if i.min_interval_hours is None else str(i.min_interval_hours)
                ),
                "source": i.source,
            }
            for i in repo.all()
        ],
        columns=[
            "ingredient_a",
            "ingredient_b",
            "severity",
            "mechanism",
            "advice",
            "min_interval_hours",
            "source",
        ],
    )

    # --- 표준 성분 목록 확정 -------------------------------------------------
    # 동의어 사전의 canonical 과, 상호작용 표에 등장하는 이름을 합친다.
    names: list[str] = []
    for value in [
        *synonyms["canonical"],
        *interactions["ingredient_a"],
        *interactions["ingredient_b"],
    ]:
        value = value.strip()
        if value and value not in names:
            names.append(value)

    ingredient_id = {name: i + 1 for i, name in enumerate(names)}

    # 동의어 사전에 없는데 상호작용 표에만 있는 성분은 검색이 안 되므로 알려준다.
    known_canonicals = {c.strip() for c in synonyms["canonical"] if c.strip()}
    orphans = [n for n in names if n not in known_canonicals]
    if orphans:
        print(f"주의: 동의어 사전에 없는 성분 {len(orphans)}건 → {orphans}")
        print("      ingredient_synonyms.csv 에 추가해야 사용자가 검색할 수 있습니다.")

    # --- 별칭 테이블 ---------------------------------------------------------
    alias_rows: list[tuple[str, str, int]] = []
    seen_norms: dict[str, str] = {}  # alias_norm → canonical (충돌 감지용)

    def add_alias(alias: str, canonical: str) -> None:
        alias, canonical = alias.strip(), canonical.strip()
        if not alias or not canonical:
            return
        norm = normalize(alias)
        if not norm:
            return
        owner = seen_norms.get(norm)
        if owner is not None:
            if owner != canonical:
                # 같은 표기가 서로 다른 성분을 가리키면 조회가 갈린다. 먼저 등록된 쪽을 살린다.
                print(f"충돌: '{alias}'(정규화 '{norm}') → {owner} / {canonical}. 앞의 것을 사용합니다.")
            return
        seen_norms[norm] = canonical
        alias_rows.append((alias, norm, ingredient_id[canonical]))

    for name in names:  # 표준명 자기 자신도 검색 가능해야 한다
        add_alias(name, name)
    for alias, canonical in zip(synonyms["alias"], synonyms["canonical"]):
        if canonical.strip() in ingredient_id:
            add_alias(alias, canonical)

    # --- 상호작용 테이블 -----------------------------------------------------
    interaction_rows: dict[tuple[int, int], tuple] = {}
    skipped = 0
    for row in interactions.itertuples(index=False):
        a, b = row.ingredient_a.strip(), row.ingredient_b.strip()
        if not a or not b or a == b:
            skipped += 1
            continue
        a_id, b_id = ingredient_id[a], ingredient_id[b]
        if a_id > b_id:
            a_id, b_id = b_id, a_id
        severity = max(1, min(5, int(float(row.severity))))
        interval = getattr(row, "min_interval_hours", "").strip()

        existing = interaction_rows.get((a_id, b_id))
        if existing is not None and existing[2] >= severity:
            continue  # 같은 쌍이 중복되면 더 심각한 등급을 남긴다
        interaction_rows[(a_id, b_id)] = (
            a_id,
            b_id,
            severity,
            getattr(row, "mechanism", "").strip(),
            getattr(row, "advice", "").strip(),
            float(interval) if interval else None,
            getattr(row, "source", "").strip(),
        )
    if skipped:
        print(f"건너뜀: 성분이 비었거나 자기 자신인 행 {skipped}건")

    # --- 쓰기 ---------------------------------------------------------------
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    conn = sqlite3.connect(output)
    try:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO ingredients (id, name, name_norm) VALUES (?, ?, ?)",
            [(ingredient_id[n], n, normalize(n)) for n in names],
        )
        conn.executemany(
            "INSERT INTO aliases (alias, alias_norm, ingredient_id) VALUES (?, ?, ?)",
            alias_rows,
        )
        conn.executemany(
            "INSERT INTO interactions (ingredient_a_id, ingredient_b_id, severity,"
            " mechanism, advice, min_interval_hours, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            list(interaction_rows.values()),
        )
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [
                ("schema_version", str(SCHEMA_VERSION)),
                ("built_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
                ("is_seed", "1" if is_seed else "0"),
                (
                    "source",
                    "interactions_seed.csv"
                    if is_seed
                    else "interactions.csv + interactions_seed.csv",
                ),
                ("ingredient_count", str(len(names))),
                ("interaction_count", str(len(interaction_rows))),
            ],
        )
        conn.commit()
        # 앱에 넣을 파일이라 조각모음으로 크기를 줄인다
        conn.execute("VACUUM")
    finally:
        conn.close()

    size_kb = output.stat().st_size / 1024
    print(
        f"\n생성 완료: {output}\n"
        f"  성분 {len(names)}종 / 별칭 {len(alias_rows)}개 / 상호작용 {len(interaction_rows)}쌍\n"
        f"  크기 {size_kb:.1f} KB / 시드 데이터 여부: {is_seed}"
    )


def write_fixture(path: Path) -> None:
    """Dart 로 포팅할 normalize() 가 Python 과 같게 동작하는지 검증할 픽스처.

    두 구현이 조용히 어긋나면 매칭이 통째로 틀어지므로, 같은 입력·기대값을
    양쪽 테스트가 공유하게 한다.

    특히 Dart 에는 유니코드 NFKC 정규화가 내장돼 있지 않다. Python 이 공짜로
    처리하는 전각 문자와 ㎎·㎖ 같은 단위 기호(한국 약 포장에 흔하다)를 Dart 쪽에서
    직접 구현해야 하므로, 해당 케이스를 반드시 픽스처에 넣어둔다.
    """
    cases = [
        # 기본
        "와파린나트륨 5mg (정제)",
        "Warfarin  Sodium",
        "탄산수소나트륨",
        "탄산칼슘",
        "나트륨",
        "오메가-3",
        "Vitamin  K",
        "시프로플록사신염산염수화물",
        "st johns wort",
        "  아스피린  ",
        "!!!",
        "",
        # NFKC — Dart 가 놓치기 쉬운 것들
        "ＷＡＲＦＡＲＩＮ",  # 전각 알파벳
        "아스피린　１００",  # 전각 공백 + 전각 숫자
        "타이레놀 500㎎",  # 단위 기호 ㎎ → mg
        "비타민D 25㎍",  # ㎍ → μg
        "마그네슘 10㎖",  # ㎖ → ml
        "비타민K 1000IU",
    ]
    payload = [{"input": c, "expected": normalize(c)} for c in cases]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"정규화 픽스처: {path} ({len(payload)}건)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data" / "app" / "medicheck.db",
        help="생성할 SQLite 파일 경로",
    )
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=PROJECT_ROOT / "data" / "app" / "normalize_fixture.json",
    )
    args = parser.parse_args()

    build(args.output, args.data_dir)
    write_fixture(args.fixture)


if __name__ == "__main__":
    main()
