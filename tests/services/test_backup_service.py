"""backup_service：备份文件生成 + 列表。"""
from datetime import datetime

import pytest

from recruitment_assistant.services import backup_service


def test_backup_creates_file_and_lists(temp_resume_db, monkeypatch):
    _SessionLocal, db_path = temp_resume_db
    # 让备份目录也落在临时区
    backup_dir = db_path.parent / "backups"
    monkeypatch.setattr(backup_service, "BACKUP_DIR", backup_dir, raising=True)

    dest = backup_service.backup_resume_db(now=datetime(2026, 7, 20, 9, 30, 0))
    assert dest.exists()
    assert dest.name == "resume_archive_20260720_093000.db"

    listed = backup_service.list_backups()
    assert any(b["name"] == dest.name for b in listed)
    assert listed[0]["size_kb"] >= 0


def test_backup_missing_source_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_service, "RESUME_DB_PATH", tmp_path / "nope.db", raising=True)
    monkeypatch.setattr(backup_service, "BACKUP_DIR", tmp_path / "backups", raising=True)
    with pytest.raises(FileNotFoundError):
        backup_service.backup_resume_db()


def test_list_backups_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_service, "BACKUP_DIR", tmp_path / "backups", raising=True)
    assert backup_service.list_backups() == []
