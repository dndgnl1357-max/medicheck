"""FastAPI 진입점.

    uvicorn app.main:app --reload
    → http://127.0.0.1:8000/docs
"""

import logging

from fastapi import FastAPI
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

# 데모 화면. API 라우터보다 뒤에 mount 해야 "/" 가 /api, /docs 를 삼키지 않는다.
# 데모는 로직을 JS 로 옮겨 심지 않고 위 엔드포인트를 그대로 호출한다 —
# 성분 매칭·위험도 산출의 사본이 하나 더 생기면 조용히 어긋나기 시작한다.
WEB_DIR = PROJECT_ROOT / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
