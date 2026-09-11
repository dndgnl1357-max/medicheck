"""API 요청/응답 스키마."""

from typing import Literal

from pydantic import BaseModel, Field

RiskLevel = Literal[1, 2, 3, 4, 5]

RISK_LABELS: dict[int, str] = {
    1: "정보 없음 / 특이사항 낮음",
    2: "가벼운 주의",
    3: "주의 — 복용 간격 조절 권장",
    4: "높은 주의 — 전문가 상담 권장",
    5: "병용금기 — 함께 복용하지 마세요",
}


class MatchedIngredient(BaseModel):
    """사용자가 입력한 문자열이 어떤 표준 성분으로 해석됐는지."""

    input: str = Field(description="사용자가 입력한 원문")
    canonical: str | None = Field(default=None, description="매칭된 표준 성분명")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    method: Literal["exact", "synonym", "fuzzy", "none"] = "none"
    via_product: str | None = Field(
        default=None, description="제품명으로 입력되어 이 성분으로 펼쳐진 경우 그 제품명"
    )


class InteractionPair(BaseModel):
    """성분 두 개 사이의 상호작용 한 건."""

    ingredient_a: str
    ingredient_b: str
    severity: RiskLevel
    severity_label: str
    mechanism: str = ""
    advice: str = ""
    min_interval_hours: float | None = Field(
        default=None, description="이 간격 이상 띄워 복용하도록 권고되는 시간"
    )
    source: str = ""


class AnalyzeRequest(BaseModel):
    ingredients: list[str] = Field(
        min_length=1,
        max_length=30,
        description="복용 중인 약물·영양제 성분명 또는 제품명 목록",
        examples=[["와파린", "아스피린", "오메가3"]],
    )


class AnalyzeResponse(BaseModel):
    overall_risk: RiskLevel
    overall_label: str
    matched: list[MatchedIngredient]
    unmatched: list[str] = Field(
        default_factory=list, description="표준 성분으로 해석하지 못한 입력"
    )
    interactions: list[InteractionPair] = Field(default_factory=list)
    timing_advice: list[str] = Field(default_factory=list)
    ambiguous_products: dict[str, list[str]] = Field(
        default_factory=dict,
        description="제품으로 보이나 어느 제품인지 확정 못 한 입력 → 후보 목록",
    )
    disclaimer: str


class ExplainedInteraction(BaseModel):
    """상호작용 한 건을 시민의 언어로 풀어쓴 것."""

    ingredient_a: str
    ingredient_b: str
    severity: RiskLevel
    severity_label: str
    plain: str = Field(description="이 조합이 왜 문제인지에 대한 쉬운 설명")
    source: str = Field(
        default="", description="이 정보의 출처. 데이터가 여러 출처에서 섞여 들어온다."
    )


class ExplainResponse(BaseModel):
    summary: str
    interactions: list[ExplainedInteraction] = Field(default_factory=list)
    what_to_do: list[str] = Field(default_factory=list)
    source: Literal["llm", "template"] = Field(
        description="설명을 만든 주체. 키가 없거나 호출이 실패하면 template 이 된다."
    )
    model: str | None = None
    cached: bool = False
    analysis: AnalyzeResponse = Field(description="설명의 근거가 된 분석 결과")
    disclaimer: str
