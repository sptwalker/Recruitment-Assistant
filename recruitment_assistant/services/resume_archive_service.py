"""简历归档数据库 Service 层。"""

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from recruitment_assistant.schemas.resume_archive import CandidateCreate
from recruitment_assistant.storage.resume_models import (
    Candidate,
    Education,
    Honor,
    InterviewEvaluation,
    JobIntention,
    JobPosition,
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
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        stmt = stmt.order_by(Candidate.candidate_id.desc()).offset((page - 1) * page_size).limit(page_size)
        return list(self.session.scalars(stmt).all()), total

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

    # --- 岗位 CRUD ---

    def create_position(self, title: str, department: str = "", requirements: str = "", salary_range: str = "", work_city: str = "") -> JobPosition:
        pos = JobPosition(title=title, department=department, requirements=requirements, salary_range=salary_range, work_city=work_city)
        self.session.add(pos)
        self.session.commit()
        self.session.refresh(pos)
        return pos

    def list_positions(self, status: str | None = None) -> list[JobPosition]:
        stmt = select(JobPosition)
        if status:
            stmt = stmt.where(JobPosition.status == status)
        stmt = stmt.order_by(JobPosition.position_id.desc())
        return list(self.session.scalars(stmt).all())

    def delete_position(self, position_id: int) -> bool:
        pos = self.session.get(JobPosition, position_id)
        if not pos:
            return False
        self.session.delete(pos)
        self.session.commit()
        return True

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
        return ev

    def list_interview_evals(self, candidate_id: int | None = None) -> list[InterviewEvaluation]:
        stmt = select(InterviewEvaluation)
        if candidate_id:
            stmt = stmt.where(InterviewEvaluation.candidate_id == candidate_id)
        stmt = stmt.order_by(InterviewEvaluation.eval_id.desc())
        return list(self.session.scalars(stmt).all())
