"""상호작용 결과를 시민의 언어로 풀어쓰는 설명 레이어.

이 모듈의 존재 이유는 하나다. `/api/analyze` 는 "와파린 × 아스피린 = 4등급"까지만
알려준다. 시민에게 필요한 건 "그래서 내가 오늘 뭘 해야 하나"이다.

## 절대 규칙

LLM 은 **새로운 상호작용을 만들어낼 수 없다.** 이 모듈은 결정론적 레이어
(repository → scoring)가 이미 찾아낸 쌍만 근거로 넘기고, 모델에게는 그것을
풀어쓰는 일만 시킨다. 등급·성분쌍은 응답에서 다시 읽지 않고 원본을 그대로 쓴다.
모델이 근거에 없는 쌍을 지어내면 그 항목은 버린다.

약 정보에서 환각은 오답이 아니라 사고다. 그래서 검증은 관대하지 않다.

## 키가 없어도 동작한다

OPENAI_API_KEY 가 비어 있거나 호출이 실패하면 템플릿 설명으로 내려앉는다.
설명 엔드포인트가 500 을 내는 일은 없어야 한다 — 근거 데이터는 이미 손에
있으므로, 말을 예쁘게 못 다듬는 것이 서비스를 멈출 이유는 되지 않는다.
응답의 `source` 필드가 "llm" 인지 "template" 인지를 항상 밝힌다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any, Protocol

from app.core.repository import Interaction
from app.schemas import RISK_LABELS

logger = logging.getLogger(__name__)

# 모델에 근거로 넘기는 상호작용 개수 상한. 조합 폭발(30개 입력 → 435쌍)로
# 프롬프트가 비대해지는 걸 막는다. 심각한 순으로 자르므로 잘리는 건 경미한 쪽이다.
MAX_FACTS = 12

# 캐시 상한. 같은 조합을 반복 조회하는 비용을 없애는 게 목적이라 크지 않아도 된다.
CACHE_SIZE = 256

SYSTEM_PROMPT = """\
너는 한국의 복약 안전 안내 도우미다. 약사가 환자에게 설명하듯, 겁주지 않되 얼버무리지도 않는 말투로 쓴다.

반드시 지킬 것:
1. 아래 "확인된 상호작용"에 없는 성분 조합은 절대 언급하지 마라. 네가 알고 있는 다른 상호작용도 쓰지 마라. 근거에 있는 것만 쓴다.
2. 위험 등급을 바꾸거나 새로 매기지 마라. 등급은 이미 정해져 있다.
3. "안전합니다", "괜찮습니다" 라고 단정하지 마라. 데이터에 없다는 것은 안전하다는 뜻이 아니라 확인되지 않았다는 뜻이다.
4. 진단하거나 처방하지 마라. 복용 중단·용량 변경을 지시하지 말고, 필요하면 의사·약사와 상의하라고 안내한다.
5. 존댓말, 쉬운 한국어. 전문용어를 써야 하면 괄호로 풀어준다.

