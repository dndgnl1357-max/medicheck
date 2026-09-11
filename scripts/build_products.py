"""e약은요 원본 → 제품 검색용 테이블.

시민은 성분명을 모른다. "아세트아미노펜"이 아니라 "타이레놀"을 안다.
이 스크립트는 e약은요(4,780품목)에서 제품명·성분·이미지를 뽑아
data/processed/products.csv 를 만든다.

    python scripts/fetch_dur_api.py --preset easydrug
    python scripts/build_products.py

## 성분을 어디서 얻는가

e약은요에는 성분 필드가 없다. 대신 제품명 끝에 괄호로 들어 있는 경우가 많다.

    어린이타이레놀산160밀리그램(아세트아미노펜)  →  아세트아미노펜
    페니라민정(클로르페니라민말레산염)            →  클로르페니라민말레산염

4,780건 중 2,218건이 이 형태다. 나머지는 성분 없이 제품 정보만 남는다.
성분을 못 뽑은 제품은 검색은 되지만 상호작용 분석에는 쓸 수 없다 — 그 사실이
응답에 드러나야 하므로 빈 값으로 남기고 숨기지 않는다.

## 검색용 이름

사용자는 "타이레놀"이라고 친다. "어린이타이레놀산160밀리그램(아세트아미노펜)"이
아니다. 그래서 괄호·용량·제형 표기를 떼어낸 `search_name` 을 따로 저장하고,
검색은 부분 일치로 한다.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_INPUT = Path("data/interim/easydrug.csv")
DEFAULT_OUTPUT = Path("data/processed/products.csv")

OUTPUT_COLUMNS = [
    "item_seq",
    "product_name",
    "search_name",
    "ingredients",
    "maker",
    "image",
    "interaction_text",
]

# 제품명 맨 끝의 괄호 묶음. 중첩 괄호("글루타티온(환원형)")까지 잡으려고 탐욕적으로 연다.
_TRAILING_PAREN = re.compile(r"\(([^(]*(?:\([^)]*\)[^(]*)*)\)\s*$")

# 성분이 아닌 괄호 내용. 수출명·판매명 표기가 섞여 들어온다.
_NOT_INGREDIENT = ("수출", "판매명", "제품명", "구:", "旧")

# search_name 에서 떼어낼 용량·제형 표기
_DOSE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:밀리그램|마이크로그램|그램|밀리리터|리터|mg|mcg|g|ml|l|iu|%)",
    re.IGNORECASE,
)
_FORM = re.compile(
    r"(정|캡슐|연질캡슐|경질캡슐|산|과립|시럽|현탁액|액|주사액|주|점안액|점이액|"
    r"연고|크림|겔|로션|패치|좌제|분말|환|엑스|건조시럽|내용액제)+\s*$"
)


def extract_ingredients(product_name: str) -> str:
    """제품명 끝 괄호에서 성분을 뽑는다. 못 뽑으면 빈 문자열."""
    match = _TRAILING_PAREN.search(product_name.strip())
    if not match:
        return ""
    inner = match.group(1).strip()
    if not inner or ":" in inner:
        return ""
    if any(token in inner for token in _NOT_INGREDIENT):
        return ""
    # 중첩 괄호는 부가 설명이라 떼어낸다: "글루타티온(환원형)" → "글루타티온"
    inner = re.sub(r"\([^)]*\)", "", inner).strip()

    parts = [p.strip() for p in re.split(r"[,·/]", inner) if p.strip()]
    # 한 글자짜리나 지나치게 긴 것은 성분명이 아니다
    parts = [p for p in parts if 2 <= len(p) <= 40]
    return "|".join(parts)


def to_search_name(product_name: str) -> str:
    """사용자가 실제로 칠 법한 형태로 줄인다."""
    name = _TRAILING_PAREN.sub("", product_name.strip()).strip()
    name = re.sub(r"\([^)]*\)", "", name).strip()  # 남은 괄호도 제거
    name = _DOSE.sub("", name).strip()
    name = _FORM.sub("", name).strip()
    return name or product_name.strip()


def build(input_path: Path, output_path: Path) -> pd.DataFrame:
    df = pd.read_csv(input_path, dtype=str).fillna("")
    print(f"원본 {len(df):,}건")

    rows = []
    for r in df.itertuples(index=False):
        name = str(r.itemName).strip()
        if not name:
            continue
        rows.append(
            {
                "item_seq": str(r.itemSeq).strip(),
                "product_name": name,
                "search_name": to_search_name(name),
                "ingredients": extract_ingredients(name),
                "maker": str(r.entpName).strip(),
                "image": str(r.itemImage).strip(),
                # 시민 언어로 쓰인 상호작용 안내. 우리 데이터에 쌍이 없어도
                # 제품 화면에서 보여줄 수 있는 유일한 근거다.
                "interaction_text": " ".join(str(r.intrcQesitm).split()),
            }
        )

    out = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    out = out.drop_duplicates(subset=["item_seq"])

    with_ingr = (out.ingredients != "").sum()
    print(f"성분 추출: {with_ingr:,}건 / 못 뽑음: {len(out) - with_ingr:,}건")
    print(f"이미지 있음: {(out.image != '').sum():,}건")
    print(f"상호작용 텍스트 있음: {(out.interaction_text != '').sum():,}건")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)
    print(f"\n저장 완료: {output_path} ({len(out):,}건)")

    print("\n검색 이름 예시:")
    for r in out[out.ingredients != ""].head(6).itertuples(index=False):
        print(f"    {r.search_name:<24} ← {r.product_name[:40]}")
        print(f"    {'':24}   성분: {r.ingredients}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if not args.input.exists():
        print(f"입력 파일이 없습니다: {args.input}")
        print("  scripts/fetch_dur_api.py --preset easydrug 를 먼저 실행하세요.")
        return 1

    build(args.input, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
