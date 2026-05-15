from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from recruitment_assistant.storage.db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlatformAccount(Base, TimestampMixin):
    __tablename__ = "platform_account"
    __table_args__ = (UniqueConstraint("platform_code", "account_name", name="uk_platform_account"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    account_name: Mapped[str] = mapped_column(String(128), nullable=False)
    login_state_path: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remark: Mapped[str | None] = mapped_column(Text)


class CrawlTask(Base, TimestampMixin):
    __tablename__ = "crawl_task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform_account_id: Mapped[int | None] = mapped_column(ForeignKey("platform_account.id"))
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True, default="pending")
    query_keyword: Mapped[str | None] = mapped_column(String(255))
    query_city: Mapped[str | None] = mapped_column(String(128))
    query_params: Mapped[dict | None] = mapped_column(JSONB)
    planned_count: Mapped[int | None] = mapped_column(Integer)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)


class CrawlTaskLog(Base):
    __tablename__ = "crawl_task_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("crawl_task.id"), index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class RawResume(Base):
    __tablename__ = "raw_resume"
    __table_args__ = (UniqueConstraint("platform_code", "source_resume_id", name="uk_raw_resume_platform_source"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_account_id: Mapped[int | None] = mapped_column(ForeignKey("platform_account.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_task.id"), index=True)
    source_resume_id: Mapped[str | None] = mapped_column(String(128))
    source_candidate_id: Mapped[str | None] = mapped_column(String(128))
    source_url: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[dict | None] = mapped_column(JSONB)
    raw_html_path: Mapped[str | None] = mapped_column(String(512))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parsed_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlatformCandidateRecord(Base):
    __tablename__ = "platform_candidate_record"
    __table_args__ = (UniqueConstraint("platform_code", "candidate_key", name="uk_platform_candidate_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    target_site: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_signature: Mapped[str | None] = mapped_column(String(512))
    name: Mapped[str | None] = mapped_column(String(128), index=True)
    gender: Mapped[str | None] = mapped_column(String(16))
    job_title: Mapped[str | None] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(64))
    resume_file_name: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    raw_resume_id: Mapped[int | None] = mapped_column(ForeignKey("raw_resume.id"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_task.id"), index=True)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BossCandidateRecord(Base):
    __tablename__ = "boss_candidate_record"
    __table_args__ = (UniqueConstraint("platform_code", "candidate_key", name="uk_boss_platform_candidate_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    target_site: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_signature: Mapped[str | None] = mapped_column(String(512))
    name: Mapped[str | None] = mapped_column(String(128), index=True)
    gender: Mapped[str | None] = mapped_column(String(16))
    job_title: Mapped[str | None] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(64))
    resume_file_name: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    raw_resume_id: Mapped[int | None] = mapped_column(ForeignKey("raw_resume.id"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_task.id"), index=True)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Candidate(Base, TimestampMixin):
    __tablename__ = "candidate"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    gender: Mapped[str | None] = mapped_column(String(16))
    birth_year: Mapped[int | None] = mapped_column(Integer)
    age: Mapped[int | None] = mapped_column(Integer)
    phone_plain: Mapped[str | None] = mapped_column(String(64))
    phone_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    phone_masked: Mapped[str | None] = mapped_column(String(64))
    email_plain: Mapped[str | None] = mapped_column(String(128))
    email_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    email_masked: Mapped[str | None] = mapped_column(String(128))
    current_city: Mapped[str | None] = mapped_column(String(128))
    highest_degree: Mapped[str | None] = mapped_column(String(64))
    years_of_experience: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    current_company: Mapped[str | None] = mapped_column(String(255))
    current_position: Mapped[str | None] = mapped_column(String(255))
    dedup_key: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new", index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    resumes: Mapped[list["Resume"]] = relationship(back_populates="candidate")


class Resume(Base, TimestampMixin):
    __tablename__ = "resume"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate.id"), index=True)
    raw_resume_id: Mapped[int | None] = mapped_column(ForeignKey("raw_resume.id"))
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    resume_title: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text)
    expected_position: Mapped[str | None] = mapped_column(String(255), index=True)
    expected_city: Mapped[str | None] = mapped_column(String(128))
    expected_salary_min: Mapped[int | None] = mapped_column(Integer)
    expected_salary_max: Mapped[int | None] = mapped_column(Integer)
    expected_industry: Mapped[str | None] = mapped_column(String(255))
    job_status: Mapped[str | None] = mapped_column(String(64))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_status: Mapped[str] = mapped_column(String(32), nullable=False, default="new", index=True)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    candidate: Mapped[Candidate] = relationship(back_populates="resumes")


class WorkExperience(Base):
    __tablename__ = "work_experience"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resume.id"), index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    position_name: Mapped[str | None] = mapped_column(String(255))
    department: Mapped[str | None] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(255))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(Text)
    achievements: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EducationExperience(Base):
    __tablename__ = "education_experience"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resume.id"), index=True)
    school_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    major: Mapped[str | None] = mapped_column(String(255))
    degree: Mapped[str | None] = mapped_column(String(64))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectExperience(Base):
    __tablename__ = "project_experience"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resume.id"), index=True)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role_name: Mapped[str | None] = mapped_column(String(255))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    responsibility: Mapped[str | None] = mapped_column(Text)
    achievement: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResumeSkill(Base):
    __tablename__ = "resume_skill"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resume.id"), index=True)
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    skill_level: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResumeTag(Base):
    __tablename__ = "resume_tag"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resume.id"), index=True)
    tag_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tag_type: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResumeAttachment(Base):
    __tablename__ = "resume_attachment"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resume.id"), index=True)
    raw_resume_id: Mapped[int | None] = mapped_column(ForeignKey("raw_resume.id"))
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_ext: Mapped[str | None] = mapped_column(String(32))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    download_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobPosition(Base, TimestampMixin):
    __tablename__ = "job_position"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    department: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(128))
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    degree_requirement: Mapped[str | None] = mapped_column(String(64))
    experience_min_years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    experience_max_years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    required_skills: Mapped[list | None] = mapped_column(JSONB)
    preferred_skills: Mapped[list | None] = mapped_column(JSONB)
    description: Mapped[str | None] = mapped_column(Text)
    source_file_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResumeScore(Base):
    __tablename__ = "resume_score"
    __table_args__ = (UniqueConstraint("resume_id", "job_position_id", name="uk_resume_score"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resume.id"), index=True)
    job_position_id: Mapped[int] = mapped_column(ForeignKey("job_position.id"), index=True)
    total_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    skill_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    experience_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    education_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    salary_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    city_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    evaluation: Mapped[str | None] = mapped_column(Text)
    matched_points: Mapped[dict | None] = mapped_column(JSONB)
    missing_points: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExportRecord(Base):
    __tablename__ = "export_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    export_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    filters: Mapped[dict | None] = mapped_column(JSONB)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ExportRecordItem(Base):
    __tablename__ = "export_record_item"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    export_record_id: Mapped[int] = mapped_column(ForeignKey("export_record.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate.id"), index=True)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resume.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OperationAuditLog(Base):
    __tablename__ = "operation_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
