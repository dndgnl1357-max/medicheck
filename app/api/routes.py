"""API 라우트."""

import random
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.config import get_settings
from app.api.deps import (
    get_explanation_service,
    get_index,
    get_photo_reader,
    get_products,
    get_repository,
    get_service,
)
from app.core.graph import build_graph, to_cytoscape
from app.core.normalize import IngredientIndex
from app.core.products import ProductIndex
from app.core.vision import PhotoReader
from app.core.repository import InteractionRepository
from app.core.service import AnalysisService, ExplanationService
from app.schemas import AnalyzeRequest, AnalyzeResponse, ExplainResponse

router = APIRouter()

ServiceDep = Annotated[AnalysisService, Depends(get_service)]
RepoDep = Annotated[InteractionRepository, Depends(get_repository)]
IndexDep = Annotated[IngredientIndex, Depends(get_index)]
ExplainDep = Annotated[ExplanationService, Depends(get_explanation_service)]
ProductDep = Annotated[ProductIndex, Depends(get_products)]
PhotoDep = Annotated[PhotoReader, Depends(get_photo_reader)]


@router.get("/health", tags=["system"])
def health(repo: RepoDep, index: IndexDep, explain: ExplainDep, photo: PhotoDep) -> dict:
    """서비스 상태와 현재 적재된 데이터 규모."""
    return {
        "status": "ok",
        "interaction_pairs": len(repo),
        "known_ingredients": len(index.canonicals),
        "using_seed_data": repo.is_seed,
        # 정제 데이터에 없어 시드에서 채운 쌍의 수 (주로 영양제·건기식)
        "supplementary_pairs": repo.supplementary_pairs,
        "llm_enabled": explain.explainer.enabled,
        "products": len(explain.analysis.products),
        "photo_enabled": photo.enabled,
    }


@router.post("/analyze", response_model=AnalyzeResponse, tags=["analysis"])
def analyze(payload: AnalyzeRequest, service: ServiceDep) -> AnalyzeResponse:
    """복용 중인 성분 목록의 상호작용 위험도를 분석한다."""
    return service.analyze(payload.ingredients)


@router.post("/explain", response_model=ExplainResponse, tags=["analysis"])
def explain(payload: AnalyzeRequest, service: ExplainDep) -> ExplainResponse:
    """분석 결과를 시민의 언어로 풀어 설명한다.

    앱은 분석까지 오프라인으로 처리하고 이 엔드포인트만 네트워크로 호출한다.
    OPENAI_API_KEY 가 없거나 호출이 실패하면 템플릿 설명이 나가며, 어느 쪽인지는
    응답의 `source` 로 알 수 있다. 설명 생성 실패로 이 엔드포인트가 5xx 를 내지는 않는다.
    """
    return service.explain(payload.ingredients)


@router.post("/photo", tags=["analysis"])
async def read_photo(reader: PhotoDep, image: Annotated[UploadFile, File()]) -> dict:
    """약봉투·처방전 사진에서 약 이름을 읽는다.

    읽어낸 이름은 그대로 돌려줄 뿐 분석하지 않는다. 화면이 사용자에게 확인받은 뒤
    /api/analyze 나 /api/explain 으로 보내는 흐름이다 — 잘못 읽은 약이 조용히
    분석에 들어가면 안 되기 때문이다.

    인식이 불가능하면 available=false 와 사유를 돌려준다. 빈 목록을 성공처럼
    돌려주지 않는다.
    """
    data = await image.read()
    result = reader.read(data, image.content_type or "")
    return {
        "available": result.available,
        "reason": result.reason,
        "note": result.note,
        "model": result.model,
        "items": [
            {"name": i.name, "kind": i.kind, "confidence": i.confidence}
            for i in result.items
        ],
    }


@router.get("/products", tags=["analysis"])
def search_products(
    products: ProductDep,
    q: Annotated[str, Query(min_length=2, description="제품명 일부")],
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> dict:
    """제품명으로 검색한다. 시민은 성분명이 아니라 제품명을 안다."""
    hits = products.search(q, limit=limit)
    return {
        "query": q,
        "results": [
            {
                "item_seq": p.item_seq,
                "name": p.name,
                "search_name": p.search_name,
                "ingredients": p.ingredients,
                "maker": p.maker,
                "image": p.image,
                # 성분을 못 뽑은 제품은 상호작용 분석에 쓸 수 없다. 숨기지 않는다.
                "analyzable": p.has_ingredients,
            }
            for p in hits
        ],
    }


@router.get("/config", tags=["system"])
def client_config() -> dict:
    """브라우저가 쓸 공개 설정.

    여기 담기는 값은 전부 공개돼도 되는 것이어야 한다. Supabase anon 키는
    그러라고 만든 키지만, service_role 키가 실수로 들어오면 그대로 유출된다.
    그래서 config 는 화이트리스트 방식으로만 넘긴다 — settings 를 통째로
    직렬화하지 않는다.
    """
    settings = get_settings()
    return {
        "auth": {
            "enabled": settings.auth_enabled,
            "url": settings.supabase_url if settings.auth_enabled else "",
            "anon_key": settings.supabase_anon_key if settings.auth_enabled else "",
        }
    }


@router.get("/examples", tags=["analysis"])
def examples(
    repo: RepoDep,
    count: Annotated[int, Query(ge=1, le=12)] = 4,
    min_severity: Annotated[int, Query(ge=1, le=5)] = 3,
) -> dict:
    """적재된 데이터에서 실제 상호작용 조합을 뽑아 준다.

    데모 화면의 예시 버튼용이다. 예시를 손으로 박아두면 네 개짜리 장식이 되지만,
    데이터에서 뽑으면 누를 때마다 진짜 조합이 나오고 적재 규모가 눈에 보인다.
    """
    pool = [i for i in repo.all() if i.severity >= min_severity]
    picked = random.sample(pool, min(count, len(pool)))
    return {
        "total_available": len(pool),
        "examples": [
            {
                "ingredients": [i.ingredient_a, i.ingredient_b],
                "severity": i.severity,
                "source": i.source,
            }
            for i in picked
        ],
    }


@router.get("/ingredients", tags=["analysis"])
def search_ingredients(
    index: IndexDep,
    q: Annotated[str, Query(min_length=1, description="검색어")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict:
    """자동완성용 성분명 검색."""
    match = index.resolve(q)
    suggestions = sorted(c for c in index.canonicals if q.lower() in c.lower())[:limit]
    return {
        "query": q,
        "best_match": match.canonical,
        "method": match.method,
        "suggestions": suggestions,
    }


@router.post("/graph", tags=["analysis"])
def interaction_graph(payload: AnalyzeRequest, service: ServiceDep) -> dict:
    """입력 성분들로 이루어진 상호작용 네트워크 그래프."""
    resolution = service.find_interactions(payload.ingredients)
    return to_cytoscape(build_graph(resolution.interactions))
