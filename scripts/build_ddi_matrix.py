"""공공데이터 원본 → 표준 DDI 매트릭스 변환.

data/raw/ 에 받아둔 심평원 병용금기 파일(xlsx/csv)을 읽어
data/processed/interactions.csv 로 정규화한다.

사용법:
    python scripts/build_ddi_matrix.py --input data/raw/병용금기.xlsx
    python scripts/build_ddi_matrix.py --input ... --col-a 성분명 --col-b 병용금기성분명

공공데이터 파일마다 컬럼명이 조금씩 달라서, 키워드로 자동 추정하되
못 찾으면 --col-* 옵션으로 직접 지정하게 되어 있다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.normalize import normalize  # noqa: E402

OUTPUT_COLUMNS = [
    "ingredient_a",
    "ingredient_b",
    "severity",
    "mechanism",
    "advice",
    "min_interval_hours",
    "source",
]

# 컬럼 자동 추정용 키워드 (우선순위 순)
#
# 지금까지 확인한 두 소스의 컬럼 표기가 서로 다르다.
#   한국의약품안전관리원 병용금기약물(CSV) : 성분명1 / 성분명2
#   식약처 DUR성분정보 오픈API            : DUR성분(INGR_KOR_NAME) / 관계성분(MIXTURE_...)
# 둘 다 자동으로 잡히도록 힌트를 넓혔다. 새 소스가 늘면 여기만 고치면 된다.
COLUMN_HINTS = {
    "a": [
        "주성분",
        "DUR성분",
        "성분명1",
        "성분1",
        "INGR_KOR_NAME",
        "성분명",
        "성분",
        "ingredient",
    ],
    "b": [
        "병용금기성분",
        "관계성분",
        "상대성분",
        "성분명2",
        "성분2",
        "MIXTURE_INGR",
        "MIXTURE",
        "interacting",
        "병용금기",
    ],
    "reason": [
        "PROHBT_CONTENT",
        "금기내용",
        "상세정보",
        "금기사유",
        "사유",
        "reason",
        "description",
    ],
}

# 힌트에 걸려도 성분명이 아닌 컬럼들.
#   - 식별자: INGR_CODE 가 INGR_KOR_NAME 보다 앞에 오는 파일이 있다.
#   - 영문명: MIXTURE_INGR_ENG_NAME 이 MIXTURE_INGR_KOR_NAME 보다 앞에 온다.
#     이걸 안 막으면 '한글 성분 × 영문 성분' 쌍이 만들어지고, 정규화가
#     둘을 못 잇는다. (실제로 첫 변환에서 그렇게 나왔다.)
_SKIP_TOKENS = ("코드", "code", "번호", "seq", "_no", "_id", "_eng", "영문")

# 삭제(철회)된 고시를 걸러내기 위한 컬럼과 '살아 있음'을 뜻하는 값.
# DUR API 는 DEL_YN 에 '정상' / '삭제' 를 담아 보낸다. 철회된 금기를 그대로
# 실으면 이미 사라진 위험을 경고하게 되므로 반드시 버린다.
_STATUS_COLUMNS = ("DEL_YN", "삭제여부", "사용여부")
_ALIVE_VALUES = {"정상", "N", "n", "사용", "Y", "y"}

# 병용금기 고시 데이터는 정의상 "함께 쓰면 안 되는" 조합이므로 최고 등급으로 둔다.
DEFAULT_SEVERITY = 5


def _should_skip(col: str, hint: str) -> bool:
    """성분명이 아닌 컬럼인가. 힌트가 대놓고 그걸 노리는 경우는 예외로 둔다."""
    low = str(col).lower()
    if any(token in hint.lower() for token in _SKIP_TOKENS):
        return False
    return any(token in low for token in _SKIP_TOKENS)


def guess_column(df: pd.DataFrame, kind: str, taken: set[str] | None = None) -> str | None:
    """키워드가 들어간 컬럼명을 찾는다. 'b'는 'a'보다 먼저 매칭돼야 하므로 주의."""
    taken = taken or set()
    for hint in COLUMN_HINTS[kind]:
        for col in df.columns:
            if col in taken or _should_skip(col, hint):
                continue
            if hint in str(col):
                return col
    return None


def drop_revoked(df: pd.DataFrame) -> pd.DataFrame:
    """철회된 고시를 버린다.

    DUR API 의 DEL_YN 이 '삭제' 인 행은 더 이상 유효한 병용금기가 아니다.
    남겨두면 이미 해제된 조합을 위험하다고 경고하게 된다 — 틀린 경고는
    사용자가 진짜 경고까지 무시하게 만든다.
    """
    for col in _STATUS_COLUMNS:
        if col not in df.columns:
            continue
        alive = df[col].fillna("").map(str).str.strip().isin(_ALIVE_VALUES)
        dropped = len(df) - int(alive.sum())
        if dropped:
            print(f"{col} 기준 철회 항목 {dropped:,}행 제외")
        return df[alive]
    return df


def read_any(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str)
    # 공공데이터 CSV는 EUC-KR(cp949)로 내려오는 경우가 많다.
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return pd.read_csv(path, dtype=str, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path.name} 의 인코딩을 판별하지 못했습니다 (utf-8/cp949 실패)")


def build(
    input_path: Path,
    output_path: Path,
    col_a: str | None,
    col_b: str | None,
    col_reason: str | None,
    severity: int,
    source_label: str,
) -> pd.DataFrame:
    df = read_any(input_path)
    print(f"원본 {len(df):,}행 / 컬럼: {list(df.columns)}")
    df = drop_revoked(df)

    # 'b' 를 먼저 찾고, 'a' 는 그 컬럼을 제외하고 찾는다.
    # 그러지 않으면 '병용금기성분명'이 'a' 의 '성분명' 힌트에 먼저 걸린다.
    col_b = col_b or guess_column(df, "b")
    col_a = col_a or guess_column(df, "a", taken={col_b} if col_b else set())
    col_reason = col_reason or guess_column(df, "reason")

    if not col_a or not col_b:
        raise SystemExit(
            "성분 컬럼을 자동으로 찾지 못했습니다.\n"
            f"  추정 결과: a={col_a!r}, b={col_b!r}\n"
            f"  사용 가능한 컬럼: {list(df.columns)}\n"
            "  --col-a / --col-b 옵션으로 직접 지정하세요."
        )
    if col_a == col_b:
        raise SystemExit(f"col-a 와 col-b 가 같은 컬럼({col_a!r})으로 잡혔습니다.")

    print(f"사용 컬럼: a={col_a!r}, b={col_b!r}, reason={col_reason!r}")

    out = pd.DataFrame(
        {
            "ingredient_a": df[col_a].fillna("").map(str).str.strip(),
            "ingredient_b": df[col_b].fillna("").map(str).str.strip(),
            # 금기내용에 리터럴 "\n" 이 섞여 들어온다 (XML→JSON 변환 잔재)
            "mechanism": (
                df[col_reason]
                .fillna("")
                .map(str)
                .str.replace(r"\s*\\n\s*", " ", regex=True)
                .str.strip()
                if col_reason
                else ""
            ),
        }
    )

    out = out[(out.ingredient_a != "") & (out.ingredient_b != "")]
    # 정규화 결과가 같으면 같은 성분이므로 쌍으로서 의미가 없다
    out = out[out.ingredient_a.map(normalize) != out.ingredient_b.map(normalize)]

    # (a,b)와 (b,a)를 같은 행으로 취급하기 위해 정렬해 저장
    swap = out.ingredient_a > out.ingredient_b
    out.loc[swap, ["ingredient_a", "ingredient_b"]] = out.loc[
        swap, ["ingredient_b", "ingredient_a"]
    ].values

    out["severity"] = severity
    out["advice"] = ""
    out["min_interval_hours"] = ""
    out["source"] = source_label

    before = len(out)
    out = out.drop_duplicates(subset=["ingredient_a", "ingredient_b"])
    print(f"중복 제거: {before:,} → {len(out):,}행")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")
    print(f"저장 완료: {output_path} ({len(out):,}행)")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="원본 xlsx/csv 경로")
    parser.add_argument(
        "--output", type=Path, default=Path("data/processed/interactions.csv")
    )
    parser.add_argument("--col-a", help="성분 A 컬럼명 (미지정 시 자동 추정)")
    parser.add_argument("--col-b", help="성분 B 컬럼명 (미지정 시 자동 추정)")
    parser.add_argument("--col-reason", help="금기사유 컬럼명")
    parser.add_argument("--severity", type=int, default=DEFAULT_SEVERITY, choices=range(1, 6))
    parser.add_argument("--source", default="심평원 DUR 병용금기", help="출처 표기")
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"입력 파일이 없습니다: {args.input}")

    build(
        args.input,
        args.output,
        args.col_a,
        args.col_b,
        args.col_reason,
        args.severity,
        args.source,
    )


if __name__ == "__main__":
    main()
