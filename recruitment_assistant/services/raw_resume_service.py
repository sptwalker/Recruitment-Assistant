from sqlalchemy import select
from sqlalchemy.orm import Session

from recruitment_assistant.schemas.raw_resume import RawResumeCreate
from recruitment_assistant.storage.models import RawResume


class RawResumeService:
    def __init__(self, session: Session):
        self.session = session

    def create_raw_resume(self, data: RawResumeCreate) -> RawResume:
        existing = self.session.scalar(
            select(RawResume).where(RawResume.content_hash == data.content_hash).limit(1)
        )
        if existing:
            return existing

        raw_resume = RawResume(**data.model_dump())
        self.session.add(raw_resume)
        self.session.commit()
        self.session.refresh(raw_resume)
        return raw_resume

    def list_raw_resumes(self, limit: int = 100) -> list[RawResume]:
        stmt = select(RawResume).order_by(RawResume.id.desc()).limit(limit)
        return list(self.session.scalars(stmt).all())
