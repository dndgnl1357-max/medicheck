"""FastAPI 진입점.

    uvicorn app.main:app --reload
    → http://127.0.0.1:8000/docs
"""

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes import router
from app.config import PROJECT_ROOT, get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s | %(message)s")

settings = get_settings()

app = FastAPI(
    title="Medicheck API",
    version=__version__,
    description="시민 맞춤형 복약 안전 가이드 — 성분 기반 상호작용 위험도 분석",
)

# 개발 중에는 전부 허용. 배포 전에 실제 프론트 도메인으로 좁힐 것.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.app_env == "development" else [],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

@app.get("/.well-known/assetlinks.json", include_in_schema=False)
def assetlinks() -> JSONResponse:
    """안드로이드 앱과 이 도메인이 한 짝임을 증명한다.

    TWA 는 이 파일의 지문이 설치된 APK 의 서명과 일치할 때만 주소창 없이
    전체화면으로 뜬다. 일치하지 않으면 주소창 달린 브라우저 탭으로 열린다
    (동작은 하지만 앱처럼 보이지 않는다).

    StaticFiles mount 보다 **앞에** 선언해야 한다.
    """
    settings = get_settings()
    prints = [f.strip() for f in settings.android_cert_fingerprints.split(",") if f.strip()]
    return JSONResponse([
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": settings.android_package_name,
                "sha256_cert_fingerprints": prints,
            },
        }
    ])


# 데모 화면. API 라우터보다 뒤에 mount 해야 "/" 가 /api, /docs 를 삼키지 않는다.
# 데모는 로직을 JS 로 옮겨 심지 않고 위 엔드포인트를 그대로 호출한다 —
# 성분 매칭·위험도 산출의 사본이 하나 더 생기면 조용히 어긋나기 시작한다.
WEB_DIR = PROJECT_ROOT / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
