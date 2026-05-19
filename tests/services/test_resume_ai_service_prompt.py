"""SYSTEM_PROMPT 字段覆盖回归。

不调真实 API，只校验 prompt 文本里枚举了 schema 所有保留字段。
任一字段被遗漏都会让 AI 不写该字段，导致 0% 填充率回归。
"""

from recruitment_assistant.services.resume_ai_service import SYSTEM_PROMPT


REQUIRED_FIELDS = [
    # candidates
    "name", "gender", "age", "birth_date", "phone", "email", "wechat",
    "current_city", "education_level", "self_intro",
    # education
    "school_name", "major", "degree", "start_date", "end_date", "is_full_time",
    # work
    "company_name", "industry", "position", "job_content",
    # project
    "project_name", "project_role", "project_desc", "project_result",
    # skills
    "skill_type", "skill_name", "proficiency",
    # intention
    "target_position", "target_city", "expected_salary", "job_status",
    # honors
    "honor_name", "honor_level",
]


def test_prompt_enumerates_every_kept_field():
    missing = [f for f in REQUIRED_FIELDS if f not in SYSTEM_PROMPT]
    assert not missing, f"prompt 缺失字段：{missing}"


def test_prompt_explicitly_forbids_dropped_fields():
    """schema 减肥后这些字段不应该再出现在 prompt 中。"""
    dropped = [
        "qq", "native_place", "political_status", "ethnicity", "height",
        "main_courses", "company_type", "department", "job_level",
        "work_duration", "performance", "manage_scope",
        "project_industry", "certificate_org", "get_date",
        "work_nature", "arrival_time", "industry_prefer", "issue_by",
    ]
    leaked = [f for f in dropped if f in SYSTEM_PROMPT]
    assert not leaked, f"prompt 仍引用已删除字段：{leaked}"


def test_prompt_has_age_calculation_rule():
    """age 是审计中重大缺失字段（48% 空），prompt 必须显式要求按生日推算。"""
    # 使用动态年份避免每年改测试；只要 prompt 包含 "减去出生年" 这条规则就通过
    assert "减去出生年" in SYSTEM_PROMPT, \
        "缺少年龄推算规则，会让 AI 在简历只给生日时漏填 age"


def test_prompt_has_skills_merge_rule():
    """合并规则：避免 skills 人均 8.79 条的拆条问题。"""
    assert "合并" in SYSTEM_PROMPT and "skill" in SYSTEM_PROMPT.lower(), \
        "缺少技能合并规则，会让 AI 把 'Python、Java' 拆成两条"


def test_prompt_has_education_level_vs_degree_rule():
    """区分学历层次和具体学位。"""
    assert "education_level" in SYSTEM_PROMPT and "degree" in SYSTEM_PROMPT, \
        "education_level 与 degree 区别说明缺失"
