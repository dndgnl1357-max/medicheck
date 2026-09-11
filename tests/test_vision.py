"""사진 인식 테스트.

OCR 은 폴백이 없다. 못 읽으면 못 읽었다고 해야지, 빈 목록을 성공처럼
돌려주면 사용자는 "약이 없다" 로 오해한다.
"""

import io
import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_photo_reader
from app.core.explain import ChatUnavailable
from app.core.vision import MAX_IMAGE_BYTES, PhotoReader
from app.main import app

JPEG = "image/jpeg"


def reply(items=None, note=""):
    return json.dumps({"items": items or [], "note": note}, ensure_ascii=False)


def reader_with(response):
    def vision(system, image_b64, mime):
        if isinstance(response, Exception):
            raise response
        return response

    return PhotoReader(vision, model="gpt-4o")


# --- 꺼져 있을 때 -------------------------------------------------------------


def test_키가_없으면_available_false():
    result = PhotoReader(None).read(b"x", JPEG)
    assert result.available is False
    assert "직접 입력" in result.reason
    assert result.items == []


def test_크레딧이_없으면_사유를_그대로_전한다():
    result = reader_with(ChatUnavailable("OpenAI 크레딧이 없습니다.")).read(b"x", JPEG)
    assert result.available is False
    assert "크레딧" in result.reason


def test_호출이_실패해도_예외가_새지_않는다():
    result = reader_with(RuntimeError("서버 오류")).read(b"x", JPEG)
    assert result.available is False


def test_읽을_수_없는_응답이면_실패로_본다():
    """빈 목록을 성공처럼 돌려주면 사용자가 '약이 없다'로 오해한다."""
    result = reader_with("JSON 아님").read(b"x", JPEG)
    assert result.available is False


# --- 입력 검증 ---------------------------------------------------------------


def test_너무_큰_이미지는_거부한다():
    result = reader_with(reply()).read(b"x" * (MAX_IMAGE_BYTES + 1), JPEG)
    assert result.available is False
    assert "너무 큽니다" in result.reason


def test_지원하지_않는_형식은_거부한다():
    result = reader_with(reply()).read(b"x", "application/pdf")
    assert result.available is False
    assert "형식" in result.reason


def test_빈_이미지는_거부한다():
    assert reader_with(reply()).read(b"", JPEG).available is False


# --- 정상 인식 ---------------------------------------------------------------


def test_읽은_이름을_돌려준다():
    result = reader_with(
        reply([{"name": "타이레놀정500밀리그램", "kind": "product", "confidence": 0.9}])
    ).read(b"x", JPEG)

    assert result.available is True
    assert result.names == ["타이레놀정500밀리그램"]
    assert result.items[0].kind == "product"


def test_하나도_못_읽으면_빈_목록이지만_available_true():
    """호출은 됐고 사진에 약이 없었던 경우. 실패와 구분되어야 한다."""
    result = reader_with(reply([], note="글씨가 흐립니다")).read(b"x", JPEG)
    assert result.available is True
    assert result.items == []
    assert result.note == "글씨가 흐립니다"


def test_중복된_이름은_한_번만():
    result = reader_with(
        reply([
            {"name": "타이레놀", "kind": "product", "confidence": 0.9},
            {"name": "타이레놀", "kind": "product", "confidence": 0.8},
        ])
    ).read(b"x", JPEG)
    assert result.names == ["타이레놀"]


def test_이상한_kind는_product로_보정한다():
    result = reader_with(
        reply([{"name": "약", "kind": "이상한값", "confidence": 0.5}])
    ).read(b"x", JPEG)
    assert result.items[0].kind == "product"


def test_confidence는_0에서_1로_제한된다():
    result = reader_with(
        reply([
            {"name": "가", "kind": "product", "confidence": 9},
            {"name": "나", "kind": "product", "confidence": -3},
        ])
    ).read(b"x", JPEG)
    assert [i.confidence for i in result.items] == [1.0, 0.0]


def test_상한을_넘는_항목은_자른다():
    from app.core.vision import MAX_ITEMS

    many = [
        {"name": f"약{n}", "kind": "product", "confidence": 0.5}
        for n in range(MAX_ITEMS + 10)
    ]
    result = reader_with(reply(many)).read(b"x", JPEG)
    assert len(result.items) == MAX_ITEMS


# --- 엔드포인트 --------------------------------------------------------------


client = TestClient(app)


def test_사진_엔드포인트는_키_없이도_200():
    """인식이 안 되는 것과 서버가 죽는 것은 다르다."""
    r = client.post(
        "/api/photo", files={"image": ("x.jpg", io.BytesIO(b"fake"), JPEG)}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["reason"]


def test_사진_엔드포인트가_읽은_이름을_돌려준다():
    app.dependency_overrides[get_photo_reader] = lambda: reader_with(
        reply([{"name": "타이레놀", "kind": "product", "confidence": 0.95}])
    )
    try:
        r = client.post(
            "/api/photo", files={"image": ("x.jpg", io.BytesIO(b"fake"), JPEG)}
        )
    finally:
        app.dependency_overrides.clear()

    body = r.json()
    assert body["available"] is True
    assert body["items"][0]["name"] == "타이레놀"
    # 읽기만 하고 분석하지 않는다 — 사용자 확인을 거쳐야 한다
    assert "overall_risk" not in body


def test_health가_사진_상태를_알려준다():
    assert "photo_enabled" in client.get("/api/health").json()