출력은 JSON 객체 하나. 다른 텍스트를 붙이지 마라.
{
  "summary": "전체 상황 요약 2~3문장. 가장 급한 것부터.",
  "items": [{"pair_index": 0, "plain": "이 조합이 왜 문제인지 1~2문장."}],
  "what_to_do": ["오늘 당장 할 수 있는 행동 지침"]
}
items 에는 확인된 상호작용마다 pair_index 를 그대로 써서 한 항목씩 넣는다.
what_to_do 는 2~4개. 확인하지 못한 성분이 있으면 그 사실을 summary 에 한 번 언급한다.
"""


class ChatUnavailable(RuntimeError):
    """설명 생성을 못 하는 상태. 메시지는 사용자에게 보여줄 수 있어야 한다.

    키가 없거나, 결제 수단이 없거나, 한도를 넘겼거나 — 원인이 무엇이든
    분석 결과는 그대로 나가야 한다. 이 예외는 "템플릿으로 내려앉되
    로그에는 이유를 한 줄로 남긴다"는 뜻이다.
    """


# OpenAI 가 돌려주는 오류 코드 → 사람이 읽고 바로 조치할 수 있는 안내.
# 스택 트레이스를 읽게 만들지 않는다.
_OPENAI_HINTS = {
    "insufficient_quota": (
        "OpenAI 크레딧이 없습니다. platform.openai.com 의 Billing 에서 "
        "결제 수단을 등록하거나 크레딧을 충전해야 호출됩니다. "
        "(키를 만드는 것은 무료지만 호출에는 크레딧이 듭니다)"
    ),
    "invalid_api_key": (
        "OpenAI API 키가 올바르지 않습니다. .env 의 OPENAI_API_KEY 를 확인하세요."
    ),
    "rate_limit_exceeded": "OpenAI 호출 한도를 넘겼습니다. 잠시 후 다시 시도하세요.",
    "model_not_found": (
        "요청한 모델을 쓸 수 없습니다. .env 의 OPENAI_MODEL 을 확인하세요."
    ),
}


def _explain_openai_error(exc: Exception) -> str:
    """예외에서 알아볼 수 있는 신호를 찾아 안내 문구로 바꾼다."""
    blob = f"{type(exc).__name__} {exc}".lower()
    for token, message in _OPENAI_HINTS.items():
        if token in blob or token.replace("_", " ") in blob:
            return message
    # 잔액 부족은 코드 없이 문구로만 오는 경우가 있다
    if "quota" in blob or "billing" in blob:
        return _OPENAI_HINTS["insufficient_quota"]
    return f"OpenAI 호출에 실패했습니다: {exc}"


class ChatClient(Protocol):
    """설명 생성에 필요한 최소 인터페이스.

    openai 클라이언트 전체가 아니라 이 함수 하나만 요구한다. 테스트에서 가짜를
    끼우기 쉽고, 나중에 다른 공급자로 바꿔도 이 파일만 손대면 된다.
    """

    def __call__(self, system: str, user: str) -> str:
        """JSON 문자열을 돌려준다. 실패하면 예외를 던진다."""


@dataclass(frozen=True)
class ExplainedItem:
    ingredient_a: str
    ingredient_b: str
    severity: int
    severity_label: str
    plain: str
    source: str = ""


@dataclass(frozen=True)
class Explanation:
    summary: str
    items: list[ExplainedItem]
    what_to_do: list[str]
    source: str  # "llm" | "template"
    model: str | None = None
    cached: bool = False


def _grounding(
    interactions: list[Interaction],
    overall: int,
    unmatched: list[str],
) -> dict[str, Any]:
    """모델에게 넘길 근거. 여기 없는 사실은 모델도 쓸 수 없다."""
    return {
        "종합_위험도": f"{overall}등급 ({RISK_LABELS[overall]})",
        "확인된_상호작용": [
            {
                "pair_index": idx,
                "성분_A": i.ingredient_a,
                "성분_B": i.ingredient_b,
                "등급": f"{i.severity}등급 ({RISK_LABELS[i.severity]})",
                "작용_기전": i.mechanism,
                "권고": i.advice,
                "권장_복용간격_시간": i.min_interval_hours,
            }
            for idx, i in enumerate(interactions)
        ],
        "확인하지_못한_입력": unmatched,
    }


def _cache_key(grounding: dict[str, Any], model: str) -> str:
    blob = json.dumps(grounding, ensure_ascii=False, sort_keys=True) + "|" + model
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _template_line(i: Interaction) -> str:
    """LLM 없이 만드는 한 쌍짜리 설명. 데이터에 있는 문장을 이어 붙인다."""
    parts = [p for p in (i.mechanism, i.advice) if p]
    if i.min_interval_hours:
        parts.append(f"{i.min_interval_hours:g}시간 이상 간격을 두세요")
    return ". ".join(parts) if parts else "함께 복용할 때 주의가 필요한 조합입니다"


def _as_item(i: Interaction, plain: str) -> ExplainedItem:
    return ExplainedItem(
        ingredient_a=i.ingredient_a,
        ingredient_b=i.ingredient_b,
        severity=i.severity,
        severity_label=RISK_LABELS[i.severity],
        plain=plain,
        source=i.source,
    )


def template_explanation(
    interactions: list[Interaction],
    overall: int,
    unmatched: list[str],
) -> Explanation:
    """LLM 을 못 쓸 때의 설명. 문장이 덜 매끄러울 뿐 내용은 같은 근거에서 나온다."""
    items = [_as_item(i, _template_line(i)) for i in interactions]

    if interactions:
        top = interactions[0]
        summary = (
            f"입력하신 조합에서 {len(interactions)}건의 상호작용이 확인됐고, "
            f"가장 주의가 필요한 것은 {top.ingredient_a} 와(과) {top.ingredient_b} "
            f"조합({top.severity}등급)입니다."
        )
    else:
        summary = (
            "입력하신 조합에서는 등록된 상호작용이 확인되지 않았습니다. "
            "다만 이는 안전하다는 뜻이 아니라 저희 데이터에 기록이 없다는 뜻입니다."
        )
    if unmatched:
        summary += f" 다음 입력은 성분을 확인하지 못했습니다: {', '.join(unmatched)}."

    what_to_do = [i.advice for i in interactions if i.advice][:3]
    what_to_do.append("복용 중인 약과 영양제 목록을 의사·약사에게 그대로 보여주세요.")

    return Explanation(
        summary=summary,
        items=items,
        what_to_do=what_to_do,
        source="template",
    )


def _parse(
    raw: str, interactions: list[Interaction]
) -> tuple[str, list[ExplainedItem], list[str]]:
    """모델 응답을 검증한다. 근거에 없는 쌍은 버리고, 빠진 쌍은 템플릿으로 채운다."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아닙니다")

    summary = str(data.get("summary", "")).strip()
    if not summary:
        raise ValueError("summary 가 비어 있습니다")

    plains: dict[int, str] = {}
    for entry in data.get("items") or []:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("pair_index")
        text = str(entry.get("plain", "")).strip()
        # 근거에 없는 인덱스 = 모델이 지어낸 쌍. 조용히 버린다.
        # bool 은 int 의 서브클래스라 명시적으로 걸러낸다.
        if isinstance(idx, int) and not isinstance(idx, bool):
            if 0 <= idx < len(interactions) and text:
                plains.setdefault(idx, text)

    # 등급과 성분쌍은 언제나 원본에서 온다. 모델 응답에서 읽지 않는다.
    items = [
        _as_item(i, plains.get(idx) or _template_line(i))
        for idx, i in enumerate(interactions)
    ]

    what_to_do = [
        str(s).strip() for s in (data.get("what_to_do") or []) if str(s).strip()
    ][:4]

    return summary, items, what_to_do


