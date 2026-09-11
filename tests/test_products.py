"""제품명 검색·확장 테스트.

핵심은 "애매하면 펼치지 않는다" 이다. 아로나민골드와 아로나민실버는 성분이
다르므로, "아로나민" 만 보고 아무거나 고르면 없는 위험을 경고하거나
있는 위험을 놓친다.
"""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.normalize import IngredientIndex
from app.core.products import Product, ProductIndex
from app.core.repository import Interaction, InteractionRepository
from app.core.service import AnalysisService
from app.main import app

COLUMNS = [
    "item_seq",
    "product_name",
    "search_name",
    "ingredients",
    "maker",
    "image",
    "interaction_text",
]


def write_products(path, rows):
    df = pd.DataFrame(rows)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df[COLUMNS].to_csv(path, index=False, encoding="utf-8")
    return path


@pytest.fixture
def index(tmp_path):
    """타이레놀(단일) / 아로나민(성분 갈림) / 겔포스(성분 없음)"""
    path = write_products(
        tmp_path / "products.csv",
        [
            {
                "item_seq": "1",
                "product_name": "타이레놀정500밀리그램(아세트아미노펜)",
                "search_name": "타이레놀",
                "ingredients": "아세트아미노펜",
            },
            {
                "item_seq": "2",
                "product_name": "어린이타이레놀산160밀리그램(아세트아미노펜)",
                "search_name": "어린이타이레놀",
                "ingredients": "아세트아미노펜",
            },
            {
                "item_seq": "3",
                "product_name": "아로나민골드정(푸르설티아민)",
                "search_name": "아로나민골드",
                "ingredients": "푸르설티아민",
            },
            {
                "item_seq": "4",
                "product_name": "아로나민실버정(벤포티아민)",
                "search_name": "아로나민실버",
                "ingredients": "벤포티아민",
            },
            {
                "item_seq": "5",
                "product_name": "겔포스현탁액",
                "search_name": "겔포스",
                "ingredients": "",
            },
            {
                "item_seq": "6",
                "product_name": "종합감기약(아세트아미노펜|슈도에페드린)",
                "search_name": "종합감기약",
                "ingredients": "아세트아미노펜|슈도에페드린",
            },
        ],
    )
    return ProductIndex.from_csv(path)


# --- 확장 --------------------------------------------------------------------


def test_이름이_같고_성분이_같으면_펼친다(index):
    """타이레놀 품목이 여럿이어도 성분이 같으면 펼쳐도 안전하다."""
    product, _ = index.expand("타이레놀")
    assert product is not None
    assert product.ingredients == ["아세트아미노펜"]


def test_성분이_갈리면_펼치지_않는다(index):
    """아로나민골드(푸르설티아민)와 실버(벤포티아민)는 다른 약이다."""
    product, candidates = index.expand("아로나민")
    assert product is None
    assert {c.search_name for c in candidates} == {"아로나민골드", "아로나민실버"}


def test_성분을_모르는_제품은_펼치지_않는다(index):
    """이름은 찾았지만 성분이 없으면 분석에 쓸 수 없다. 후보로만 돌려준다."""
    product, candidates = index.expand("겔포스")
    assert product is None
    assert [c.search_name for c in candidates] == ["겔포스"]


def test_복합제는_성분을_모두_펼친다(index):
    """성분 하나로 줄이면 나머지 성분의 상호작용을 통째로 놓친다."""
    product, _ = index.expand("종합감기약")
    assert product.ingredients == ["아세트아미노펜", "슈도에페드린"]


def test_너무_짧은_입력은_확장하지_않는다(index):
    assert index.expand("타")[0] is None
    assert index.expand("")[0] is None


def test_없는_제품(index):
    assert index.expand("듣도보도못한약")[0] is None


# --- 검색 --------------------------------------------------------------------


def test_부분_일치로_검색된다(index):
    names = [p.search_name for p in index.search("타이레놀")]
    assert "타이레놀" in names
    assert "어린이타이레놀" in names


def test_검색은_짧은_이름부터_보여준다(index):
    assert index.search("타이레놀")[0].search_name == "타이레놀"


def test_검색_결과는_중복되지_않는다(index):
    hits = index.search("아로나민")
    assert len({p.search_name for p in hits}) == len(hits)


def test_제품_파일이_없어도_빈_색인이_나온다(tmp_path):
    """제품 검색만 안 될 뿐 서버는 떠야 한다."""
    idx = ProductIndex.from_csv(tmp_path / "없는파일.csv")
    assert len(idx) == 0
    assert idx.expand("타이레놀") == (None, [])


# --- 분석 서비스와의 연결 -------------------------------------------------------


@pytest.fixture
def service(index, tmp_path):
    syn = tmp_path / "syn.csv"
    syn.write_text(
        "alias,canonical\n아세트아미노펜,아세트아미노펜\n와파린,와파린\n", encoding="utf-8"
    )
    repo = InteractionRepository(
        [Interaction("아세트아미노펜", "와파린", 3, "출혈 위험", source="테스트")]
    )
    return AnalysisService(IngredientIndex.from_csv(syn), repo, index)


def test_제품명으로_상호작용을_찾는다(service):
    result = service.analyze(["타이레놀", "와파린"])
    assert len(result.interactions) == 1
    assert result.overall_risk == 3


def test_어느_성분에서_왔는지_밝힌다(service):
    """사용자는 '타이레놀'을 쳤는데 '아세트아미노펜'이 나오면 혼란스럽다."""
    result = service.analyze(["타이레놀"])
    matched = result.matched[0]
    assert matched.canonical == "아세트아미노펜"
    assert matched.via_product == "타이레놀정500밀리그램(아세트아미노펜)"


def test_애매한_제품은_응답에_드러난다(service):
    """조용히 넘기면 사용자는 자기 약이 반영된 줄 안다."""
    result = service.analyze(["아로나민"])
    assert "아로나민" in result.ambiguous_products
    assert "아로나민" in result.unmatched


def test_성분명_입력은_제품_확장을_거치지_않는다(service):
    """성분명이 우연히 제품명과 겹칠 때 성분 쪽이 이겨야 한다."""
    result = service.analyze(["와파린"])
    assert result.matched[0].via_product is None
    assert result.matched[0].method == "exact"


# --- 엔드포인트 ----------------------------------------------------------------


client = TestClient(app)


def test_제품_검색_엔드포인트():
    r = client.get("/api/products?q=타이레놀")
    assert r.status_code == 200
    body = r.json()
    assert body["results"]
    assert any("타이레놀" in p["name"] for p in body["results"])
    # 성분을 못 뽑은 제품도 숨기지 않고 analyzable 로 표시한다
    assert all("analyzable" in p for p in body["results"])


def test_한_글자_검색은_422():
    assert client.get("/api/products?q=타").status_code == 422
