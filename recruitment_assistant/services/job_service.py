from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from recruitment_assistant.schemas.job import JobPositionCreate
from recruitment_assistant.storage.models import JobPosition


class JobService:
    def __init__(self, session: Session):
        self.session = session

    # -- Create --

    def create_job(self, data: JobPositionCreate) -> JobPosition:
        job = JobPosition(**data.model_dump())
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    def create_position(
        self,
        title: str,
        department: str = "",
        work_city: str = "",
        salary_range: str = "",
        min_education: str | None = None,
        min_experience: str | None = None,
        responsibilities: str = "",
        job_requirements: str = "",
        source_file_name: str | None = None,
    ) -> JobPosition:
        """Create a position using keyword args (drop-in replacement for ResumeArchiveService)."""
        pos = JobPosition(
            title=title,
            department=department or None,
            work_city=work_city or None,
            salary_range=salary_range or None,
            min_education=min_education,
            min_experience=min_experience,
            responsibilities=responsibilities or None,
            job_requirements=job_requirements or None,
            source_file_name=source_file_name,
        )
        self.session.add(pos)
        self.session.commit()
        self.session.refresh(pos)
        return pos

    # -- Read --

    def get_by_id(self, position_id: int) -> JobPosition | None:
        return self.session.get(JobPosition, position_id)

    def list_jobs(self, keyword: str | None = None, limit: int = 100) -> list[JobPosition]:
        stmt = select(JobPosition).where(JobPosition.deleted_at.is_(None)).order_by(JobPosition.id.desc())
        if keyword:
            stmt = stmt.where(JobPosition.title.ilike(f"%{keyword}%"))
        return list(self.session.scalars(stmt.limit(limit)).all())

    def list_positions(self, status: str | None = None) -> list[JobPosition]:
        """List positions, optionally filtered by status. Compatible with ResumeArchiveService API."""
        stmt = select(JobPosition).where(JobPosition.deleted_at.is_(None))
        if status:
            stmt = stmt.where(JobPosition.status == status)
        stmt = stmt.order_by(JobPosition.id.desc())
        return list(self.session.scalars(stmt).all())

    def count_positions(self) -> int:
        """Count non-deleted positions (for dashboard stats)."""
        return (
            self.session.scalar(
                select(func.count(JobPosition.id)).where(JobPosition.deleted_at.is_(None))
            )
            or 0
        )

    # -- Update --

    def update_position(self, position_id: int, **fields) -> bool:
        """Update a position by ID. Returns True if a row was updated."""
        stmt = (
            update(JobPosition)
            .where(JobPosition.id == position_id, JobPosition.deleted_at.is_(None))
            .values(**fields)
        )
        result = self.session.execute(stmt)
        self.session.commit()
        return result.rowcount > 0

    # -- Delete (soft) --

    def delete_position(self, position_id: int) -> bool:
        """Soft-delete a position by setting deleted_at."""
        pos = self.session.get(JobPosition, position_id)
        if not pos or pos.deleted_at is not None:
            return False
        pos.deleted_at = datetime.now(timezone.utc)
        self.session.commit()
        return True
