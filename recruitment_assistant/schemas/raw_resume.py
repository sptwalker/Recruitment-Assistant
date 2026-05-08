from pydantic import BaseModel, ConfigDict


class RawResumeCreate(BaseModel):
    platform_code: str
    source_resume_id: str | None = None
    source_candidate_id: str | None = None
    source_url: str | None = None
    raw_json: dict | None = None
    raw_html_path: str | None = None
    content_hash: str
    parsed_status: str = "pending"


class RawResumeRead(RawResumeCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