def preview_prompt(
    interactions: list[Interaction],
    overall: int,
    unmatched: list[str],
) -> tuple[str, str]:
    """실제로 보낼 (system, user) 를 그대로 돌려준다. 호출은 하지 않는다.

    프롬프트가 마음에 드는지는 돈을 쓰기 전에 확인할 수 있어야 한다.
    """
    grounding = _grounding(interactions[:MAX_FACTS], overall, unmatched)
    return SYSTEM_PROMPT, json.dumps(grounding, ensure_ascii=False, indent=2)


class Explainer:
    def __init__(
        self,
        chat: ChatClient | None,
        *,
        model: str = "",
        cache_size: int = CACHE_SIZE,
    ) -> None:
        self._chat = chat
        self._model = model
        self._cache: OrderedDict[str, Explanation] = OrderedDict()
        self._cache_size = cache_size

    @property
    def enabled(self) -> bool:
        return self._chat is not None

    def explain(
        self,
        interactions: list[Interaction],
        overall: int,
        unmatched: list[str],
    ) -> Explanation:
        capped = interactions[:MAX_FACTS]  # repository.find_all 이 이미 심각한 순 정렬
        fallback = template_explanation(capped, overall, unmatched)

        if self._chat is None:
            return fallback

        grounding = _grounding(capped, overall, unmatched)
        key = _cache_key(grounding, self._model)
        if (hit := self._cache.get(key)) is not None:
            self._cache.move_to_end(key)
            return replace(hit, cached=True)

        user = json.dumps(grounding, ensure_ascii=False, indent=2)
        try:
            raw = self._chat(SYSTEM_PROMPT, user)
            summary, items, what_to_do = _parse(raw, capped)
        except ChatUnavailable as exc:
            # 원인을 아는 실패. 스택 트레이스는 소용이 없고 안내 문구가 필요하다.
            logger.warning("LLM 설명을 건너뜁니다 — %s", exc)
            return fallback
        except Exception:
            # 설명을 못 만드는 것이 분석 결과를 못 주는 이유가 되면 안 된다.
            logger.exception("LLM 설명 생성 실패 — 템플릿 설명으로 대체합니다")
            return fallback

        result = Explanation(
            summary=summary,
            items=items,
            what_to_do=what_to_do or fallback.what_to_do,
            source="llm",
            model=self._model,
        )
        self._cache[key] = result
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return result


def openai_chat(api_key: str, model: str, *, timeout: float = 30.0) -> ChatClient:
    """openai 패키지를 이 함수 안에서만 import 한다.

    키가 없는 환경(테스트·CI)에서 패키지나 클라이언트 초기화 때문에
    앱이 못 뜨는 일을 막기 위해서다.
    """
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=timeout)

    def chat(system: str, user: str) -> str:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,  # 같은 약 조합에 매번 다른 말이 나오면 신뢰를 잃는다
            )
        except Exception as exc:  # openai 예외 계층에 의존하지 않는다
            raise ChatUnavailable(_explain_openai_error(exc)) from exc
        return response.choices[0].message.content or ""

    return chat
