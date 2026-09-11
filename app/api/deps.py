"""의존성 주입. 데이터는 프로세스당 한 번만 읽는다."""

from functools import lru_cache

from app.config import get_settings
from app.core.explain import Explainer, openai_chat
from app.core.normalize import IngredientIndex
from app.core.products import ProductIndex
from app.core.repository import InteractionRepository
from app.core.service import AnalysisService, ExplanationService
from app.core.vision import PhotoReader, openai_vision


@lru_cache
def get_index() -> IngredientIndex:
    """세 소스를 합쳐 색인을 만든다.

    1. 손으로 쓴 동의어 사전 (상품명·구어체·흔한 오타 — 기계가 못 만드는 것들)
    2. 원본에서 자동 생성한 별칭 (영문명 등, scripts/build_aliases.py)
    3. 상호작용 데이터에 등장하는 성분명 그 자체

    사전만 쓰면 적재한 데이터의 극히 일부만 조회된다. 순서가 곧 우선순위라,
    같은 별칭이 겹치면 손으로 쓴 쪽이 남는다.
    """
    settings = get_settings()
    return IngredientIndex.from_csvs(
        [
            settings.seed_path / "ingredient_synonyms.csv",
            settings.processed_path / "aliases_generated.csv",
        ],
        fuzzy_cutoff=settings.fuzzy_cutoff,
        extra_canonicals=get_repository().ingredients,
    )


@lru_cache
def get_repository() -> InteractionRepository:
    settings = get_settings()
    return InteractionRepository.load(settings.processed_path, settings.seed_path)


@lru_cache
def get_products() -> ProductIndex:
    """제품명 → 성분 색인. 파일이 없으면 빈 색인이라 서버는 그대로 뜬다."""
    return ProductIndex.from_csv(get_settings().processed_path / "products.csv")


@lru_cache
def get_service() -> AnalysisService:
    return AnalysisService(get_index(), get_repository(), get_products())


@lru_cache
def get_explainer() -> Explainer:
    """키가 없으면 chat=None 인 Explainer 를 준다 — 템플릿 설명으로 동작한다."""
    settings = get_settings()
    if not settings.openai_api_key:
        return Explainer(None)
    return Explainer(
        openai_chat(settings.openai_api_key, settings.openai_model),
        model=settings.openai_model,
    )


@lru_cache
def get_explanation_service() -> ExplanationService:
    return ExplanationService(get_service(), get_explainer())


@lru_cache
def get_photo_reader() -> PhotoReader:
    """사진 인식기. 키가 없으면 꺼진 상태로 준다 (읽은 척하지 않는다)."""
    settings = get_settings()
    if not settings.openai_api_key:
        return PhotoReader(None)
    return PhotoReader(
        openai_vision(settings.openai_api_key, settings.openai_model),
        model=settings.openai_model,
    )
