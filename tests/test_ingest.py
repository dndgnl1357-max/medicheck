"""공공데이터 수집·변환 스크립트 테스트.

네트워크도 키도 없이 확인할 수 있는 부분만 다룬다.
  - 응답을 어떻게 해석하는가 (extract_items, 에러 안내)
  - 컬럼 이름을 어떻게 알아보는가 (guess_column)

실제 API 가 기대한 모양으로 답하는지는 키가 있어야 알 수 있고,
그건 `scripts/fetch_dur_api.py --max-pages 1` 이 담당한다.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.build_ddi_matrix import build, drop_revoked, guess_column  # noqa: E402
from scripts.fetch_dur_api import _explain_xml_error, extract_items, write_csv  # noqa: E402

# --- 컬럼 자동 추정 -----------------------------------------------------------

# 실제로 마주칠 두 소스의 컬럼 표기. 새 소스를 만나면 여기에 한 줄 추가한다.
SOURCES = {
    "의약품안전관리원_병용금기약물": (
        ["연번", "제품코드", "성분명1", "성분명2", "금기사유", "고시일자"],
        "성분명1",
        "성분명2",
    ),
    # 실제 API 응답의 컬럼 순서 그대로. 영문명이 한글명보다 앞에 온다는 게 핵심이다.
    "식약처_DUR성분정보_API": (
        [
            "TYPE_NAME", "MIX_TYPE", "INGR_CODE", "INGR_ENG_NAME", "INGR_KOR_NAME",
            "MIX", "ORI", "CLASS", "MIXTURE_MIX_TYPE", "MIXTURE_INGR_CODE",
            "MIXTURE_INGR_ENG_NAME", "MIXTURE_INGR_KOR_NAME", "MIXTURE_MIX",
            "MIXTURE_ORI", "MIXTURE_CLASS", "NOTIFICATION_DATE", "PROHBT_CONTENT",
            "REMARK", "DEL_YN",
        ],
        "INGR_KOR_NAME",
        "MIXTURE_INGR_KOR_NAME",
    ),
    "심평원_병용금기_xlsx": (
        ["주성분코드", "주성분명", "병용금기성분명", "상세정보"],
        "주성분명",
        "병용금기성분명",
    ),
}


@pytest.mark.parametrize("name", list(SOURCES))
def test_소스별로_성분_컬럼을_알아본다(name):
    columns, expect_a, expect_b = SOURCES[name]
    df = pd.DataFrame(columns=columns)

    col_b = guess_column(df, "b")
    col_a = guess_column(df, "a", taken={col_b})

    assert col_b == expect_b
    assert col_a == expect_a


def test_영문명_컬럼을_고르지_않는다():
    """ENG_NAME 이 KOR_NAME 보다 앞에 있다. 영문을 고르면 한글 x 영문 쌍이 된다."""
    df = pd.DataFrame(columns=["MIXTURE_INGR_ENG_NAME", "MIXTURE_INGR_KOR_NAME"])
    assert guess_column(df, "b") == "MIXTURE_INGR_KOR_NAME"


def test_철회된_고시는_버린다():
    df = pd.DataFrame(
        {
            "성분명1": ["가", "나", "다"],
            "성분명2": ["A", "B", "C"],
            "DEL_YN": ["정상", "삭제", "정상"],
        }
    )
    assert len(drop_revoked(df)) == 2


def test_상태_컬럼이_없으면_그대로_둔다():
    df = pd.DataFrame({"성분명1": ["가"], "성분명2": ["A"]})
    assert len(drop_revoked(df)) == 1


def test_코드_컬럼을_성분명으로_착각하지_않는다():
    """INGR_CODE 가 INGR_KOR_NAME 보다 앞에 있어도 이름 쪽을 골라야 한다."""
    df = pd.DataFrame(columns=["INGR_CODE", "INGR_KOR_NAME"])
    assert guess_column(df, "a") == "INGR_KOR_NAME"


def test_같은_컬럼을_a와_b로_동시에_고르지_않는다():
    df = pd.DataFrame(columns=["성분명", "병용금기성분명"])
    col_b = guess_column(df, "b")
    col_a = guess_column(df, "a", taken={col_b})
    assert col_a != col_b


def test_알_수_없는_컬럼이면_None():
    df = pd.DataFrame(columns=["foo", "bar"])
    assert guess_column(df, "a") is None


# --- 변환 --------------------------------------------------------------------


def test_변환은_쌍을_정렬하고_중복을_없앤다(tmp_path):
    src = tmp_path / "raw.csv"
    pd.DataFrame(
        {
            # 같은 쌍이 순서만 바뀌어 두 번, 자기 자신과의 쌍 하나, 빈 행 하나
            "성분명1": ["아스피린", "와파린", "와파린", ""],
            "성분명2": ["와파린", "아스피린", "와파린나트륨", "칼슘"],
            "금기사유": ["출혈 위험", "출혈 위험", "동일 성분", ""],
        }
    ).to_csv(src, index=False, encoding="utf-8")

    out = build(src, tmp_path / "out.csv", None, None, None, 5, "테스트")

    # (와파린, 아스피린) 한 쌍만 남는다.
    # 와파린 × 와파린나트륨 은 정규화하면 같은 성분이라 쌍이 아니다.
    assert len(out) == 1
    row = out.iloc[0]
    assert {row.ingredient_a, row.ingredient_b} == {"아스피린", "와파린"}
    assert row.severity == 5
    assert row.source == "테스트"
    assert row.mechanism == "출혈 위험"


def test_변환_결과는_repository가_읽을_수_있다(tmp_path):
    """스키마가 어긋나면 서버가 뜬 다음에야 알게 된다. 여기서 잡는다."""
    from app.core.repository import InteractionRepository

    src = tmp_path / "raw.csv"
    pd.DataFrame(
        {"성분명1": ["와파린"], "성분명2": ["아스피린"], "금기사유": ["출혈"]}
    ).to_csv(src, index=False, encoding="utf-8")

    out_path = tmp_path / "interactions.csv"
    build(src, out_path, None, None, None, 5, "테스트")

    repo = InteractionRepository.from_csv(out_path)
    hit = repo.get("와파린", "아스피린")
    assert hit is not None
    assert hit.severity == 5


# --- API 응답 해석 ------------------------------------------------------------


def test_items가_리스트로_올_때():
    body = {"items": [{"a": 1}, {"a": 2}]}
    assert len(extract_items(body)) == 2


def test_items가_dict_하나로_올_때():
    """결과가 1건이면 리스트가 아니라 dict 로 온다. 흔한 함정이다."""
    assert extract_items({"items": {"item": {"a": 1}}}) == [{"a": 1}]
    assert extract_items({"items": {"a": 1}}) == [{"a": 1}]


def test_items가_비었을_때():
    assert extract_items({"items": None}) == []
    assert extract_items({}) == []


@pytest.mark.parametrize(
    "token, keyword",
    [
        ("SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "디코딩"),
        ("LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR", "트래픽"),
        ("SERVICE_ACCESS_DENIED_ERROR", "권한"),
    ],
)
def test_포털_에러를_사람_말로_바꾼다(token, keyword):
    xml = f"<OpenAPI_ServiceResponse><returnReasonCode>{token}</returnReasonCode></OpenAPI_ServiceResponse>"
    assert keyword in _explain_xml_error(xml)


def test_모르는_에러도_원문을_보여준다():
    assert "이상한" in _explain_xml_error("<error>이상한 응답</error>")


def test_페이지마다_필드가_달라도_csv가_깨지지_않는다(tmp_path):
    """뒤 페이지에만 있는 컬럼이 누락되면 조용히 데이터를 잃는다."""
    rows = [{"a": 1, "b": 2}, {"a": 3, "c": 4}]
    out = tmp_path / "out.csv"
    write_csv(rows, out)

    df = pd.read_csv(out)
    assert list(df.columns) == ["a", "b", "c"]
    assert len(df) == 2


def test_item으로_한_겹_감싸인_응답을_벗긴다():
    """실제 DUR API 는 items: [{"item": {...}}] 로 온다.

    벗기지 않으면 컬럼이 'item' 하나뿐인 CSV 가 나온다 (실제로 그렇게 나왔다).
    """
    body = {"items": [{"item": {"INGR_KOR_NAME": "이트라코나졸"}}]}
    assert extract_items(body) == [{"INGR_KOR_NAME": "이트라코나졸"}]


def test_감싸이지_않은_응답도_그대로_읽는다():
    """API 가 껍데기를 없애도 깨지지 않아야 한다."""
    body = {"items": [{"INGR_KOR_NAME": "이트라코나졸"}]}
    assert extract_items(body) == [{"INGR_KOR_NAME": "이트라코나졸"}]


def test_item이라는_이름의_실제_필드는_벗기지_않는다():
    """키가 item 하나뿐이어도 값이 dict 가 아니면 데이터 자체다."""
    assert extract_items({"items": [{"item": "문자열"}]}) == [{"item": "문자열"}]
