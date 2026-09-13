"""환경설정. .env 파일에서 값을 읽는다."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    data_dir: str = "data"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    data_go_kr_service_key: str = ""

    # Supabase — 로그인에만 쓴다. 복약 정보는 서버로 보내지 않는다.
    #
    # anon 키는 브라우저에 노출되도록 설계된 공개 키다. service_role 키는
    # 절대 여기 넣지 마라 — RLS 를 우회하는 관리자 키이고, /api/config 로
    # 그대로 새어 나간다.
    supabase_url: str = ""
    supabase_anon_key: str = ""

    # 안드로이드 TWA 앱의 서명 지문(SHA-256, 콜론 구분, 쉼표로 여러 개).
    # /.well-known/assetlinks.json 으로 공개되며, 이게 맞아야 앱이 주소창 없이
    # 전체화면으로 뜬다. **Play 스토어에 올리면 구글이 다시 서명하므로
    # 지문이 바뀐다** — 그때 Play Console 의 지문을 여기에 추가해야 한다.
    android_cert_fingerprints: str = (
        "56:69:1E:27:D4:78:0D:25:1F:B0:55:74:5C:31:8E:FD:"
        "AA:3D:86:9D:4B:0F:85:49:60:7F:3E:38:F6:76:42:F0"
    )
    android_package_name: str = "kr.medicheck.app"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_anon_key)

    # 성분명 매칭에서 오탈자를 같은 성분으로 볼 최소 유사도 (0~1)
    fuzzy_cutoff: float = 0.8

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def seed_path(self) -> Path:
        return self.data_path / "seed"

    @property
    def processed_path(self) -> Path:
        return self.data_path / "processed"


@lru_cache
def get_settings() -> Settings:
    return Settings()
