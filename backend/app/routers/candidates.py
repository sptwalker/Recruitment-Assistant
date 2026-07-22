"""候选人路由（M2.3）。tenant_ctx 保证租户/归属过滤：recruiter 只见自己盖章的候选人，
admin/manager 见整租户。异租户/他人候选人 get 返回 None → 404，改删同样够不到（fail-closed）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.deps import get_db, tenant_ctx
from recruitment_assistant.schemas.resume_archive import CandidateCreate, CandidateRead
from recruitment_assistant.services.resume_archive_service import ResumeArchiveService

router = APIRouter(prefix="/candidates", tags=["candidates"], dependencies=[Depends(tenant_ctx)])


def _svc(db: Session = Depends(get_db)) -> ResumeArchiveService:
    return ResumeArchiveService(db)


@router.get("", response_model=dict)
def list_candidates(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    name: str | None = None,
    city: str | None = None,
    education_level: str | None = None,
    platform: str | None = None,
    favorite_only: bool = False,
    svc: ResumeArchiveService = Depends(_svc),
) -> dict:
    items, total = svc.list_candidates(
        page=page, page_size=page_size, name=name, city=city,
        education_level=education_level, platform=platform, favorite_only=favorite_only,
    )
    return {
        "total": total,
        "items": [CandidateRead.model_validate(c) for c in items],
    }


@router.get("/{candidate_id}", response_model=CandidateRead)
def get_candidate(candidate_id: int, svc: ResumeArchiveService = Depends(_svc)) -> CandidateRead:
    c = svc.get_candidate(candidate_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "候选人不存在")
    return CandidateRead.model_validate(c)


@router.post("", response_model=CandidateRead, status_code=status.HTTP_201_CREATED)
def create_candidate(data: CandidateCreate, svc: ResumeArchiveService = Depends(_svc)) -> CandidateRead:
    return CandidateRead.model_validate(svc.create_candidate(data))


@router.delete("/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_candidate(candidate_id: int, svc: ResumeArchiveService = Depends(_svc)) -> None:
    # delete_candidate 内部 get 已走租户过滤：够不到（异租户/他人）→ False → 404
    if not svc.delete_candidate(candidate_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "候选人不存在")
