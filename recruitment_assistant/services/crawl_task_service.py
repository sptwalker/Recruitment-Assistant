from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, MetaData, String, Table, Text, func, select
from sqlalchemy.orm import Session

from recruitment_assistant.storage.models import CrawlTask

metadata = MetaData()
platform_candidate_record = Table(
    "platform_candidate_record",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("platform_code", String(32), nullable=False, index=True),
    Column("target_site", String(64), nullable=False, index=True),
    Column("candidate_key", String(64), nullable=False, index=True),
    Column("candidate_signature", String(512)),
    Column("name", String(128), index=True),
    Column("gender", String(16)),
    Column("job_title", String(255), index=True),
    Column("phone", String(64)),
    Column("resume_file_name", String(255)),
    Column("source_url", Text),
    Column("content_hash", String(64), index=True),
    Column("raw_resume_id", BigInteger, index=True),
    Column("task_id", BigInteger, index=True),
    Column("hit_count", Integer, nullable=False, default=1),
    Column("first_seen_at", DateTime(timezone=True), server_default=func.now()),
    Column("last_seen_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now()),
)


class CrawlTaskService:
    def __init__(self, session: Session):
        self.session = session

    def create_task(
        self,
        *,
        platform_code: str,
        task_name: str,
        task_type: str,
        query_params: dict | None = None,
        planned_count: int | None = None,
    ) -> CrawlTask:
        task = CrawlTask(
            platform_code=platform_code,
            task_name=task_name,
            task_type=task_type,
            status="running",
            query_params=query_params,
            planned_count=planned_count,
            started_at=datetime.now(),
        )
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def finish_task(
        self,
        task: CrawlTask,
        *,
        status: str,
        success_count: int = 0,
        failed_count: int = 0,
        error_message: str | None = None,
    ) -> CrawlTask:
        task.status = status
        task.success_count = success_count
        task.failed_count = failed_count
        task.error_message = error_message
        task.finished_at = datetime.now()
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def list_tasks(self, limit: int = 50) -> list[CrawlTask]:
        stmt = select(CrawlTask).order_by(CrawlTask.id.desc()).limit(limit)
        return list(self.session.scalars(stmt).all())

    def success_summary(self) -> tuple[int, int]:
        stmt = select(func.count(CrawlTask.id), func.coalesce(func.sum(CrawlTask.success_count), 0)).where(
            CrawlTask.status == "success"
        )
        task_count, resume_count = self.session.execute(stmt).one()
        return int(task_count or 0), int(resume_count or 0)


class PlatformCandidateRecordService:
    def __init__(self, session: Session):
        self.session = session

    def list_profile_lookup_keys(self, platform_code: str) -> set[str]:
        stmt = select(platform_candidate_record.c.candidate_key).where(
            platform_candidate_record.c.platform_code == platform_code
        )
        return {str(key) for key in self.session.execute(stmt).scalars() if key}

