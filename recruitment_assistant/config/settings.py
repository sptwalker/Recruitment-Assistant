from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    # DB 连接串。空 → 本地 SQLite（见 storage.resume_db.resolve_db_url）；
    # 设 postgresql+psycopg://… → 多用户 PG 部署。pydantic 自动读同名环境变量 DATABASE_URL。
    database_url: str = ""
    # 前端 SPA 源（CORS，携带 cookie）。
    frontend_origin: str = "http://localhost:5173"
    playwright_headless: bool = False
    crawler_min_interval_seconds: int = 8
    crawler_max_interval_seconds: int = 30
    crawler_max_resumes_per_task: int = 50
    export_dir: Path = Field(default=Path("data/exports"))
    attachment_dir: Path = Field(default=Path("data/attachments"))
    browser_state_dir: Path = Field(default=Path("data/browser_state"))
    snapshot_dir: Path = Field(default=Path("data/snapshots"))
    log_level: str = "INFO"

    # AI 大模型配置（兼容 OpenAI 格式，支持 DeepSeek / 通义千问）
    ai_api_key: str = ""
    ai_base_url: str = "https://api.deepseek.com/v1"
    ai_model: str = "deepseek-chat"

    def ensure_local_dirs(self) -> None:
        for path in [
            self.export_dir,
            self.attachment_dir,
            self.browser_state_dir,
            self.snapshot_dir,
            Path("logs"),
        ]:
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_local_dirs()
    return settings
