"""简历归档数据库 Pydantic v2 Schema。"""

import re
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict


def _coerce_partial_date(value: Any) -> Any:
    """把 AI/简历常见的不完整日期字符串补成 YYYY-MM-DD。

    支持的输入：
      - "2021-09"        → date(2021, 9, 1)        年-月，补 1 号
      - "2021/09"        → date(2021, 9, 1)        年/月，补 1 号
      - "2021"           → date(2021, 1, 1)        只有年份，补 1 月 1 日
      - "2021.09"        → date(2021, 9, 1)        年.月
      - "2021-09-15"     → 原样返回，让 pydantic 自己解析
      - "至今" / "现在"  → None（仍在职）
      - "" / None / "null" → None
      - 完整 date 对象   → 原样返回
    """
    if value is None or isinstance(value, (date, datetime)):
        return value
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text or text.lower() in {"null", "none", "n/a", "na"}:
        return None
    # 在职 / 至今类标记 → 视为未结束
    if text in {"至今", "现在", "在职", "present", "Present", "PRESENT", "now", "Now"}:
        return None

    # YYYY-MM 或 YYYY/MM 或 YYYY.MM 补成 1 号
    m = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})月?", text)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-01"

    # 只有年份 YYYY → 1 月 1 日
    m = re.fullmatch(r"(\d{4})年?", text)
    if m:
        return f"{int(m.group(1)):04d}-01-01"

    # 否则原样交给 pydantic（它能解析 YYYY-MM-DD 等标准格式）
    return text


# 通用类型别名：date | None，且能容忍年-月 / 年 这类不完整输入
PartialDate = Annotated[date | None, BeforeValidator(_coerce_partial_date)]


class EducationCreate(BaseModel):
    # school_name 放宽为 Optional：AI 偶尔只识别到学历层次/专业但没识别到学校名，
    # 不应让整份简历入库失败
    school_name: str | None = None
    education_level: str | None = None
    major: str | None = None
    degree: str | None = None
    start_date: PartialDate = None
    end_date: PartialDate = None
    is_full_time: int = 1


class WorkExperienceCreate(BaseModel):
    # company_name 放宽为 Optional：AI 偶尔从文本片段里识别不到公司名
    company_name: str | None = None
    industry: str | None = None
    position: str | None = None
    start_date: PartialDate = None
    end_date: PartialDate = None
    job_content: str | None = None
    is_main_job: int = 1


class ProjectExperienceCreate(BaseModel):
    # project_name 放宽为 Optional：AI 偶尔只提取到项目描述但没标题
    project_name: str | None = None
    project_role: str | None = None
    project_date: str | None = None
    project_desc: str | None = None
    project_duty: str | None = None
    project_result: str | None = None


class SkillCertificateCreate(BaseModel):
    skill_type: str | None = None
    skill_name: str | None = None
    proficiency: str | None = None
    is_core: int = 0


class JobIntentionCreate(BaseModel):
    target_position: str | None = None
    target_city: str | None = None
    expected_salary: str | None = None
    job_status: str | None = None


class HonorCreate(BaseModel):
    # honor_name 放宽为 Optional：AI 偶尔只识别到荣誉级别没识别到名字
    honor_name: str | None = None
    honor_date: PartialDate = None
    honor_level: str | None = None


class ResumeSourceCreate(BaseModel):
    source_platform: str | None = None
    file_name: str | None = None
    file_type: str | None = None
    file_path: str | None = None
    attachment_works_path: str | None = None
    crawl_time: datetime | None = None
    is_duplicate: int = 0


class SystemEvaluationCreate(BaseModel):
    total_score: int | None = None
    grade_level: str | None = None
    match_position: str | None = None
    match_degree: str | None = None
    tags: str | None = None
    interview_status: str | None = None
    interview_time: datetime | None = None
    operator: str | None = None
    remark: str | None = None


class CandidateCreate(BaseModel):
    name: str
    gender: str | None = None
    age: int | None = None
    birth_date: PartialDate = None
    phone: str | None = None
    email: str | None = None
    wechat: str | None = None
    current_city: str | None = None
    education_level: str | None = None
    self_intro: str | None = None
    educations: list[EducationCreate] = []
    work_experiences: list[WorkExperienceCreate] = []
    project_experiences: list[ProjectExperienceCreate] = []
    skills: list[SkillCertificateCreate] = []
    job_intention: JobIntentionCreate | None = None
    honors: list[HonorCreate] = []
    resume_source: ResumeSourceCreate | None = None
    system_evaluation: SystemEvaluationCreate | None = None


class CandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    candidate_id: int
    name: str
    gender: str | None = None
    age: int | None = None
    phone: str | None = None
    email: str | None = None
    current_city: str | None = None
    education_level: str | None = None
    create_time: datetime | None = None
    update_time: datetime | None = None
