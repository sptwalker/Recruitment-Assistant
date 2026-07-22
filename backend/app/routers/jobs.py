"""岗位路由（M2.3）。岗位 OwnedMixin：recruiter 只见自己的，admin/manager 见整租户。
匹配结果先经 get_by_id 确认岗位可达（租户过滤），再列该岗位匹配（PositionMatch 亦租户过滤）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.deps import get_db, tenant_ctx
from recruitment_assistant.schemas.job import JobPositionCreate, JobPositionRead
from recruitment_assistant.services.job_service import JobService
from recruitment_assistant.services.resume_archive_service import ResumeArchiveService

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(tenant_ctx)])


def _svc(db: Session = Depends(get_db)) -> JobService:
    return JobService(db)


@router.get("", response_model=list[JobPositionRead])
def list_jobs(
    keyword: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    svc: JobService = Depends(_svc),
) -> list[JobPositionRead]:
    return [JobPositionRead.model_validate(j) for j in svc.list_jobs(keyword=keyword, limit=limit)]


@router.get("/{position_id}", response_model=JobPositionRead)
def get_job(position_id: int, svc: JobService = Depends(_svc)) -> JobPositionRead:
    job = svc.get_by_id(position_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "岗位不存在")
    return JobPositionRead.model_validate(job)


@router.post("", response_model=JobPositionRead, status_code=status.HTTP_201_CREATED)
def create_job(data: JobPositionCreate, svc: JobService = Depends(_svc)) -> JobPositionRead:
    return JobPositionRead.model_validate(svc.create_job(data))


@router.delete("/{position_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(position_id: int, svc: JobService = Depends(_svc)) -> None:
    # get_by_id 先走租户过滤：够不到 → 404；软删只作用于可达岗位
    if svc.get_by_id(position_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "岗位不存在")
    svc.delete_position(position_id)


@router.get("/{position_id}/matches", response_model=list[dict])
def list_matches(
    position_id: int,
    min_score: int = Query(50, ge=0, le=100),
    svc: JobService = Depends(_svc),
    db: Session = Depends(get_db),
) -> list[dict]:
    if svc.get_by_id(position_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "岗位不存在")
    rows = ResumeArchiveService(db).list_position_matches(position_id, min_score=min_score)
    return [
        {
            "candidate_id": c.candidate_id,
            "name": c.name,
            "score": m.score,
            "reason": m.reason,
            "skill_match": m.skill_match,
            "experience_match": m.experience_match,
            "education_match": m.education_match,
            "location_match": m.location_match,
        }
        for m, c in rows
    ]
