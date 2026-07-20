"""简历库备份 / 导出。

crown-jewel（data/resume_archive.db，存全部候选人 PII）无自动备份，卸载可能误删。
本模块提供应用内一键备份与候选人 Excel 导出，供「系统设置 → 数据备份」使用。
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from recruitment_assistant.storage.resume_db import RESUME_DB_PATH

BACKUP_DIR = Path("data/backups")


def backup_resume_db(now: datetime | None = None) -> Path:
    """把 resume_archive.db 复制到 data/backups/resume_archive_<时间戳>.db，返回备份路径。

    now 可注入（测试用）；默认取当前时间。
    """
    src = Path(RESUME_DB_PATH)
    if not src.exists():
        raise FileNotFoundError(f"简历数据库不存在：{src}")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"resume_archive_{stamp}.db"
    shutil.copy2(src, dest)
    return dest


def list_backups() -> list[dict]:
    """列出已有备份，按时间倒序：[{name, path, size_kb, mtime}]。"""
    if not BACKUP_DIR.exists():
        return []
    items: list[dict] = []
    for f in sorted(BACKUP_DIR.glob("resume_archive_*.db"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        stt = f.stat()
        items.append({
            "name": f.name,
            "path": str(f),
            "size_kb": round(stt.st_size / 1024, 1),
            "mtime": datetime.fromtimestamp(stt.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return items
