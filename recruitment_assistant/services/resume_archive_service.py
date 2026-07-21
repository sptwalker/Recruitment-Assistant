"""简历归档数据库 Service 层。"""

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from recruitment_assistant.schemas.resume_archive import CandidateCreate
from recruitment_assistant.storage.resume_models import (
    Candidate,
    Education,
    Honor,
    InterviewEvaluation,
    InterviewInvitation,
    InterviewOutline,
    JobIntention,
    PositionMatch,
    ProjectExperience,
    ResumeSource,
    SkillCertificate,
    SystemEvaluation,
    WorkExperience,
)


class ResumeArchiveService:
    def __init__(self, session: Session):
        self.session = session

    def is_duplicate(
        self,
        phone: str | None = None,
        name: str | None = None,
        age: int | None = None,
        education_level: str | None = None,
    ) -> bool:
        if phone:
            exists = self.session.scalar(
                select(Candidate.candidate_id).where(Candidate.phone == phone).limit(1)
            )
            if exists:
                return True
        if name and (age is not None or education_level):
            stmt = select(Candidate.candidate_id).where(Candidate.name == name)
            if age is not None:
                stmt = stmt.where(Candidate.age == age)
            if education_level:
                stmt = stmt.where(Candidate.education_level == education_level)
            if self.session.scalar(stmt.limit(1)):
                return True
        return False

    def create_candidate(self, data: CandidateCreate) -> Candidate:
        candidate = Candidate(
            name=data.name,
            gender=data.gender,
            age=data.age,
            birth_date=data.birth_date,
            phone=data.phone,
            email=data.email,
            wechat=data.wechat,
            current_city=data.current_city,
            education_level=data.education_level,
            self_intro=data.self_intro,
        )
        for edu in data.educations:
            # 学校名缺失的教育条目入库意义不大，且 ORM school_name NOT NULL，直接丢弃
            if not edu.school_name:
                continue
            candidate.educations.append(Education(**edu.model_dump()))
        for work in data.work_experiences:
            # 公司名缺失的工作经历同理跳过
            if not work.company_name:
                continue
            candidate.work_experiences.append(WorkExperience(**work.model_dump()))
        for proj in data.project_experiences:
            # 项目名缺失的项目经历同理跳过
            if not proj.project_name:
                continue
            candidate.project_experiences.append(ProjectExperience(**proj.model_dump()))
        for skill in data.skills:
            candidate.skills.append(SkillCertificate(**skill.model_dump()))
        if data.job_intention:
            candidate.job_intention = JobIntention(**data.job_intention.model_dump())
        for honor in data.honors:
            # 荣誉名缺失同理跳过
            if not honor.honor_name:
                continue
            candidate.honors.append(Honor(**honor.model_dump()))
        if data.resume_source:
            candidate.resume_source = ResumeSource(**data.resume_source.model_dump())
        if data.system_evaluation:
            candidate.system_evaluation = SystemEvaluation(**data.system_evaluation.model_dump())

        self.session.add(candidate)
        self.session.commit()
        self.session.refresh(candidate)
        return candidate

    def get_candidate(self, candidate_id: int) -> Candidate | None:
        return self.session.get(Candidate, candidate_id)

    def search_candidates(
        self,
        name: str | None = None,
        city: str | None = None,
        education_level: str | None = None,
        limit: int = 50,
    ) -> list[Candidate]:
        stmt = select(Candidate)
        if name:
            stmt = stmt.where(Candidate.name.contains(name))
        if city:
            stmt = stmt.where(Candidate.current_city.contains(city))
        if education_level:
            stmt = stmt.where(Candidate.education_level == education_level)
        stmt = stmt.order_by(Candidate.candidate_id.desc()).limit(limit)
        return list(self.session.scalars(stmt).all())

    def get_stats(self) -> dict:
        total = self.session.scalar(select(func.count(Candidate.candidate_id))) or 0
        platform_counts: dict[str, int] = {}
        rows = self.session.execute(
            select(ResumeSource.source_platform, func.count(ResumeSource.source_id))
            .group_by(ResumeSource.source_platform)
        ).all()
        for platform, count in rows:
            platform_counts[platform or "未知"] = count
        return {"total": total, "platform_counts": platform_counts}

    # --- 分页浏览 ---

    def list_candidates(
        self,
        page: int = 1,
        page_size: int = 20,
        name: str | None = None,
        city: str | None = None,
        education_level: str | None = None,
        platform: str | None = None,
        favorite_only: bool = False,
    ) -> tuple[list[Candidate], int]:
        stmt = select(Candidate)
        if name:
            stmt = stmt.where(Candidate.name.contains(name))
        if city:
            stmt = stmt.where(Candidate.current_city.contains(city))
        if education_level:
            stmt = stmt.where(Candidate.education_level == education_level)
        if platform:
            stmt = stmt.join(ResumeSource).where(ResumeSource.source_platform == platform)
        if favorite_only:
            stmt = stmt.where(Candidate.is_favorite == 1)
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        stmt = stmt.order_by(Candidate.candidate_id.desc()).offset((page - 1) * page_size).limit(page_size)
        return list(self.session.scalars(stmt).all()), total

    def export_candidates(self) -> list[dict]:
        """全量导出候选人扁平记录，供 Excel 导出用（不分页）。"""
        stmt = (
            select(Candidate)
            .options(selectinload(Candidate.resume_source))
            .order_by(Candidate.candidate_id.desc())
        )
        rows: list[dict] = []
        for c in self.session.scalars(stmt).all():
            src = c.resume_source
            rows.append({
                "ID": c.candidate_id,
                "姓名": c.name or "",
                "性别": c.gender or "",
                "年龄": c.age or "",
                "电话": c.phone or "",
                "邮箱": c.email or "",
                "微信": c.wechat or "",
                "城市": c.current_city or "",
                "学历": c.education_level or "",
                "来源平台": (src.source_platform if src else ""),
                "简历文件": (src.file_name if src else ""),
                "归档时间": (src.crawl_time.strftime("%Y-%m-%d %H:%M:%S")
                             if src and src.crawl_time else ""),
                "关注": "是" if c.is_favorite else "",
            })
        return rows

    # --- 删除 / 屏蔽 ---

    def delete_candidate(self, candidate_id: int) -> bool:
        candidate = self.session.get(Candidate, candidate_id)
        if not candidate:
            return False
        self.session.delete(candidate)
        self.session.commit()
        return True

    def update_candidate_field(self, candidate_id: int, **fields) -> bool:
        stmt = update(Candidate).where(Candidate.candidate_id == candidate_id).values(**fields)
        result = self.session.execute(stmt)
        self.session.commit()
        return result.rowcount > 0

    # --- 岗位 CRUD 已迁移到 JobService (PostgreSQL) ---
    # create_position / update_position / list_positions / delete_position
    # 请使用 recruitment_assistant.services.job_service.JobService

    # --- 面试评价 CRUD ---

    def create_interview_eval(
        self,
        candidate_id: int,
        position_id: int | None = None,
        interviewer: str = "",
        interview_round: str = "",
        score: int | None = None,
        strengths: str = "",
        weaknesses: str = "",
        conclusion: str = "",
        notes: str = "",
        interview_time=None,
    ) -> InterviewEvaluation:
        ev = InterviewEvaluation(
            candidate_id=candidate_id,
            position_id=position_id,
            interviewer=interviewer,
            interview_round=interview_round,
            score=score,
            strengths=strengths,
            weaknesses=weaknesses,
            conclusion=conclusion,
            notes=notes,
            interview_time=interview_time,
        )
        self.session.add(ev)
        self.session.commit()
        self.session.refresh(ev)
        try:
            from recruitment_assistant.services.monitoring import record_operation
            _cand = self.session.get(Candidate, candidate_id)
            _name = _cand.name if _cand else str(candidate_id)
            record_operation("面试评价", target=f"{_name}·{interview_round or '面试'}",
                             status=conclusion or "已保存",
                             detail=f"评分{score}" if score is not None else "")
        except Exception:
            pass
        return ev

    def list_interview_evals(self, candidate_id: int | None = None) -> list[InterviewEvaluation]:
        stmt = select(InterviewEvaluation)
        if candidate_id:
            stmt = stmt.where(InterviewEvaluation.candidate_id == candidate_id)
        stmt = stmt.order_by(InterviewEvaluation.eval_id.desc())
        return list(self.session.scalars(stmt).all())

    def delete_interview_evals(self, candidate_id: int) -> int:
        result = self.session.execute(
            delete(InterviewEvaluation).where(InterviewEvaluation.candidate_id == candidate_id)
        )
        self.session.commit()
        return result.rowcount

    # --- 面试邀约 CRUD ---

    def has_pending_invitation(self, candidate_id: int) -> bool:
        """检测候选人是否已有 pending 邀约（用于发起邀约前去重）。"""
        stmt = (
            select(InterviewInvitation.invitation_id)
            .where(
                InterviewInvitation.candidate_id == candidate_id,
                InterviewInvitation.status == "pending",
            )
            .limit(1)
        )
        return self.session.scalar(stmt) is not None

    def create_invitation(
        self,
        candidate_id: int,
        position_id: int | None = None,
        notes: str = "",
    ) -> InterviewInvitation:
        inv = InterviewInvitation(
            candidate_id=candidate_id,
            position_id=position_id,
            notes=notes or None,
            status="pending",
        )
        self.session.add(inv)
        self.session.commit()
        self.session.refresh(inv)
        try:
            from recruitment_assistant.services.monitoring import record_operation
            _cand = self.session.get(Candidate, candidate_id)
            record_operation("面试邀约", target=(_cand.name if _cand else str(candidate_id)),
                             status="已发起")
        except Exception:
            pass
        return inv

    def get_invitation(self, invitation_id: int) -> InterviewInvitation | None:
        return self.session.get(InterviewInvitation, invitation_id)

    def list_invitations(self, status: str | None = "pending") -> list[InterviewInvitation]:
        """默认只列 pending；status=None 表示全部。"""
        stmt = select(InterviewInvitation)
        if status:
            stmt = stmt.where(InterviewInvitation.status == status)
        stmt = stmt.order_by(InterviewInvitation.invitation_id.desc())
        return list(self.session.scalars(stmt).all())

    def update_invitation_status(self, invitation_id: int, status: str) -> bool:
        """status: pending / completed / cancelled"""
        inv = self.session.get(InterviewInvitation, invitation_id)
        if not inv:
            return False
        inv.status = status
        self.session.commit()
        return True

    # --- 岗位匹配 CRUD ---

    def clear_position_matches(self, position_id: int) -> int:
        result = self.session.execute(
            delete(PositionMatch).where(PositionMatch.position_id == position_id)
        )
        self.session.commit()
        return result.rowcount

    def save_position_match(
        self,
        position_id: int,
        candidate_id: int,
        score: int,
        reason: str,
        dimensions: dict | None = None,
        jd_hash: str | None = None,
    ) -> None:
        """保存岗位匹配结果（M1 后走统一 ORM session，SQLite upsert）。

        candidate_id 已在调用方预验证；position_id 与 job_position 同库，有真 FK。
        用 SQLite ON CONFLICT 在 (position_id, candidate_id) 唯一键上 upsert，
        取代原先的裸 sqlite3 + INSERT OR REPLACE。
        """
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        values = {
            "position_id": position_id,
            "candidate_id": candidate_id,
            "score": score,
            "reason": reason,
            "skill_match": dimensions.get("skill_match") if dimensions else None,
            "experience_match": dimensions.get("experience_match") if dimensions else None,
            "education_match": dimensions.get("education_match") if dimensions else None,
            "location_match": dimensions.get("location_match") if dimensions else None,
            "jd_hash": jd_hash,
        }
        stmt = sqlite_insert(PositionMatch).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["position_id", "candidate_id"],
            set_={
                k: getattr(stmt.excluded, k)
                for k in ("score", "reason", "skill_match", "experience_match",
                          "education_match", "location_match", "jd_hash")
            },
        )
        try:
            self.session.execute(stmt)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def list_position_matches(self, position_id: int, min_score: int = 50) -> list[tuple]:
        stmt = (
            select(PositionMatch, Candidate)
            .join(Candidate, PositionMatch.candidate_id == Candidate.candidate_id)
            .where(PositionMatch.position_id == position_id, PositionMatch.score >= min_score)
            .order_by(PositionMatch.score.desc(), PositionMatch.match_id)
        )
        return list(self.session.execute(stmt).all())

    def get_scored_candidate_ids(self, position_id: int, jd_hash: str) -> set[int]:
        """返回该岗位下已用指定 JD 哈希评分过的候选人 ID 集合。

        用于跳过 JD 未变化、已评分的候选人，避免重复调用 LLM。
        """
        stmt = select(PositionMatch.candidate_id).where(
            PositionMatch.position_id == position_id,
            PositionMatch.jd_hash == jd_hash,
        )
        return {int(cid) for cid in self.session.scalars(stmt) if cid is not None}

    def resume_source_exists(self, file_path: str) -> bool:
        """按简历文件绝对路径判断是否已入库（用于解析前跳过已处理文件）。"""
        return self.session.scalar(
            select(ResumeSource.source_id).where(ResumeSource.file_path == file_path).limit(1)
        ) is not None

    # --- 面试大纲 CRUD ---

    def get_outline(self, invitation_id: int) -> InterviewOutline | None:
        return self.session.scalar(
            select(InterviewOutline).where(InterviewOutline.invitation_id == invitation_id)
        )

    def save_outline(self, invitation_id: int, candidate_id: int, position_id: int | None, content: str) -> InterviewOutline:
        existing = self.get_outline(invitation_id)
        if existing:
            existing.content = content
            existing.update_time = func.now()
            self.session.commit()
            return existing
        outline = InterviewOutline(
            invitation_id=invitation_id,
            candidate_id=candidate_id,
            position_id=position_id,
            content=content,
        )
        self.session.add(outline)
        self.session.commit()
        return outline
