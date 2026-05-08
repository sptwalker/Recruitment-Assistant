from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.utils.hash_utils import text_hash


def safe_filename(value: str, max_length: int = 80) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    cleaned = cleaned.strip("._") or "page"
    return cleaned[:max_length]


def build_snapshot_path(platform_code: str, url: str, suffix: str = "html") -> Path:
    settings = get_settings()
    now = datetime.now()
    host = safe_filename(urlparse(url).netloc or "unknown")
    name_hash = text_hash(url)[:12] if text_hash(url) else "unknown"
    filename = f"{now.strftime('%H%M%S')}_{host}_{name_hash}.{suffix}"
    return settings.snapshot_dir / platform_code / now.strftime("%Y%m%d") / filename


def save_text_snapshot(platform_code: str, url: str, content: str, suffix: str = "html") -> Path:
    path = build_snapshot_path(platform_code, url, suffix=suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
