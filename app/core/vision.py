"""약봉투·처방전 사진에서 약 이름을 읽어낸다.

시민이 성분명을 타이핑하는 것은 큰 마찰이다. 약봉투를 찍으면 되는 편이 낫다.

## 이 모듈이 하는 일과 하지 않는 일

**한다**: 사진에 적힌 제품명·성분명 문자열을 읽어 목록으로 돌려준다.
**하지 않는다**: 그 약이 무엇인지 판단하지 않는다. 위험도도 매기지 않는다.

읽어낸 이름은 평소와 똑같이 `AnalysisService` 를 거친다. 우리 데이터에 없으면
`unmatched` 로 드러난다. 즉 사진 인식은 **입력 방법**일 뿐이고, 분석의 근거는
여전히 공공데이터다. 모델이 "이 약은 위험합니다" 라고 말해도 쓰지 않는다.

## 못 읽으면 못 읽었다고 한다

OCR 은 폴백이 없다. 설명 레이어는 템플릿으로 내려앉을 수 있지만, 사진을 못 읽으면
읽은 척할 방법이 없다. 크레딧이 없거나 호출이 실패하면 `available=False` 와
이유를 돌려주고, 사용자는 직접 입력하면 된다. **빈 목록을 성공처럼 돌려주지 않는다.**
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Protocol

from app.core.explain import ChatUnavailable, _explain_openai_error

logger = logging.getLogger(__name__)

# 업로드 상한. 휴대폰 사진 한 장은 보통 2~5MB 다.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic"}

# 한 장에서 뽑을 약 개수 상한. 처방전이라도 이보다 많으면 오인식일 가능성이 높다.
MAX_ITEMS = 20

SYSTEM_PROMPT = """\
너는 약봉투·처방전·의약품 포장 사진에서 **약 이름만** 읽어내는 도구다.

반드시 지킬 것:
1. 사진에 **실제로 적혀 있는 글자만** 읽어라. 흐릿해서 확신이 없으면 그 항목의 confidence 를 낮게 준다.
2. 사진에 없는 약을 추측해서 채우지 마라. 하나도 못 읽었으면 빈 배열을 준다.
3. 위험도를 매기거나, 이 약이 무엇에 쓰이는지 설명하거나, 복용을 권하거나 말리지 마라. 그건 네 일이 아니다.
4. 제품명과 성분명이 둘 다 보이면 둘 다 넣되 kind 로 구분한다.
5. 용량·제형 표기(500mg, 정, 캡슐)는 name 에 그대로 남겨라. 뒤에서 정규화한다.

출력은 JSON 객체 하나. 다른 텍스트를 붙이지 마라.
{
  "items": [
    {"name": "타이레놀정500밀리그램", "kind": "product", "confidence": 0.9}
  ],
  "note": "사진이 흐리다거나 일부를 못 읽었다면 그 사실만 한 문장으로."
}
kind 는 "product"(제품명) 또는 "ingredient"(성분명) 중 하나다.
"""


class VisionClient(Protocol):
    """이미지 한 장을 받아 JSON 문자열을 돌려준다."""

    def __call__(self, system: str, image_b64: str, mime: str) -> str: ...


@dataclass(frozen=True)
class ReadItem:
    name: str
    kind: str  # product | ingredient
    confidence: float


@dataclass(frozen=True)
class ReadResult:
    available: bool
    items: list[ReadItem] = field(default_factory=list)
    note: str = ""
    reason: str = ""  # available=False 일 때의 사유 (사용자에게 보여줄 수 있어야 한다)
    model: str | None = None

    @property
    def names(self) -> list[str]:
        return [i.name for i in self.items]


def _parse(raw: str) -> tuple[list[ReadItem], str]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아닙니다")

    items: list[ReadItem] = []
    seen: set[str] = set()
    for entry in data.get("items") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        kind = str(entry.get("kind", "")).strip()
        if kind not in ("product", "ingredient"):
            kind = "product"
        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        items.append(
            ReadItem(name=name, kind=kind, confidence=max(0.0, min(1.0, confidence)))
        )
        if len(items) >= MAX_ITEMS:
            break

    return items, str(data.get("note", "")).strip()


class PhotoReader:
    def __init__(self, vision: VisionClient | None, *, model: str = "") -> None:
        self._vision = vision
        self._model = model

    @property
    def enabled(self) -> bool:
        return self._vision is not None

    def read(self, image: bytes, mime: str) -> ReadResult:
        if self._vision is None:
            return ReadResult(
                available=False,
                reason=(
                    "사진 인식이 꺼져 있습니다. OPENAI_API_KEY 를 설정하면 켜집니다. "
                    "그동안에는 약 이름을 직접 입력해 주세요."
                ),
            )
        if not image:
            return ReadResult(available=False, reason="이미지가 비어 있습니다.")
        if len(image) > MAX_IMAGE_BYTES:
            mb = MAX_IMAGE_BYTES // (1024 * 1024)
            return ReadResult(
                available=False,
                reason=f"이미지가 너무 큽니다. {mb}MB 이하로 줄여 주세요.",
            )
        if mime not in ALLOWED_TYPES:
            return ReadResult(
                available=False,
                reason=f"지원하지 않는 형식입니다({mime}). JPG 또는 PNG 로 올려 주세요.",
            )

        encoded = base64.b64encode(image).decode("ascii")
        try:
            raw = self._vision(SYSTEM_PROMPT, encoded, mime)
            items, note = _parse(raw)
        except ChatUnavailable as exc:
            logger.warning("사진 인식을 건너뜁니다 — %s", exc)
            return ReadResult(available=False, reason=str(exc))
        except Exception as exc:
            logger.exception("사진 인식 실패")
            return ReadResult(
                available=False,
                reason=f"사진을 읽지 못했습니다: {exc}",
            )

        return ReadResult(available=True, items=items, note=note, model=self._model)


def openai_vision(api_key: str, model: str, *, timeout: float = 60.0) -> VisionClient:
    """openai 패키지를 이 함수 안에서만 import 한다 (explain.openai_chat 과 같은 이유)."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=timeout)

    def vision(system: str, image_b64: str, mime: str) -> str:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "이 사진에서 약 이름을 읽어 주세요.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime};base64,{image_b64}",
                                    "detail": "high",  # 작은 글씨를 읽어야 한다
                                },
                            },
                        ],
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0,  # 읽기 작업이라 창의성이 필요 없다
            )
        except Exception as exc:
            raise ChatUnavailable(_explain_openai_error(exc)) from exc
        return response.choices[0].message.content or ""

    return vision
