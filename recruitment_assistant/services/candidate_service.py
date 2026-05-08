from sqlalchemy import select
from sqlalchemy.orm import Session

from recruitment_assistant.schemas.candidate import CandidateCreate
from recruitment_assistant.storage.models import Candidate
from recruitment_assistant.utils.hash_utils import mask_email, mask_phone, text_hash


class CandidateService:
    def __init__(self, session: Session):
        self.session = session

    def create_candidate(self, data: CandidateCreate) -> Candidate:
        candidate = Candidate(
            name=data.name.strip(),
            gender=data.gender,
            age=data.age,
            phone_plain=data.phone_plain,
            phone_hash=text_hash(data.phone_plain),
            phone_masked=mask_phone(data.phone_plain),
            email_plain=data.email_plain,
            email_hash=text_hash(data.email_plain),
            email_masked=mask_email(data.email_plain),
            current_city=data.current_city,
            highest_degree=data.highest_degree,
            years_of_experience=data.years_of_experience,
            current_company=data.current_company,
            current_position=data.current_position,
            dedup_key=text_hash(data.phone_plain or data.email_plain or data.name),
        )
        self.session.add(candidate)
        self.session.commit()
        self.session.refresh(candidate)
        return candidate

    def list_candidates(self, keyword: str | None = None, limit: int = 100) -> list[Candidate]:
        stmt = select(Candidate).where(Candidate.deleted_at.is_(None)).order_by(Candidate.id.desc())
        if keyword:
            stmt = stmt.where(Candidate.name.ilike(f"%{keyword}%"))
        return list(self.session.scalars(stmt.limit(limit)).all())
