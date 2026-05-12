from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/recruitment_assistant"
    playwright_headless: bool = False
    crawler_min_interval_seconds: int = 8
    crawler_max_interval_seconds: int = 30
    crawler_max_resumes_per_task: int = 50
    export_dir: Path = Field(default=Path("data/exports"))
    attachment_dir: Path = Field(default=Path("data/attachments"))
    browser_state_dir: Path = Field(default=Path("data/browser_state"))
    snapshot_dir: Path = Field(default=Path("data/snapshots"))
    log_level: str = "INFO"

    # Boss CDP browser settings
    chrome_executable_path: str | None = None
    boss_cdp_port: int = 9222

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
