"""简历归档数据库 SQLAlchemy 模型（独立 SQLite）。

9 张表：candidates / education / work_experience / project_experience /
skills_certificates / job_intention / honors / resume_source / system_evaluation
"""

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from recruitment_assistant.storage.resume_db import ResumeBase


class Candidate(ResumeBase):
    __tablename__ = "candidates"
    __table_args__ = (
        UniqueConstraint("name", "age", "education_level", name="uq_candidate_dedup_fallback"),
    )

    candidate_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(10))
    age: Mapped[int | None] = mapped_column(Integer)
    birth_date: Mapped[date | None] = mapped_column(Date)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    email: Mapped[str | None] = mapped_column(String(100))
    wechat: Mapped[str | None] = mapped_column(String(50))
    current_city: Mapped[str | None] = mapped_column(String(50))
    education_level: Mapped[str | None] = mapped_column(String(20))
    self_intro: Mapped[str | None] = mapped_column(Text)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    educations: Mapped[list["Education"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    work_experiences: Mapped[list["WorkExperience"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    project_experiences: Mapped[list["ProjectExperience"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    skills: Mapped[list["SkillCertificate"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    job_intention: Mapped["JobIntention | None"] = relationship(back_populates="candidate", uselist=False, cascade="all, delete-orphan")
    honors: Mapped[list["Honor"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    resume_source: Mapped["ResumeSource | None"] = relationship(back_populates="candidate", uselist=False, cascade="all, delete-orphan")
    system_evaluation: Mapped["SystemEvaluation | None"] = relationship(back_populates="candidate", uselist=False, cascade="all, delete-orphan")


class Education(ResumeBase):
    __tablename__ = "education"

    edu_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id", ondelete="CASCADE"), nullable=False, index=True)
    school_name: Mapped[str] = mapped_column(String(100), nullable=False)
    education_level: Mapped[str | None] = mapped_column(String(20), index=True)
    major: Mapped[str | None] = mapped_column(String(100))
    degree: Mapped[str | None] = mapped_column(String(20))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_full_time: Mapped[int] = mapped_column(Integer, default=1)

    candidate: Mapped["Candidate"] = relationship(back_populates="educations")


class WorkExperience(ResumeBase):
    __tablename__ = "work_experience"

    work_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id", ondelete="CASCADE"), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(100), index=True)
    position: Mapped[str | None] = mapped_column(String(100), index=True)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    job_content: Mapped[str | None] = mapped_column(Text)
    is_main_job: Mapped[int] = mapped_column(Integer, default=1)

    candidate: Mapped["Candidate"] = relationship(back_populates="work_experiences")


class ProjectExperience(ResumeBase):
    __tablename__ = "project_experience"

    project_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id", ondelete="CASCADE"), nullable=False, index=True)
    project_name: Mapped[str] = mapped_column(String(200), nullable=False)
    project_role: Mapped[str | None] = mapped_column(String(100))
    project_date: Mapped[str | None] = mapped_column(String(50))
    project_desc: Mapped[str | None] = mapped_column(Text)
    project_duty: Mapped[str | None] = mapped_column(Text)
    project_result: Mapped[str | None] = mapped_column(Text)

    candidate: Mapped["Candidate"] = relationship(back_populates="project_experiences")


class SkillCertificate(ResumeBase):
    __tablename__ = "skills_certificates"

    skill_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id", ondelete="CASCADE"), nullable=False, index=True)
    skill_type: Mapped[str | None] = mapped_column(String(20))
    skill_name: Mapped[str | None] = mapped_column(String(100), index=True)
    proficiency: Mapped[str | None] = mapped_column(String(20))
    is_core: Mapped[int] = mapped_column(Integer, default=0)

    candidate: Mapped["Candidate"] = relationship(back_populates="skills")


class JobIntention(ResumeBase):
    __tablename__ = "job_intention"

    intention_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    target_position: Mapped[str | None] = mapped_column(String(100), index=True)
    target_city: Mapped[str | None] = mapped_column(String(50))
    expected_salary: Mapped[str | None] = mapped_column(String(50))
    job_status: Mapped[str | None] = mapped_column(String(30))

    candidate: Mapped["Candidate"] = relationship(back_populates="job_intention")


class Honor(ResumeBase):
    __tablename__ = "honors"

    honor_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id", ondelete="CASCADE"), nullable=False, index=True)
    honor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    honor_date: Mapped[date | None] = mapped_column(Date)
    honor_level: Mapped[str | None] = mapped_column(String(20))

    candidate: Mapped["Candidate"] = relationship(back_populates="honors")


class ResumeSource(ResumeBase):
    __tablename__ = "resume_source"

    source_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    source_platform: Mapped[str | None] = mapped_column(String(30))
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_type: Mapped[str | None] = mapped_column(String(10))
    file_path: Mapped[str | None] = mapped_column(Text)
    crawl_time: Mapped[datetime | None] = mapped_column(DateTime)
    is_duplicate: Mapped[int] = mapped_column(Integer, default=0)

    candidate: Mapped["Candidate"] = relationship(back_populates="resume_source")


class SystemEvaluation(ResumeBase):
    __tablename__ = "system_evaluation"

    eval_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    total_score: Mapped[int | None] = mapped_column(Integer, index=True)
    grade_level: Mapped[str | None] = mapped_column(String(5), index=True)
    match_position: Mapped[str | None] = mapped_column(String(100))
    match_degree: Mapped[str | None] = mapped_column(String(20))
    tags: Mapped[str | None] = mapped_column(Text)
    interview_status: Mapped[str | None] = mapped_column(String(20))
    interview_time: Mapped[datetime | None] = mapped_column(DateTime)
    operator: Mapped[str | None] = mapped_column(String(50))
    remark: Mapped[str | None] = mapped_column(Text)

    candidate: Mapped["Candidate"] = relationship(back_populates="system_evaluation")


class JobPosition(ResumeBase):
    __tablename__ = "job_positions"

    position_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100))
    requirements: Mapped[str | None] = mapped_column(Text)
    salary_range: Mapped[str | None] = mapped_column(String(50))
    work_city: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="open")
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class InterviewEvaluation(ResumeBase):
    __tablename__ = "interview_evaluations"

    eval_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.candidate_id"), nullable=False, index=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("job_positions.position_id"))
    interviewer: Mapped[str | None] = mapped_column(String(50))
    interview_round: Mapped[str | None] = mapped_column(String(20))
    score: Mapped[int | None] = mapped_column(Integer)
    strengths: Mapped[str | None] = mapped_column(Text)
    weaknesses: Mapped[str | None] = mapped_column(Text)
    conclusion: Mapped[str | None] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(Text)
    interview_time: Mapped[datetime | None] = mapped_column(DateTime)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
