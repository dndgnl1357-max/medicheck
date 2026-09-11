"""설명 레이어 테스트.

여기서 지키려는 건 문장 품질이 아니라 안전 속성이다.
LLM 이 무슨 말을 하든 등급과 성분쌍은 결정론적 레이어의 것이어야 하고,
LLM 이 죽어도 엔드포인트는 살아 있어야 한다.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_explanation_service
from app.core.explain import MAX_FACTS, Explainer, template_explanation
from app.core.repository import Interaction
from app.core.service import ExplanationService
from app.main import app

WARFARIN_ASPIRIN = Interaction(
    ingredient_a="와파린",
    ingredient_b="아스피린",
    severity=4,
    mechanism="항혈소판 작용이 더해져 출혈 위험이 커집니다",
    advice="반드시 의사와 상의하세요",
    source="테스트",
)
LEVO_CALCIUM = Interaction(
    ingredient_a="레보티록신",
    ingredient_b="칼슘",
    severity=3,
    mechanism="칼슘이 흡수를 방해합니다",
    advice="간격을 두고 복용하세요",
    min_interval_hours=4,
    source="테스트",
)


class FakeChat:
    """호출 횟수를 세는 가짜 LLM. 정해진 응답 또는 예외를 돌려준다."""

    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls = 0
        self.last_user = ""

    def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        self.last_user = user
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def reply(summary: str = "요약입니다.", items=None, what_to_do=None) -> str:
    return json.dumps(
        {
            "summary": summary,
            "items": items if items is not None else [],
            "what_to_do": what_to_do if what_to_do is not None else ["의사와 상의하세요"],
        },
        ensure_ascii=False,
    )


# --- 템플릿 폴백 -------------------------------------------------------------


def test_키가_없으면_템플릿_설명이_나온다():
    explainer = Explainer(None)
    assert not explainer.enabled

    result = explainer.explain([WARFARIN_ASPIRIN], 4, [])
    assert result.source == "template"
    assert result.model is None
    assert len(result.items) == 1
    assert "출혈" in result.items[0].plain


def test_상호작용이_없어도_안전하다고_말하지_않는다():
    result = template_explanation([], 1, [])
    assert "안전" not in result.summary or "안전하다는 뜻이 아니라" in result.summary
    assert "기록이 없다" in result.summary


def test_확인하지_못한_입력은_요약에_드러난다():
    result = template_explanation([WARFARIN_ASPIRIN], 4, ["듣도보도못한것"])
    assert "듣도보도못한것" in result.summary


def test_복용간격은_템플릿_문장에_들어간다():
    result = template_explanation([LEVO_CALCIUM], 3, [])
    assert "4시간" in result.items[0].plain


# --- LLM 경로 ----------------------------------------------------------------


def test_정상_응답이면_source가_llm():
    chat = FakeChat(
        reply(items=[{"pair_index": 0, "plain": "함께 먹으면 피가 잘 안 멎을 수 있어요."}])
    )
    result = Explainer(chat, model="test-model").explain([WARFARIN_ASPIRIN], 4, [])

    assert result.source == "llm"
    assert result.model == "test-model"
    assert result.summary == "요약입니다."
    assert result.items[0].plain == "함께 먹으면 피가 잘 안 멎을 수 있어요."


def test_모델은_등급과_성분쌍을_바꿀_수_없다():
    """응답에 등급을 실어 보내도 무시되고 원본이 나간다."""
    chat = FakeChat(
        json.dumps(
            {
                "summary": "요약",
                "items": [
                    {
                        "pair_index": 0,
                        "plain": "설명",
                        "severity": 1,
                        "ingredient_a": "타이레놀",
                        "ingredient_b": "비타민C",
                    }
                ],
                "what_to_do": ["상의하세요"],
            },
            ensure_ascii=False,
        )
    )
    result = Explainer(chat).explain([WARFARIN_ASPIRIN], 4, [])

    assert result.items[0].severity == 4
    assert result.items[0].ingredient_a == "와파린"
    assert result.items[0].ingredient_b == "아스피린"


def test_근거에_없는_쌍을_지어내면_버린다():
    chat = FakeChat(
        reply(
            items=[
                {"pair_index": 0, "plain": "진짜 설명"},
                {"pair_index": 7, "plain": "지어낸 상호작용"},
                {"pair_index": -1, "plain": "이것도 지어낸 것"},
            ]
        )
    )
    result = Explainer(chat).explain([WARFARIN_ASPIRIN], 4, [])

    assert len(result.items) == 1
    assert all("지어낸" not in item.plain for item in result.items)


def test_빠뜨린_쌍은_템플릿_문장으로_채운다():
    """모델이 한 쌍만 설명해도 나머지 쌍이 응답에서 사라지면 안 된다."""
    chat = FakeChat(reply(items=[{"pair_index": 0, "plain": "첫 번째만 설명"}]))
    result = Explainer(chat).explain([WARFARIN_ASPIRIN, LEVO_CALCIUM], 4, [])

    assert len(result.items) == 2
    assert result.items[0].plain == "첫 번째만 설명"
    assert "흡수를 방해" in result.items[1].plain


@pytest.mark.parametrize(
    "bad",
    [
        "이건 JSON이 아닙니다",
        "[]",
        '{"items": []}',  # summary 없음
        '{"summary": "   "}',  # summary 가 공백뿐
    ],
)
def test_응답이_쓸_수_없으면_템플릿으로_내려앉는다(bad):
    result = Explainer(FakeChat(bad)).explain([WARFARIN_ASPIRIN], 4, [])
    assert result.source == "template"
    assert result.items  # 근거는 그대로 남는다


def test_호출이_실패해도_예외가_새지_않는다():
    result = Explainer(FakeChat(RuntimeError("API 다운"))).explain(
        [WARFARIN_ASPIRIN], 4, []
    )
    assert result.source == "template"


def test_bool은_pair_index로_받지_않는다():
    """파이썬에서 True 는 int 1 이다. 인덱스로 새어 들어가면 안 된다."""
    chat = FakeChat(reply(items=[{"pair_index": True, "plain": "잘못된 인덱스"}]))
    result = Explainer(chat).explain([WARFARIN_ASPIRIN, LEVO_CALCIUM], 4, [])
    assert all("잘못된 인덱스" not in item.plain for item in result.items)


# --- 캐시 --------------------------------------------------------------------


def test_같은_조합은_두_번_묻지_않는다():
    chat = FakeChat(reply(items=[{"pair_index": 0, "plain": "설명"}]))
    explainer = Explainer(chat)

    first = explainer.explain([WARFARIN_ASPIRIN], 4, [])
    second = explainer.explain([WARFARIN_ASPIRIN], 4, [])

    assert chat.calls == 1
    assert first.cached is False
    assert second.cached is True
    assert second.summary == first.summary


def test_조합이_다르면_다시_묻는다():
    chat = FakeChat(reply(items=[{"pair_index": 0, "plain": "설명"}]))
    explainer = Explainer(chat)

    explainer.explain([WARFARIN_ASPIRIN], 4, [])
    explainer.explain([LEVO_CALCIUM], 3, [])

    assert chat.calls == 2


def test_캐시는_상한을_넘지_않는다():
    chat = FakeChat(reply())
    explainer = Explainer(chat, cache_size=2)

    for hours in range(5):
        explainer.explain(
            [Interaction("가", "나", 3, min_interval_hours=float(hours))], 3, []
        )

    assert len(explainer._cache) <= 2


# --- 근거 상한 ---------------------------------------------------------------


def test_근거는_상한까지만_넘긴다():
    many = [
        Interaction(f"성분{n}", f"성분{n + 100}", severity=3) for n in range(MAX_FACTS + 5)
    ]
    chat = FakeChat(reply())
    Explainer(chat).explain(many, 4, [])

    grounding = json.loads(chat.last_user)
    assert len(grounding["확인된_상호작용"]) == MAX_FACTS


# --- 엔드포인트 --------------------------------------------------------------


client = TestClient(app)


def test_explain_엔드포인트는_키_없이도_200():
    r = client.post("/api/explain", json={"ingredients": ["와파린", "아스피린"]})
    assert r.status_code == 200
    body = r.json()

    assert body["source"] == "template"
    assert body["summary"]
    assert len(body["interactions"]) == 1
    assert body["interactions"][0]["severity"] == 4
    assert body["disclaimer"]
    # 설명의 근거가 된 분석 결과가 함께 실린다
    assert body["analysis"]["overall_risk"] == 4


def test_explain_응답에_면책이_두_군데_다_있다():
    body = client.post("/api/explain", json={"ingredients": ["와파린"]}).json()
    assert body["disclaimer"] == body["analysis"]["disclaimer"]


def test_health가_llm_상태를_알려준다():
    body = client.get("/api/health").json()
    assert "llm_enabled" in body


def test_explain_엔드포인트가_llm_설명을_실어_보낸다():
    """의존성을 갈아끼워 LLM 경로를 태운다."""
    from app.api.deps import get_service

    chat = FakeChat(reply(summary="약사님 말투 요약", items=[{"pair_index": 0, "plain": "쉬운 설명"}]))
    app.dependency_overrides[get_explanation_service] = lambda: ExplanationService(
        get_service(), Explainer(chat, model="test-model")
    )
    try:
        body = client.post(
            "/api/explain", json={"ingredients": ["와파린", "아스피린"]}
        ).json()
    finally:
        app.dependency_overrides.clear()

    assert body["source"] == "llm"
    assert body["model"] == "test-model"
    assert body["summary"] == "약사님 말투 요약"
    assert body["interactions"][0]["plain"] == "쉬운 설명"
    assert body["interactions"][0]["severity_label"]


def test_explain도_빈_입력은_422():
    assert client.post("/api/explain", json={"ingredients": []}).status_code == 422


# --- 실패 안내 ---------------------------------------------------------------


class FakeOpenAIError(Exception):
    """openai 예외 계층을 흉내 낸다. 실제 패키지에 의존하지 않기 위함."""


@pytest.mark.parametrize(
    "message, keyword",
    [
        ("Error code: 429 - insufficient_quota", "Billing"),
        ("You exceeded your current quota, please check your plan and billing", "Billing"),
        ("Error code: 401 - invalid_api_key", "OPENAI_API_KEY"),
        ("Error code: 429 - rate_limit_exceeded", "한도"),
        ("Error code: 404 - model_not_found", "OPENAI_MODEL"),
    ],
)
def test_openai_오류를_사람_말로_바꾼다(message, keyword):
    from app.core.explain import _explain_openai_error

    assert keyword in _explain_openai_error(FakeOpenAIError(message))


def test_모르는_오류는_원문을_보여준다():
    from app.core.explain import _explain_openai_error

    assert "이상한 오류" in _explain_openai_error(FakeOpenAIError("이상한 오류"))


def test_크레딧이_없어도_분석은_나간다():
    """결제 전에는 이 경로를 탄다. 500 이 나면 안 된다."""
    from app.core.explain import ChatUnavailable

    def chat(system, user):
        raise ChatUnavailable("OpenAI 크레딧이 없습니다.")

    result = Explainer(chat, model="gpt-4o").explain([WARFARIN_ASPIRIN], 4, [])

    assert result.source == "template"
    assert result.items[0].severity == 4  # 근거는 그대로 남는다


def test_크레딧이_없으면_엔드포인트도_200():
    from app.api.deps import get_service
    from app.core.explain import ChatUnavailable

    def chat(system, user):
        raise ChatUnavailable("OpenAI 크레딧이 없습니다.")

    app.dependency_overrides[get_explanation_service] = lambda: ExplanationService(
        get_service(), Explainer(chat, model="gpt-4o")
    )
    try:
        r = client.post("/api/explain", json={"ingredients": ["와파린", "아스피린"]})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    assert r.json()["source"] == "template"


def test_실패는_캐시되지_않는다():
    """결제 후 다시 물어봐야 한다. 실패를 캐시하면 영영 템플릿만 나온다."""
    from app.core.explain import ChatUnavailable

    calls = {"n": 0}

    def chat(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ChatUnavailable("크레딧 없음")
        return reply(items=[{"pair_index": 0, "plain": "이제 됩니다"}])

    explainer = Explainer(chat, model="gpt-4o")
    first = explainer.explain([WARFARIN_ASPIRIN], 4, [])
    second = explainer.explain([WARFARIN_ASPIRIN], 4, [])

    assert first.source == "template"
    assert second.source == "llm"
    assert calls["n"] == 2


def test_preview_prompt는_호출하지_않는다():
    """근거만 만들어 돌려준다. 결제 전에 프롬프트를 검토하기 위한 것."""
    from app.core.explain import preview_prompt

    system, user = preview_prompt([WARFARIN_ASPIRIN], 4, ["모르는것"])

    assert "근거에 있는 것만 쓴다" in system
    assert "와파린" in user
    assert "모르는것" in user
