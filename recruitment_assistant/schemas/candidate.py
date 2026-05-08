from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CandidateCreate(BaseModel):
    name: str
    gender: str | None = None
    age: int | None = None
    phone_plain: str | None = None
    email_plain: str | None = None
    current_city: str | None = None
    highest_degree: str | None = None
    years_of_experience: Decimal | None = None
    current_company: str | None = None
    current_position: str | None = None


class CandidateRead(CandidateCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phone_masked: str | None = None
    email_masked: str | None = None
    status: str
