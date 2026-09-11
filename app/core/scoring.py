"""위험도 스코어링.

개별 쌍의 등급(1~5)은 데이터에서 온다. 이 모듈이 하는 일은
"여러 쌍을 하나의 종합 위험도로 어떻게 합칠 것인가" 이다.

설계 원칙:
  - 최댓값 기반. 위험은 평균되지 않는다. 4등급 하나가 1등급 열 개보다 위험하다.
  - 다만 중등도(3 이상) 상호작용이 여러 건 겹치면 한 단계 올린다.
  - 데이터에 없는 조합은 "안전"이 아니라 "정보 없음"(1등급)이다.
"""

from __future__ import annotations

from app.core.repository import Interaction

# 중등도 이상 상호작용이 이 개수 이상이면 종합 위험도를 한 단계 올린다.
_ESCALATION_THRESHOLD = 3
_ESCALATION_MIN_SEVERITY = 3


def overall_risk(interactions: list[Interaction]) -> int:
    """상호작용 목록으로부터 종합 위험도 1~5를 계산한다."""
    if not interactions:
        return 1

    score = max(i.severity for i in interactions)
    moderate_plus = sum(1 for i in interactions if i.severity >= _ESCALATION_MIN_SEVERITY)
    if moderate_plus >= _ESCALATION_THRESHOLD:
        score += 1

    return max(1, min(5, score))


def timing_advice(interactions: list[Interaction]) -> list[str]:
    """복용 간격 권고 문장을 만든다. 간격 정보가 있는 쌍만 대상."""
    lines: list[str] = []
    for item in sorted(interactions, key=lambda i: -i.severity):
        if item.min_interval_hours:
            hours = item.min_interval_hours
            hours_text = f"{hours:g}"
            lines.append(
                f"{item.ingredient_a} 와(과) {item.ingredient_b} 은(는) "
                f"{hours_text}시간 이상 간격을 두고 복용하세요."
            )
    return lines
