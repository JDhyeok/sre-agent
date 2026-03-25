"""환경 설정 — 폐쇄망 대응. 외부 서비스 의존성 없음."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Data directory (SQLite files)
    data_directory: str = "./data"

    # Claude API (내부 프록시 경유)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    anthropic_base_url: str | None = None  # 내부 프록시 URL (예: http://llm-proxy.internal:8080)

    # App
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    # Correlator
    match_window_seconds: int = 60
    correlator_poll_interval: float = 2.0  # 이벤트 큐 폴링 간격 (초)
    stale_edge_days: int = 7

    # Skills
    skills_directory: str = "./skills"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
