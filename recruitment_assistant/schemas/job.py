from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class JobPositionCreate(BaseModel):
    job_name: str
    department: str | None = None
    city: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    degree_requirement: str | None = None
    experience_min_years: Decimal | None = None
    experience_max_years: Decimal | None = None
    required_skills: list[str] | None = None
    preferred_skills: list[str] | None = None
    description: str | None = None
    source_file_name: str | None = None


class JobPositionRead(JobPositionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
