"""采集/岗位相关表（统一到 SQLite 单库）。

M1 地基整改：原本这些表在捆绑 PostgreSQL，现与候选人 PII 表同处一个 SQLite 库
（`ResumeBase.metadata`），从而 position_id 等可用真 FK，且移除了捆绑 PG 的全部复杂度。
类型已转为 SQLite 兼容（JSONB→JSON、BigInteger→Integer、去 timezone）。

仅保留 5 张活表：PlatformAccount / CrawlTask / RawResume / BossCandidateRecord / JobPosition。
原先重复的 PG candidate/resume/export/audit 家族已删除（死代码）。
"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, synonym

from recruitment_assistant.storage.db import Base
from recruitment_assistant.storage.tenancy import OwnedMixin, TenantMixin


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class PlatformAccount(Base, TimestampMixin):
    __tablename__ = "platform_account"
    __table_args__ = (UniqueConstraint("platform_code", "account_name", name="uk_platform_account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    account_name: Mapped[str] = mapped_column(String(128), nullable=False)
    login_state_path: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime)
    remark: Mapped[str | None] = mapped_column(Text)


class CrawlTask(TenantMixin, Base, TimestampMixin):
    __tablename__ = "crawl_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_account_id: Mapped[int | None] = mapped_column(ForeignKey("platform_account.id"))
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True, default="pending")
    query_keyword: Mapped[str | None] = mapped_column(String(255))
    query_city: Mapped[str | None] = mapped_column(String(128))
    query_params: Mapped[dict | None] = mapped_column(JSON)
    planned_count: Mapped[int | None] = mapped_column(Integer)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    error_message: Mapped[str | None] = mapped_column(Text)


class RawResume(Base):
    __tablename__ = "raw_resume"
    __table_args__ = (UniqueConstraint("platform_code", "source_resume_id", name="uk_raw_resume_platform_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_account_id: Mapped[int | None] = mapped_column(ForeignKey("platform_account.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_task.id"), index=True)
    source_resume_id: Mapped[str | None] = mapped_column(String(128))
    source_candidate_id: Mapped[str | None] = mapped_column(String(128))
    source_url: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    raw_html_path: Mapped[str | None] = mapped_column(String(512))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parsed_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BossCandidateRecord(TenantMixin, Base):
    """采集期去重记录（三平台共用，按 platform_code 区分，名称含 boss 只是历史）。"""
    __tablename__ = "boss_candidate_record"
    __table_args__ = (UniqueConstraint("platform_code", "candidate_key", name="uk_boss_platform_candidate_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    target_site: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_signature: Mapped[str | None] = mapped_column(String(512))
    name: Mapped[str | None] = mapped_column(String(128), index=True)
    gender: Mapped[str | None] = mapped_column(String(16))
    job_title: Mapped[str | None] = mapped_column(String(255), index=True)
    talking_position: Mapped[str | None] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(String(64))
    resume_file_name: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    raw_resume_id: Mapped[int | None] = mapped_column(ForeignKey("raw_resume.id"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_task.id"), index=True)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class JobPosition(OwnedMixin, Base, TimestampMixin):
    __tablename__ = "job_position"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # -- UI-facing fields --
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    department: Mapped[str | None] = mapped_column(String(255))
    work_city: Mapped[str | None] = mapped_column(String(128))
    salary_range: Mapped[str | None] = mapped_column(String(100))
    min_education: Mapped[str | None] = mapped_column(String(64))
    min_experience: Mapped[str | None] = mapped_column(String(64))

    # -- 岗位职责 & 任职要求 --
    responsibilities: Mapped[str | None] = mapped_column(Text)
    job_requirements: Mapped[str | None] = mapped_column(Text)

    required_skills: Mapped[list | None] = mapped_column(JSON)
    preferred_skills: Mapped[list | None] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(Text)
    source_file_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    # 向后兼容属性访问
    position_id = synonym("id")

    @property
    def create_time(self) -> datetime:
        return self.created_at
