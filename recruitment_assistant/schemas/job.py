from pydantic import BaseModel, ConfigDict


class JobPositionCreate(BaseModel):
    title: str
    department: str | None = None
    work_city: str | None = None
    salary_range: str | None = None
    min_education: str | None = None
    min_experience: str | None = None
    responsibilities: str | None = None
    job_requirements: str | None = None
    required_skills: list[str] | None = None
    preferred_skills: list[str] | None = None
    description: str | None = None
    source_file_name: str | None = None


class JobPositionRead(JobPositionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
