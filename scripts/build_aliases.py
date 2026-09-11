"""수집한 원본에서 성분 별칭을 자동 생성한다.

DUR 원본에는 성분마다 한글명과 영문명이 함께 들어 있다. 이걸 별칭으로 뽑아두면
사용자가 "warfarin" 이라고 쳐도 "와파린" 으로 찾힌다. API 를 더 호출하지 않고
이미 받아둔 파일만으로 되는 일이다.

    python scripts/build_aliases.py

## 왜 seed 파일에 직접 쓰지 않는가

`data/seed/ingredient_synonyms.csv` 는 손으로 관리하는 파일이다 (상품명, 흔한 오타,
구어체 표기처럼 기계가 만들 수 없는 것들이 들어간다). 생성물을 거기에 섞으면
다음에 다시 생성할 때 손으로 넣은 것과 구분이 안 된다. 그래서 생성물은
`data/processed/` 에 따로 두고, 색인이 두 파일을 함께 읽는다.

**충돌 시 손으로 쓴 쪽이 이긴다.** (`IngredientIndex.from_csvs` 의 인자 순서)
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.core.normalize import normalize  # noqa: E402

DEFAULT_INPUT = Path("data/interim/dur_usjnt_taboo.csv")
DEFAULT_OUTPUT = Path("data/processed/aliases_generated.csv")

# (한글명 컬럼, 영문명 컬럼) 쌍. DUR 원본은 한 행에 성분 두 개가 들어 있다.
NAME_COLUMN_PAIRS = [
    ("INGR_KOR_NAME", "INGR_ENG_NAME"),
    ("MIXTURE_INGR_KOR_NAME", "MIXTURE_INGR_ENG_NAME"),
    ("INGR_NAME", "INGR_ENG_NAME"),  # 병용금기 외 오퍼레이션의 컬럼명
]


def extract_pairs(df: pd.DataFrame) -> dict[str, str]:
    """{영문명 → 한글 표준명} 을 뽑는다."""
    aliases: dict[str, str] = {}
    for kor_col, eng_col in NAME_COLUMN_PAIRS:
        if kor_col not in df.columns or eng_col not in df.columns:
            continue
        for kor, eng in zip(df[kor_col], df[eng_col]):
            kor, eng = str(kor).strip(), str(eng).strip()
            if not kor or not eng or eng.lower() == "nan":
                continue
            # 정규화하면 같아지는 별칭은 색인에 넣어봐야 의미가 없다
            if normalize(eng) == normalize(kor):
                continue
            aliases.setdefault(eng, kor)
    return aliases


def build(input_path: Path, output_path: Path, seed_path: Path) -> int:
    df = pd.read_csv(input_path, dtype=str).fillna("")
    aliases = extract_pairs(df)
    print(f"원본 {len(df):,}행에서 별칭 {len(aliases):,}개 추출")

    # 손으로 쓴 사전에 이미 있는 별칭은 만들지 않는다. 중복이 아니라 출처가 갈리는 게 문제다.
    existing: set[str] = set()
    if seed_path.exists():
        seed = pd.read_csv(seed_path, dtype=str).fillna("")
        existing = {normalize(a) for a in seed["alias"]}
        before = len(aliases)
        aliases = {a: c for a, c in aliases.items() if normalize(a) not in existing}
        if before != len(aliases):
            print(f"  손으로 쓴 사전에 이미 있는 {before - len(aliases)}개 제외")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["alias", "canonical"])
        for alias, canonical in sorted(aliases.items()):
            writer.writerow([alias, canonical])

    print(f"저장 완료: {output_path} ({len(aliases):,}개)")
    print("  예시:")
    for alias, canonical in sorted(aliases.items())[:5]:
        print(f"      {alias} → {canonical}")
    return len(aliases)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--seed", type=Path, default=Path("data/seed/ingredient_synonyms.csv")
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"입력 파일이 없습니다: {args.input}")
        print("  scripts/fetch_dur_api.py 를 먼저 실행하세요.")
        return 1

    build(args.input, args.output, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
