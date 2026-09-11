"""분석 서비스 — 라우터와 코어 로직 사이를 잇는다."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.explain import Explainer
from app.core.normalize import IngredientIndex, Match
from app.core.products import ProductIndex
from app.core.repository import Interaction, InteractionRepository
from app.core.scoring import overall_risk, timing_advice
from app.schemas import (
    RISK_LABELS,
    AnalyzeResponse,
    ExplainedInteraction,
    ExplainResponse,
    InteractionPair,
    MatchedIngredient,
)

DISCLAIMER = (
    "본 결과는 공개 데이터에 기반한 참고 정보이며 의학적 진단이나 처방이 아닙니다. "
    "실제 복약 변경은 반드시 의사·약사와 상의하세요."
)
SEED_DISCLAIMER = (
    "[개발용 시드 데이터로 동작 중입니다 — 실제 복약 판단에 사용하지 마세요] "
)


@dataclass(frozen=True)
class Resolution:
    """입력 해석 결과 한 묶음.

    `matches` 와 `via_products` 는 길이가 같고 인덱스가 대응한다.
    제품명으로 들어와 성분으로 펼쳐진 항목은 via_products 에 제품명이 들어간다.
    """

    matches: list[Match] = field(default_factory=list)
    via_products: list[str | None] = field(default_factory=list)
    interactions: list[Interaction] = field(default_factory=list)
    # 제품으로 보이지만 어느 제품인지 확정하지 못한 입력 → 사용자에게 되물어야 한다
    ambiguous_products: dict[str, list[str]] = field(default_factory=dict)

    @property
    def unmatched(self) -> list[str]:
        return [m.input for m in self.matches if not m.ok]


class AnalysisService:
    def __init__(
        self,
        index: IngredientIndex,
        repo: InteractionRepository,
        products: ProductIndex | None = None,
    ) -> None:
        self.index = index
        self.repo = repo
        self.products = products or ProductIndex([])

    def _expand(self, raw_inputs: list[str]) -> tuple[list[tuple[str, str | None]], dict[str, list[str]]]:
        """제품명을 성분 목록으로 펼친다.

        시민은 "타이레놀"을 치지 "아세트아미노펜"을 치지 않는다. 성분 색인에
        걸리는 입력은 건드리지 않고, 안 걸릴 때만 제품으로 해석해 본다.
        (성분명이 우연히 제품명과 겹칠 때 성분 쪽을 우선하기 위함이다.)
        """
        expanded: list[tuple[str, str | None]] = []
        ambiguous: dict[str, list[str]] = {}

        for raw in raw_inputs:
            if self.index.resolve(raw).ok:
                expanded.append((raw, None))
                continue

            product, candidates = self.products.expand(raw)
            if product is not None:
                for ingredient in product.ingredients:
                    expanded.append((ingredient, product.name))
                continue

            if candidates:
                # 이름은 알겠는데 어느 제품인지 모른다. 임의로 고르지 않는다.
                ambiguous[raw] = [c.search_name for c in candidates]
            expanded.append((raw, None))

        return expanded, ambiguous

    def find_interactions(self, raw_ingredients: list[str]) -> Resolution:
        """입력을 표준 성분으로 해석하고 상호작용을 찾는다.

        analyze() 와 그래프·설명 계층이 같은 결과를 보게 하려고 한곳에 모았다.
        """
        expanded, ambiguous = self._expand(raw_ingredients)
        matches = self.index.resolve_many([name for name, _ in expanded])
        canonicals = [m.canonical for m in matches if m.canonical]
        return Resolution(
            matches=matches,
            via_products=[via for _, via in expanded],
            interactions=self.repo.find_all(canonicals),
            ambiguous_products=ambiguous,
        )

    @property
    def disclaimer(self) -> str:
        if self.repo.is_seed:
            return SEED_DISCLAIMER + DISCLAIMER
        return DISCLAIMER

    def analyze(self, raw_ingredients: list[str]) -> AnalyzeResponse:
        return self.build_response(self.find_interactions(raw_ingredients))

    def build_response(self, resolution: Resolution) -> AnalyzeResponse:
        """이미 찾아둔 결과로 응답을 조립한다.

        설명 계층이 같은 입력을 두 번 해석하지 않도록 분리해 두었다.
        """
        matches = resolution.matches
        interactions = resolution.interactions
        risk = overall_risk(interactions)

        return AnalyzeResponse(
            overall_risk=risk,
            overall_label=RISK_LABELS[risk],
            matched=[
                MatchedIngredient(
                    input=m.input,
                    canonical=m.canonical,
                    confidence=m.confidence,
                    method=m.method,
                    via_product=via,
                )
                for m, via in zip(matches, resolution.via_products)
            ],
            ambiguous_products=resolution.ambiguous_products,
            unmatched=[m.input for m in matches if not m.ok],
            interactions=[
                InteractionPair(
                    ingredient_a=i.ingredient_a,
                    ingredient_b=i.ingredient_b,
                    severity=i.severity,
                    severity_label=RISK_LABELS[i.severity],
                    mechanism=i.mechanism,
                    advice=i.advice,
                    min_interval_hours=i.min_interval_hours,
                    source=i.source,
                )
                for i in interactions
            ],
            timing_advice=timing_advice(interactions),
            disclaimer=self.disclaimer,
        )


class ExplanationService:
    """분석 결과 위에 설명을 얹는다.

    분석(결정론적)과 설명(LLM)을 굳이 분리해 둔 이유는, 앱이 오프라인에서
    분석까지는 스스로 하고 설명만 서버에 물어보는 구조이기 때문이다.
    설명이 실패해도 분석 결과는 그대로 응답에 실린다.
    """

    def __init__(self, analysis: AnalysisService, explainer: Explainer) -> None:
        self.analysis = analysis
        self.explainer = explainer

    def explain(self, raw_ingredients: list[str]) -> ExplainResponse:
        resolution = self.analysis.find_interactions(raw_ingredients)
        result = self.analysis.build_response(resolution)

        explanation = self.explainer.explain(
            resolution.interactions,
            overall_risk(resolution.interactions),
            resolution.unmatched,
        )

        return ExplainResponse(
            summary=explanation.summary,
            interactions=[
                ExplainedInteraction(
                    ingredient_a=item.ingredient_a,
                    ingredient_b=item.ingredient_b,
                    severity=item.severity,
                    severity_label=item.severity_label,
                    plain=item.plain,
                    source=item.source,
                )
                for item in explanation.items
            ],
            what_to_do=explanation.what_to_do,
            source=explanation.source,
            model=explanation.model,
            cached=explanation.cached,
            analysis=result,
            # 근거 안에도 들어 있지만, 설명만 렌더링하는 화면에서 면책이
            # 사라지지 않도록 최상위에 한 번 더 둔다.
            disclaimer=result.disclaimer,
        )
