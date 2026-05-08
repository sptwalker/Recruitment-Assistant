from sqlalchemy import select
from sqlalchemy.orm import Session

from recruitment_assistant.schemas.job import JobPositionCreate
from recruitment_assistant.storage.models import JobPosition


class JobService:
    def __init__(self, session: Session):
        self.session = session

    def create_job(self, data: JobPositionCreate) -> JobPosition:
        job = JobPosition(**data.model_dump())
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    def list_jobs(self, keyword: str | None = None, limit: int = 100) -> list[JobPosition]:
        stmt = select(JobPosition).where(JobPosition.deleted_at.is_(None)).order_by(JobPosition.id.desc())
        if keyword:
            stmt = stmt.where(JobPosition.job_name.ilike(f"%{keyword}%"))
        return list(self.session.scalars(stmt.limit(limit)).all())
