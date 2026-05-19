# 简历 AI 解析三轴优化（D 方案）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把简历 AI 解析的字段填充率从当前平均 50% 拉到 80%+，同时砍掉 schema 里 0% 命中率的死字段，再回填已入库 75 人缺失的关键字段。

**Architecture:**
1. **Prompt 重写**：SYSTEM_PROMPT 由概括型改写为字段枚举型，按 schema 实际字段逐项给出语义/示例/缺省值，配合上调 raw_text 截断阈值（8000 → 25000）。
2. **Schema 减肥**：删掉审计报告中 0% 填充率且无业务价值的 19 个字段（schema / ORM model / service / 数据库列 同步删）。
3. **数据治理**：写幂等迁移脚本，统一 `BOSS` → `BOSS直聘`，按减肥后的 schema DROP COLUMN，合并 skills，用 phone 作 key 二次过 AI 补全已入库候选人的 age/gender/current_city。
4. **回归**：抽样跑改造后的解析流程，对比前后填充率；更新开发日志到 V2.17。

**Tech Stack:** Python 3.11、SQLAlchemy 2.x、SQLite、Pydantic v2、OpenAI Python SDK（DeepSeek 兼容）、loguru。

**关键基线（来自 `scripts/audit_resume_db.py` 当前输出）：**
- 候选人 75，教育 91，工作 199，项目 136，技能 659，意向 67，荣誉 112。
- candidates 字段：age 52%、gender 52%、current_city 41%、wechat 11%、qq 0%、籍贯 0%、政治面貌 0%、民族 0%、身高 0%。
- skills 人均 8.79 条（偏多，需合并）。
- 平台命名分裂：智联招聘 52、BOSS直聘 14、51前程无忧 8、**BOSS 1**（孤儿数据）。

---

## 文件清单

### 新建
- `scripts/migrate_resume_db.py` —— 一次性数据治理脚本（幂等，支持 `--dry-run` / `--phase ai-fill`）。
- `scripts/test_parse_one.py` —— 拿一份简历跑解析输出 JSON，回归对比工具。

### 修改
- `recruitment_assistant/services/resume_ai_service.py` —— SYSTEM_PROMPT 重写、`raw_text[:8000]` 上调、`source_platform` 校验枚举。
- `recruitment_assistant/schemas/resume_archive.py` —— 删 19 个字段，剩字段保持 PartialDate 容错。
- `recruitment_assistant/storage/resume_models.py` —— 同步删 ORM 列。
- `recruitment_assistant/services/resume_archive_service.py` —— `create_candidate` 移除已删字段的赋值。
- `app/pages/07_简历管理.py` —— 入库时把 `source_platform` 走规范化函数（防止再写出 `BOSS` 等不规范值）。
- `docs/04_development_log.md` —— 顶部插入 V2.17 章节。

### 不动
- `recruitment_assistant/storage/resume_db.py` —— 引擎层。
- 解析器 `recruitment_assistant/parsers/pdf_resume_parser.py` —— 文本提取层。

---

## Task 0：开工前快照

**Files:**
- Read: `data/resume_archive.db`

- [ ] **Step 0.1：备份数据库**

```bash
cp "data/resume_archive.db" "data/resume_archive.db.backup-20260519"
ls -la "data/resume_archive.db.backup-20260519"
```
Expected: 文件存在，size 与原库相同（401408 bytes）。

- [ ] **Step 0.2：跑一遍审计脚本作为基线**

```bash
python -X utf8 scripts/audit_resume_db.py > data/audit-before.txt 2>&1
head -30 data/audit-before.txt
```
Expected: 第一节"全局规模"显示候选人 75。把这份输出留作改造前基线。

- [ ] **Step 0.3：commit 当前未提交的改动作为安全检查点**

```bash
git status
git add -A
git commit -m "chore: 数据治理 D 方案前的基线快照"
```

---

## Task 1：重写 SYSTEM_PROMPT 全字段枚举

**Files:**
- Modify: `recruitment_assistant/services/resume_ai_service.py:38-65`（SYSTEM_PROMPT 常量）
- Modify: `recruitment_assistant/services/resume_ai_service.py:99`（`raw_text[:8000]` → `raw_text[:25000]`）
- Test: `tests/services/test_resume_ai_service_prompt.py`（新建）

### Step 1.1：写 prompt 单元测试（断言关键字段都在 prompt 里）

- [ ] **Step 1.1：新建测试文件**

```python
# tests/services/test_resume_ai_service_prompt.py
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
    assert "2026" in SYSTEM_PROMPT or "当前年" in SYSTEM_PROMPT, \
        "缺少年龄推算规则，会让 AI 在简历只给生日时漏填 age"


def test_prompt_has_skills_merge_rule():
    """合并规则：避免 skills 人均 8.79 条的拆条问题。"""
    assert "合并" in SYSTEM_PROMPT and "skill" in SYSTEM_PROMPT.lower(), \
        "缺少技能合并规则，会让 AI 把 'Python、Java' 拆成两条"


def test_prompt_has_education_level_vs_degree_rule():
    """区分学历层次和具体学位。"""
    assert "education_level" in SYSTEM_PROMPT and "degree" in SYSTEM_PROMPT, \
        "education_level 与 degree 区别说明缺失"
```

- [ ] **Step 1.2：运行测试，确认全部失败**

```bash
cd "d:/Users/pc/Documents/walker/CodeBuddy/招聘网站助手" && python -X utf8 -m pytest tests/services/test_resume_ai_service_prompt.py -v
```
Expected: 5 个 test 至少 4 个 FAIL（旧 prompt 没有 2026 字样、没有合并规则、缺很多字段；`test_prompt_explicitly_forbids_dropped_fields` 当前会通过因为这些字段本就不在旧 prompt 里）。

### Step 1.3：重写 SYSTEM_PROMPT

- [ ] **Step 1.3：替换 SYSTEM_PROMPT 常量**

打开 `recruitment_assistant/services/resume_ai_service.py:38-65`，把 `SYSTEM_PROMPT = """..."""` 整段替换为：

```python
SYSTEM_PROMPT = """你是一个专业的简历解析助手。把下列简历纯文本结构化为标准 JSON 对象。

# 输出 JSON 字段表

## candidates 主信息
- name (str, 必填)：姓名。中文名/英文名/单字名都要识别。
- gender (str)：性别，只能是 "男" / "女"。从姓名/称谓/简历头部识别。
- age (int)：年龄。识别优先级：(1) 简历明确写"XX岁"取值 (2) 只写出生日期 → 用 2026 减去出生年得到 (3) 简历只写工作年限或毕业年 → 不要硬猜，留 null。
- birth_date (str|null)：出生日期 YYYY-MM-DD，可只到年/月。
- phone (str)：手机号。只保留 11 位数字，去掉所有空格、横线、括号。
- email (str)：邮箱。
- wechat (str)：微信号 / WX。
- current_city (str)：现居城市。注意区别于"籍贯/家乡"——只取候选人当前生活/工作所在地。
- education_level (str)：最高学历层次。只能是 "高中" / "中专" / "大专" / "本科" / "硕士" / "博士" 之一。
- self_intro (str)：自我评价摘要，控制在 80 字内。

## educations[] 教育经历
- school_name (str)：学校名。完整官方名（如"湖南大学"而非"湖大"）。
- education_level (str)：本段学历，枚举同上。
- major (str)：专业。
- degree (str)：具体学位（"工学学士" / "管理学硕士" / "MBA"）。注意：和 education_level 不同——education_level 是层次，degree 是学位名称，简历常省略 degree，留 null 不要硬填。
- start_date / end_date (str|null)：YYYY-MM-DD，缺月份补 01；在读用 null 表示。
- is_full_time (int)：1=全日制，0=非全日制（在职/函授/网教）。默认 1。

## work_experiences[] 工作经历
- company_name (str)：公司名。
- industry (str)：行业（如"互联网"、"制造业"、"金融"）。简历明确写出才填。
- position (str)：职位名（如"高级工程师"）。
- start_date / end_date (str|null)：YYYY-MM-DD，至今/在职 → null。
- job_content (str)：工作内容描述。

## project_experiences[] 项目经历
- project_name (str)：项目名。
- project_role (str)：在项目中的角色（如"项目经理"、"后端开发"）。
- project_desc (str)：项目描述。
- project_result (str)：项目成果/产出。

## skills[] 技能/证书 ★合并规则
**重要：相同 skill_type 的技能要合并到一条记录里**，多个 skill_name 用顿号"、"连接。例如：
- ❌ 错：[{"skill_type":"语言","skill_name":"Python"},{"skill_type":"语言","skill_name":"Java"}]
- ✅ 对：[{"skill_type":"语言","skill_name":"Python、Java、SQL"}]

字段：
- skill_type (str)：分类，只能是 "专业" / "语言" / "工具" / "证书" 之一。
- skill_name (str)：技能/证书名（合并后用"、"连接）。
- proficiency (str)：熟练度，"精通" / "熟练" / "了解"。证书类不写。

## job_intention 求职意向（单对象，非数组）
- target_position (str)：目标岗位。
- target_city (str)：期望工作城市。
- expected_salary (str)：期望薪资（如"15-20K"）。
- job_status (str)：求职状态（"在职-看机会" / "离职-随时到岗" / "应届"）。

## honors[] 荣誉
- honor_name (str)：荣誉名（如"国家奖学金"、"优秀员工"）。
- honor_level (str)：等级，"国家级" / "省级" / "市级" / "校级" / "公司级"。

# 通用规则

1. 输出**严格 JSON 对象**（不是数组），不要 markdown 代码块、不要解释文字。
2. 任何字段无法识别就返回 null，不要编造。
3. 日期统一 YYYY-MM-DD，缺月份用 -01 补齐，"至今/在职" → null。
4. 手机号、邮箱、微信若简历有多个，取主要一个。
5. 数组字段（educations / work_experiences 等）若简历完全没有该信息就返回空数组 []，不是 null。
"""
```

### Step 1.4：上调文本截断阈值

- [ ] **Step 1.4：把 8000 改为 25000**

打开 `recruitment_assistant/services/resume_ai_service.py`，找到第 99 行：

```python
{"role": "user", "content": f"请解析以下简历：\n\n{raw_text[:8000]}"},
```

替换为：

```python
{"role": "user", "content": f"请解析以下简历：\n\n{raw_text[:25000]}"},
```

理由：DeepSeek context 64k，单份 PDF 简历 95% 在 12k 字内，25000 阈值足够覆盖长简历且不至于触发 context 上限（system prompt + 25k user + 输出预算 ~6k 共约 32k）。

### Step 1.5：跑测试看是否全绿

- [ ] **Step 1.5：再次运行 prompt 测试**

```bash
python -X utf8 -m pytest tests/services/test_resume_ai_service_prompt.py -v
```
Expected: 5 个测试全部 PASS。

### Step 1.6：commit

- [ ] **Step 1.6：commit prompt 重写**

```bash
git add recruitment_assistant/services/resume_ai_service.py tests/services/test_resume_ai_service_prompt.py
git commit -m "feat(ai): 重写简历解析 SYSTEM_PROMPT，按字段枚举 + 上调截断到 25k"
```

---

## Task 2：Schema 减肥（删 19 个 0% 字段）

**目标**：审计报告确认这 19 个字段填充率 0% 且无业务价值，从 4 个层面同步删除：
- Pydantic schema (`resume_archive.py`)
- ORM model (`resume_models.py`)
- Service 层赋值 (`resume_archive_service.py`)
- 数据库列（在 Task 3 的迁移脚本里 DROP COLUMN）

**待删字段清单**：

| 表 | 字段 |
|---|---|
| candidates | qq, native_place, political_status, ethnicity, height |
| education | main_courses, honors |
| work_experience | company_type, department, job_level, work_duration, performance, manage_scope |
| project_experience | project_industry |
| skills_certificates | certificate_org, get_date |
| job_intention | work_nature, arrival_time, industry_prefer |
| honors | issue_by |

**Files:**
- Modify: `recruitment_assistant/schemas/resume_archive.py`（删字段）
- Modify: `recruitment_assistant/storage/resume_models.py`（删 ORM 列）
- Modify: `recruitment_assistant/services/resume_archive_service.py:49-94`（删字段赋值）

### Step 2.1：删 Pydantic schema 字段

- [ ] **Step 2.1：编辑 `recruitment_assistant/schemas/resume_archive.py`**

找到 `class EducationCreate`，删除 `main_courses` / `honors` 两行：

```python
# 删前
class EducationCreate(BaseModel):
    school_name: str | None = None
    education_level: str | None = None
    major: str | None = None
    degree: str | None = None
    start_date: PartialDate = None
    end_date: PartialDate = None
    is_full_time: int = 1
    main_courses: str | None = None  # ← 删
    honors: str | None = None         # ← 删

# 删后
class EducationCreate(BaseModel):
    school_name: str | None = None
    education_level: str | None = None
    major: str | None = None
    degree: str | None = None
    start_date: PartialDate = None
    end_date: PartialDate = None
    is_full_time: int = 1
```

`class WorkExperienceCreate`：删 `company_type / department / job_level / work_duration / performance / manage_scope`（保留 industry/position/start_date/end_date/job_content/is_main_job 等）。

```python
class WorkExperienceCreate(BaseModel):
    company_name: str | None = None
    industry: str | None = None
    position: str | None = None
    start_date: PartialDate = None
    end_date: PartialDate = None
    job_content: str | None = None
    is_main_job: int = 1
```

`class ProjectExperienceCreate`：删 `project_industry`：

```python
class ProjectExperienceCreate(BaseModel):
    project_name: str | None = None
    project_role: str | None = None
    project_date: str | None = None
    project_desc: str | None = None
    project_duty: str | None = None
    project_result: str | None = None
```

`class SkillCertificateCreate`：删 `certificate_org / get_date`：

```python
class SkillCertificateCreate(BaseModel):
    skill_type: str | None = None
    skill_name: str | None = None
    proficiency: str | None = None
    is_core: int = 0
```

`class JobIntentionCreate`：删 `work_nature / arrival_time / industry_prefer`：

```python
class JobIntentionCreate(BaseModel):
    target_position: str | None = None
    target_city: str | None = None
    expected_salary: str | None = None
    job_status: str | None = None
```

`class HonorCreate`：删 `issue_by`：

```python
class HonorCreate(BaseModel):
    honor_name: str | None = None
    honor_date: PartialDate = None
    honor_level: str | None = None
```

`class CandidateCreate`：删 `qq / native_place / political_status / ethnicity / height`：

```python
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
```

### Step 2.2：删 ORM 模型字段

- [ ] **Step 2.2：编辑 `recruitment_assistant/storage/resume_models.py`**

`class Candidate`：删除以下 5 行（行号见 grep 输出）：

```python
qq: Mapped[str | None] = mapped_column(String(20))
native_place: Mapped[str | None] = mapped_column(String(50))
political_status: Mapped[str | None] = mapped_column(String(20))
ethnicity: Mapped[str | None] = mapped_column(String(20))
height: Mapped[str | None] = mapped_column(String(20))
```

`class Education`：删除：

```python
main_courses: Mapped[str | None] = mapped_column(Text)
honors: Mapped[str | None] = mapped_column(Text)
```

`class WorkExperience`：删除：

```python
company_type: Mapped[str | None] = mapped_column(String(50))
department: Mapped[str | None] = mapped_column(String(100))
job_level: Mapped[str | None] = mapped_column(String(50))
work_duration: Mapped[int | None] = mapped_column(Integer)
performance: Mapped[str | None] = mapped_column(Text)
manage_scope: Mapped[str | None] = mapped_column(Text)
```

`class ProjectExperience`：删除：

```python
project_industry: Mapped[str | None] = mapped_column(String(100))
```

`class SkillCertificate`：删除：

```python
certificate_org: Mapped[str | None] = mapped_column(String(100))
get_date: Mapped[date | None] = mapped_column(Date)
```

`class JobIntention`：删除：

```python
work_nature: Mapped[str | None] = mapped_column(String(20))
arrival_time: Mapped[str | None] = mapped_column(String(30))
industry_prefer: Mapped[str | None] = mapped_column(String(100))
```

`class Honor`：删除：

```python
issue_by: Mapped[str | None] = mapped_column(String(100))
```

### Step 2.3：清理 service 层赋值

- [ ] **Step 2.3：编辑 `recruitment_assistant/services/resume_archive_service.py:49-66`**

`create_candidate` 方法里把 Candidate 构造改为：

```python
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
    # ... 其余子条目循环不动 ...
```

（删除 `qq / native_place / political_status / ethnicity / height` 5 行赋值。）

### Step 2.4：语法检查

- [ ] **Step 2.4：跑 py_compile 验证三个文件**

```bash
cd "d:/Users/pc/Documents/walker/CodeBuddy/招聘网站助手" && python -m py_compile recruitment_assistant/schemas/resume_archive.py recruitment_assistant/storage/resume_models.py recruitment_assistant/services/resume_archive_service.py && echo OK
```
Expected: 输出 `OK`，无 SyntaxError。

### Step 2.5：跑 prompt 测试看是否还绿

- [ ] **Step 2.5：回归 prompt 测试**

```bash
python -X utf8 -m pytest tests/services/test_resume_ai_service_prompt.py -v
```
Expected: 5/5 PASS。`test_prompt_explicitly_forbids_dropped_fields` 现在变成"刚性"（之前是被动通过），任何字段名残留都会报错。

### Step 2.6：commit

- [ ] **Step 2.6：commit schema 减肥**

```bash
git add recruitment_assistant/schemas/resume_archive.py recruitment_assistant/storage/resume_models.py recruitment_assistant/services/resume_archive_service.py
git commit -m "refactor(schema): 删除 19 个 0% 填充率字段（candidates/education/work/...）"
```

---

## Task 3：数据治理迁移脚本

**目标**：写一个幂等脚本，对现有 75 条数据做：
1. 把 `resume_source.source_platform = 'BOSS'` 改为 `'BOSS直聘'`。
2. 删掉 ORM 已经移除的物理列（DROP COLUMN）。
3. 合并同一候选人 + 同 skill_type 的 skills_certificates 记录。
4. 用 phone 作 key 二次过 AI，补全 candidates 表里 age/gender/current_city 为空的字段（仅当三者都缺至少一项时调用）。

**Files:**
- Create: `scripts/migrate_resume_db.py`

### Step 3.1：搭脚本骨架（含 dry-run / phase 分阶段）

- [ ] **Step 3.1：创建 `scripts/migrate_resume_db.py`**

```python
"""resume_archive.db 数据治理迁移（幂等）。

四个阶段：
  1. fix-platform：BOSS → BOSS直聘
  2. drop-columns：DROP COLUMN 19 个已删字段
  3. merge-skills：同候选人 + 同 skill_type 合并
  4. ai-fill：phone 作 key 跑 AI 补全 candidates 缺失字段

用法：
  python scripts/migrate_resume_db.py --dry-run            # 全部阶段干跑
  python scripts/migrate_resume_db.py                      # 全部阶段执行
  python scripts/migrate_resume_db.py --phase ai-fill      # 单跑 AI 补全
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("data/resume_archive.db")

DROP_COLUMNS = {
    "candidates": ["qq", "native_place", "political_status", "ethnicity", "height"],
    "education": ["main_courses", "honors"],
    "work_experience": [
        "company_type", "department", "job_level",
        "work_duration", "performance", "manage_scope",
    ],
    "project_experience": ["project_industry"],
    "skills_certificates": ["certificate_org", "get_date"],
    "job_intention": ["work_nature", "arrival_time", "industry_prefer"],
    "honors": ["issue_by"],
}


def _open(readonly: bool = False) -> sqlite3.Connection:
    if not DB_PATH.exists():
        sys.exit(f"[FAIL] 数据库不存在：{DB_PATH.resolve()}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def phase_fix_platform(conn: sqlite3.Connection, dry: bool) -> None:
    print("\n[阶段 1] fix-platform: BOSS → BOSS直聘")
    cur = conn.execute(
        "SELECT COUNT(*) FROM resume_source WHERE source_platform = 'BOSS'"
    )
    n = cur.fetchone()[0]
    print(f"  待修正记录数：{n}")
    if n == 0:
        print("  ✓ 已经是规范命名（幂等通过）")
        return
    if dry:
        print("  [dry-run] 跳过实际写入")
        return
    conn.execute(
        "UPDATE resume_source SET source_platform = 'BOSS直聘' WHERE source_platform = 'BOSS'"
    )
    conn.commit()
    print(f"  ✓ 已更新 {n} 条")


def phase_drop_columns(conn: sqlite3.Connection, dry: bool) -> None:
    print("\n[阶段 2] drop-columns: 19 个字段")
    existing_tables = {
        r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, cols in DROP_COLUMNS.items():
        if table not in existing_tables:
            print(f"  [{table}] 表不存在，跳过")
            continue
        existing_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        to_drop = [c for c in cols if c in existing_cols]
        already = [c for c in cols if c not in existing_cols]
        if already:
            print(f"  [{table}] 已删除：{already}（幂等跳过）")
        if not to_drop:
            continue
        print(f"  [{table}] 待删除：{to_drop}")
        if dry:
            print("    [dry-run] 跳过实际 DROP")
            continue
        for col in to_drop:
            try:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
                print(f"    ✓ DROP {col}")
            except sqlite3.OperationalError as exc:
                # SQLite 3.35+ 支持 DROP COLUMN，老版本不支持
                sys.exit(f"    [FAIL] DROP COLUMN 失败：{exc}（需 SQLite 3.35+）")
        conn.commit()


def phase_merge_skills(conn: sqlite3.Connection, dry: bool) -> None:
    print("\n[阶段 3] merge-skills: 同候选人 + 同 skill_type 合并")
    rows = conn.execute("""
        SELECT candidate_id, skill_type, COUNT(*) as n
        FROM skills_certificates
        WHERE skill_type IS NOT NULL
        GROUP BY candidate_id, skill_type
        HAVING n > 1
    """).fetchall()
    print(f"  发现 {len(rows)} 组重复 (candidate_id, skill_type)")
    total_dropped = 0
    for r in rows:
        cid, stype, _n = r["candidate_id"], r["skill_type"], r["n"]
        items = conn.execute(
            "SELECT skill_id, skill_name, proficiency, is_core FROM skills_certificates "
            "WHERE candidate_id=? AND skill_type=? ORDER BY skill_id",
            (cid, stype),
        ).fetchall()
        names = [i["skill_name"] for i in items if i["skill_name"]]
        merged_name = "、".join(dict.fromkeys(names))  # 顺序去重
        # 取首个非空 proficiency 和 is_core
        prof = next((i["proficiency"] for i in items if i["proficiency"]), None)
        core = max((i["is_core"] for i in items), default=0)
        keep_id = items[0]["skill_id"]
        drop_ids = [i["skill_id"] for i in items[1:]]
        print(f"  cid={cid} type={stype} keep #{keep_id} drop {drop_ids} merged='{merged_name}'")
        if dry:
            continue
        conn.execute(
            "UPDATE skills_certificates SET skill_name=?, proficiency=?, is_core=? WHERE skill_id=?",
            (merged_name, prof, core, keep_id),
        )
        conn.execute(
            f"DELETE FROM skills_certificates WHERE skill_id IN ({','.join('?'*len(drop_ids))})",
            drop_ids,
        )
        total_dropped += len(drop_ids)
    if not dry:
        conn.commit()
        print(f"  ✓ 合并完成，物理删除 {total_dropped} 条冗余")


def phase_ai_fill(conn: sqlite3.Connection, dry: bool) -> None:
    print("\n[阶段 4] ai-fill: phone 作 key 调 AI 补全 age/gender/current_city")
    targets = conn.execute("""
        SELECT c.candidate_id, c.name, c.phone, c.age, c.gender, c.current_city,
               rs.file_path
        FROM candidates c
        LEFT JOIN resume_source rs ON rs.candidate_id = c.candidate_id
        WHERE (c.age IS NULL OR c.gender IS NULL OR c.current_city IS NULL)
          AND rs.file_path IS NOT NULL
    """).fetchall()
    print(f"  待补全候选人：{len(targets)}")
    if dry or not targets:
        if dry:
            print("  [dry-run] 跳过 AI 调用")
        return

    # 真实跑 AI
    from recruitment_assistant.config.settings import get_settings
    from recruitment_assistant.parsers.pdf_resume_parser import (
        extract_text_from_docx,
        extract_text_from_pdf,
    )
    from recruitment_assistant.services.resume_ai_service import ResumeAIService

    settings = get_settings()
    if not settings.ai_api_key:
        sys.exit("  [FAIL] AI_API_KEY 未配置，跳过 ai-fill 阶段")
    ai = ResumeAIService(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        model=settings.ai_model,
    )

    updated = 0
    for row in targets:
        cid, fp = row["candidate_id"], row["file_path"]
        path = Path(fp) if fp else None
        if not path or not path.exists():
            print(f"  cid={cid} 文件不存在，跳过：{fp}")
            continue
        suffix = path.suffix.lower()
        try:
            text = extract_text_from_pdf(path) if suffix == ".pdf" else extract_text_from_docx(path)
        except Exception as exc:
            print(f"  cid={cid} 提取文本失败：{exc}")
            continue
        if len(text.strip()) < 50:
            print(f"  cid={cid} 文本过短，跳过")
            continue
        try:
            data = ai.parse_resume_text(text)
        except Exception as exc:
            print(f"  cid={cid} AI 调用异常：{exc}")
            continue
        if not data:
            continue
        new_age = row["age"] if row["age"] is not None else data.age
        new_gender = row["gender"] or data.gender
        new_city = row["current_city"] or data.current_city
        if (new_age, new_gender, new_city) == (row["age"], row["gender"], row["current_city"]):
            continue
        conn.execute(
            "UPDATE candidates SET age=?, gender=?, current_city=? WHERE candidate_id=?",
            (new_age, new_gender, new_city, cid),
        )
        updated += 1
        print(f"  cid={cid} {row['name']} age:{row['age']}→{new_age} "
              f"gender:{row['gender']}→{new_gender} city:{row['current_city']}→{new_city}")
    conn.commit()
    print(f"  ✓ 补全完成，更新 {updated} 条")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--phase",
        choices=["fix-platform", "drop-columns", "merge-skills", "ai-fill", "all"],
        default="all",
    )
    args = parser.parse_args()

    print(f"DB:    {DB_PATH.resolve()}")
    print(f"Phase: {args.phase}")
    print(f"Mode:  {'DRY-RUN' if args.dry_run else 'EXECUTE'}")

    conn = _open()
    try:
        if args.phase in ("fix-platform", "all"):
            phase_fix_platform(conn, args.dry_run)
        if args.phase in ("drop-columns", "all"):
            phase_drop_columns(conn, args.dry_run)
        if args.phase in ("merge-skills", "all"):
            phase_merge_skills(conn, args.dry_run)
        if args.phase in ("ai-fill", "all"):
            phase_ai_fill(conn, args.dry_run)
    finally:
        conn.close()
    print("\n[DONE]")


if __name__ == "__main__":
    main()
```

### Step 3.2：先 dry-run 全跑

- [ ] **Step 3.2：dry-run 验证脚本逻辑**

```bash
cd "d:/Users/pc/Documents/walker/CodeBuddy/招聘网站助手" && python -X utf8 scripts/migrate_resume_db.py --dry-run
```
Expected:
- 阶段 1 报告 `待修正记录数：1`。
- 阶段 2 报告每张表待删字段。
- 阶段 3 报告若干组重复 `(cid, type)`，平均技能 8.79 → 预计降到 3-5。
- 阶段 4 报告待补全候选人数（接近 36）。

### Step 3.3：检查 SQLite 版本支持 DROP COLUMN

- [ ] **Step 3.3：确认 SQLite ≥ 3.35**

```bash
python -c "import sqlite3; print(sqlite3.sqlite_version)"
```
Expected: `3.35.0` 或更高（Python 3.11+ 自带版本通常是 3.39+）。如果低于 3.35，需要回退到"建新表 → INSERT SELECT → 改名"的方案；本计划假设 3.35+。

### Step 3.4：执行迁移

- [ ] **Step 3.4：实跑前 3 个阶段（不含 AI）**

```bash
python -X utf8 scripts/migrate_resume_db.py --phase fix-platform
python -X utf8 scripts/migrate_resume_db.py --phase drop-columns
python -X utf8 scripts/migrate_resume_db.py --phase merge-skills
```
Expected: 每个阶段结尾打印 `✓ ...` 行；不报错。

### Step 3.5：跑 audit 检查中间状态

- [ ] **Step 3.5：审计中间状态**

```bash
python -X utf8 scripts/audit_resume_db.py | head -50
```
Expected:
- 平台分布表里不再有 `BOSS` 行，`BOSS直聘` 计数 +1（变 15）。
- candidates 字段表里看不到 qq/native_place 等列。
- skills 总数从 659 降到 250-400 区间。

### Step 3.6：执行 AI 补全阶段（独立跑，方便观察）

- [ ] **Step 3.6：跑 AI 补全**

```bash
python -X utf8 scripts/migrate_resume_db.py --phase ai-fill 2>&1 | tee data/migrate-ai-fill.log
```
Expected: 36 个候选人被处理，每条打印 `age:None→XX gender:None→男`。耗时约 8-15 分钟。

### Step 3.7：commit migration 脚本

- [ ] **Step 3.7：commit**

```bash
git add scripts/migrate_resume_db.py data/migrate-ai-fill.log
git commit -m "feat(db): 数据治理迁移脚本（修平台命名+DROP 19 列+合并 skills+AI 补全）"
```

---

## Task 4：入库时规范化 source_platform

**目的**：避免未来再出现 `BOSS` / `BOSS直聘` 混用。

**Files:**
- Modify: `recruitment_assistant/services/resume_ai_service.py`（新增模块级常量 `PLATFORM_ENUM`）
- Modify: `app/pages/07_简历管理.py:395-400`（用规范化函数）
- Test: `tests/services/test_platform_normalize.py`

### Step 4.1：写规范化函数 + 测试

- [ ] **Step 4.1：在 resume_ai_service.py 顶部加规范化函数**

在 `_normalize_base_url` 函数下方加：

```python
PLATFORM_ALIAS = {
    "BOSS": "BOSS直聘",
    "boss": "BOSS直聘",
    "Boss": "BOSS直聘",
    "BOSS直聘": "BOSS直聘",
    "智联": "智联招聘",
    "智联招聘": "智联招聘",
    "51": "51前程无忧",
    "51job": "51前程无忧",
    "前程无忧": "51前程无忧",
    "51前程无忧": "51前程无忧",
}
PLATFORM_VALID = {"BOSS直聘", "智联招聘", "51前程无忧"}


def normalize_platform(name: str | None) -> str | None:
    """把 source_platform 规范成 3 个枚举值之一，未知值返回原值。"""
    if not name:
        return name
    return PLATFORM_ALIAS.get(name.strip(), name)
```

- [ ] **Step 4.2：写测试**

```python
# tests/services/test_platform_normalize.py
from recruitment_assistant.services.resume_ai_service import (
    PLATFORM_VALID,
    normalize_platform,
)


def test_boss_aliases_map_to_canonical():
    assert normalize_platform("BOSS") == "BOSS直聘"
    assert normalize_platform("boss") == "BOSS直聘"
    assert normalize_platform("Boss") == "BOSS直聘"


def test_canonical_passes_through():
    for v in PLATFORM_VALID:
        assert normalize_platform(v) == v


def test_unknown_returns_original():
    assert normalize_platform("拉勾") == "拉勾"


def test_none_and_empty():
    assert normalize_platform(None) is None
    assert normalize_platform("") == ""


def test_strips_whitespace():
    assert normalize_platform("  BOSS  ") == "BOSS直聘"
```

```bash
python -X utf8 -m pytest tests/services/test_platform_normalize.py -v
```
Expected: 5/5 PASS。

### Step 4.3：在 07 页入库逻辑里调用规范化

- [ ] **Step 4.3：编辑 `app/pages/07_简历管理.py:395-401`**

找到：

```python
candidate_data.resume_source = ResumeSourceCreate(
    source_platform=platform,
    file_name=fname,
    ...
)
```

改为：

```python
from recruitment_assistant.services.resume_ai_service import normalize_platform

# ... 在文件顶层 import 区把这行加进去 ...

# 入库处：
candidate_data.resume_source = ResumeSourceCreate(
    source_platform=normalize_platform(platform),
    file_name=fname,
    ...
)
```

注：07 页文件顶部已经 `from recruitment_assistant.services.resume_ai_service import ResumeAIService`，把这一行扩成 `from recruitment_assistant.services.resume_ai_service import ResumeAIService, normalize_platform` 即可。

### Step 4.4：commit

- [ ] **Step 4.4：commit**

```bash
git add recruitment_assistant/services/resume_ai_service.py app/pages/07_简历管理.py tests/services/test_platform_normalize.py
git commit -m "feat(ai): 入库时规范化 source_platform，杜绝混用"
```

---

## Task 5：抽样回归 + 填充率对比

**Files:**
- Create: `scripts/test_parse_one.py`

### Step 5.1：建抽样工具脚本

- [ ] **Step 5.1：创建 `scripts/test_parse_one.py`**

```python
"""抽样跑一份简历看结构化结果。

用法：
  python scripts/test_parse_one.py "data/attachments/zhilian/xxx/yyy.pdf"
"""

import json
import sys
from pathlib import Path

from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.parsers.pdf_resume_parser import (
    extract_text_from_docx,
    extract_text_from_pdf,
)
from recruitment_assistant.services.resume_ai_service import ResumeAIService


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("用法：python scripts/test_parse_one.py <简历路径>")
    path = Path(sys.argv[1])
    if not path.exists():
        sys.exit(f"文件不存在：{path}")

    settings = get_settings()
    text = extract_text_from_pdf(path) if path.suffix.lower() == ".pdf" else extract_text_from_docx(path)
    print(f"--- 提取文本 {len(text)} 字符 ---")
    print(text[:500])
    print("...\n")

    ai = ResumeAIService(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        model=settings.ai_model,
    )
    data = ai.parse_resume_text(text)
    if not data:
        sys.exit("AI 返回空")
    print("--- 结构化结果 ---")
    print(json.dumps(data.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

### Step 5.2：选 3 份代表性简历测试

- [ ] **Step 5.2：列出可用简历**

```bash
ls "data/attachments/zhilian"/*/*.pdf 2>/dev/null | head -3
ls "data/attachments/boss"/*/*.pdf 2>/dev/null | head -3
ls "data/attachments/51job"/*/*.pdf 2>/dev/null | head -3
```
记下 3 个文件路径，分别来自三个平台。

- [ ] **Step 5.3：跑 3 份对比**

```bash
python -X utf8 scripts/test_parse_one.py "<智联那份的绝对路径>" 2>&1 | tail -80
python -X utf8 scripts/test_parse_one.py "<BOSS那份的绝对路径>" 2>&1 | tail -80
python -X utf8 scripts/test_parse_one.py "<51那份的绝对路径>" 2>&1 | tail -80
```
Expected：每份输出里 `name / phone / age / gender / current_city / education_level` 都不应是 null（除非简历真没写）；skills 数量 ≤ 5 条；educations/work/honors 数组结构正常。

### Step 5.4：重启 streamlit 跑现有简历目录的回归

- [ ] **Step 5.4：清空数据库并重新入库（可选，用于完整回归）**

如果要做"改造前/后填充率"严肃对比，可以：

```bash
# 备份当前已迁移的库
cp data/resume_archive.db data/resume_archive.db.after-migrate
# 重置成空库
python -c "
from recruitment_assistant.storage.resume_db import RESUME_DB_PATH, init_resume_database
RESUME_DB_PATH.unlink(missing_ok=True)
init_resume_database()
print('reset')
"
```

然后到 Streamlit 07 页"简历自动解析入库"重新入库 75 份；跑完后：

```bash
python -X utf8 scripts/audit_resume_db.py > data/audit-after.txt 2>&1
diff data/audit-before.txt data/audit-after.txt | head -100
```
Expected：candidates 表里 age/gender/current_city 三个字段从 41-52% 提升到 70%+；skills 人均从 8.79 降到 4 以内。

**注**：如果不想丢已 ai-fill 补全的数据，跳过 Step 5.4，仅靠 5.3 抽样验证即可。

### Step 5.5：commit 工具脚本

- [ ] **Step 5.5：commit**

```bash
git add scripts/test_parse_one.py
git commit -m "tools: 加抽样解析对比脚本 test_parse_one"
```

---

## Task 6：开发日志 V2.17

**Files:**
- Modify: `docs/04_development_log.md`（顶部插入新章节）

### Step 6.1：写日志条目

- [ ] **Step 6.1：在 `## 2026-05-18` 上方插入**

```markdown
## 2026-05-19

### V2.17 简历 AI 解析三轴优化（D 方案：prompt + schema + 数据治理）

#### 背景

V2.07 后第一次大规模简历入库 75 份，审计发现：
- candidates 主信息：age/gender 仅 52%、current_city 41%、wechat 11%、qq/籍贯/政治面貌/民族/身高 0%。
- 子表多个字段 0% 填充率（main_courses、performance、manage_scope、industry_prefer 等）。
- skills 人均 8.79 条（拆条严重）；source_platform 出现 `BOSS` 与 `BOSS直聘` 两种命名。
- 36/75 候选人无年龄。

根因：(1) SYSTEM_PROMPT 概括式描述，未枚举字段。(2) `raw_text[:8000]` 截断过早。(3) schema 设计阶段含大量"理论上有用、实际简历不写"的字段。(4) 写库时未规范化平台名。

#### 改动

**Prompt 重写（`recruitment_assistant/services/resume_ai_service.py`）：**
- 用字段表枚举每个保留字段，给出语义/枚举值/示例/缺省规则。
- `age` 显式要求"只有生日时用 2026 减出生年"。
- `skills` 加合并规则："相同 skill_type 的多技能用顿号合并到一条"。
- 区分 `education_level`（学历层次）和 `degree`（具体学位）。
- `raw_text[:8000]` → `raw_text[:25000]`。

**Schema 减肥（`schemas/resume_archive.py` + `storage/resume_models.py` + `services/resume_archive_service.py`）：**

删除 19 个 0% 填充率且无业务价值的字段：

| 表 | 删除字段 |
|---|---|
| candidates | qq, native_place, political_status, ethnicity, height |
| education | main_courses, honors |
| work_experience | company_type, department, job_level, work_duration, performance, manage_scope |
| project_experience | project_industry |
| skills_certificates | certificate_org, get_date |
| job_intention | work_nature, arrival_time, industry_prefer |
| honors | issue_by |

**数据治理脚本（`scripts/migrate_resume_db.py`）：** 4 阶段幂等迁移：
- `fix-platform`: `BOSS` → `BOSS直聘`（1 条）
- `drop-columns`: ALTER TABLE DROP COLUMN（19 列）
- `merge-skills`: 同候选人 + 同 type 合并 skill_name（659 → ~250 估算）
- `ai-fill`: phone 作 key 用现有附件二次过 AI，补 36 个无年龄候选人

**入库规范化（`07_简历管理.py`）：**
- 引入 `normalize_platform()` 函数 + `PLATFORM_ALIAS` 别名表，写库时统一为三个枚举值之一。

#### 测试

- 新增 `tests/services/test_resume_ai_service_prompt.py` 5 个测试，回归 prompt 字段覆盖。
- 新增 `tests/services/test_platform_normalize.py` 5 个测试，验证别名映射。
- `scripts/test_parse_one.py` 抽样工具 + `scripts/audit_resume_db.py` 字段填充率审计，前/后对比。

#### 预期收益

| 指标 | 改造前 | 目标 |
|---|---|---|
| candidates.age 填充率 | 52% | 80%+ |
| candidates.gender 填充率 | 52% | 90%+ |
| candidates.current_city 填充率 | 41% | 75%+ |
| skills 人均条数 | 8.79 | 3-5 |
| 0% 字段数 | 19 | 0（已删） |
| source_platform 混乱命名 | 有 | 无 |

#### 后续观察

- 下一批入库后再跑 `audit_resume_db.py`，确认实际收益是否达标。
- 如 `current_city` 仍 < 70%，考虑在 prompt 里追加"现居城市与籍贯区别"案例样本。
```

### Step 6.2：commit 日志

- [ ] **Step 6.2：commit**

```bash
git add docs/04_development_log.md
git commit -m "docs: V2.17 简历 AI 解析三轴优化（prompt + schema + 数据治理）"
```

---

## Task 7：终验

### Step 7.1：跑全套测试

- [ ] **Step 7.1：所有新增测试一起跑**

```bash
cd "d:/Users/pc/Documents/walker/CodeBuddy/招聘网站助手" && python -X utf8 -m pytest tests/services/ -v
```
Expected：`test_resume_ai_service_prompt.py` 5 PASS + `test_platform_normalize.py` 5 PASS。

### Step 7.2：跑 audit 验最终状态

- [ ] **Step 7.2：审计最终库**

```bash
python -X utf8 scripts/audit_resume_db.py > data/audit-final.txt 2>&1
head -80 data/audit-final.txt
```
Expected：
- 候选人 75（不变）。
- 平台分布只剩 3 行：智联招聘 / BOSS直聘 / 51前程无忧。
- candidates 字段表里没有 qq/籍贯/政治面貌/民族/身高 这 5 行。
- candidates.age 填充率 ≥ 75%。
- skills 总数显著下降（具体 N 取决于数据）。

### Step 7.3：导出 audit 对比附录

- [ ] **Step 7.3：把对比表写进 docs**

```bash
{
  echo "## 改造前"
  cat data/audit-before.txt
  echo
  echo "## 改造后"
  cat data/audit-final.txt
} > docs/05_db_audit_v2_17.md
git add docs/05_db_audit_v2_17.md data/audit-before.txt data/audit-final.txt
git commit -m "docs: 附 V2.17 改造前后填充率对比"
```

### Step 7.4：最终 status

- [ ] **Step 7.4：检查工作树状态**

```bash
git status
git log --oneline -10
```
Expected：工作树干净（只剩 `data/resume_archive.db` 和 `data/resume_archive.db.backup-20260519` 这种 .gitignore 里的产物未追踪）；最近 6-8 个提交按本计划顺序。

---

## 风险与回退

| 风险 | 触发条件 | 应对 |
|---|---|---|
| SQLite < 3.35 不支持 DROP COLUMN | Step 3.4 报 OperationalError | 回退到"建新表 + 迁移数据 + 改名" 模板（Pattern of EAA"重命名表"模式），脚本里加分支 |
| AI 补全阶段超时 / API 限流 | Step 3.6 中途中断 | 脚本天然幂等（只补 NULL 字段），重跑即可。可加 `--phase ai-fill --since-cid N` 断点续跑（本计划不实现，按需追加） |
| 回归发现某关键字段反而填充率下降 | Task 5 或 7.2 看到回退 | 检查 prompt 该字段的描述是否被减肥时误删；查 git diff `recruitment_assistant/services/resume_ai_service.py` |
| 已入库数据被破坏 | 任何阶段 | Step 0.1 的 `data/resume_archive.db.backup-20260519` 一键恢复 |

回退命令（极端情况）：

```bash
cp data/resume_archive.db.backup-20260519 data/resume_archive.db
git revert <V2.17 相关 commit hash...>
```

---

## 自检

**1. Spec 覆盖**
- prompt 重写 → Task 1 ✓
- schema 减肥 → Task 2 ✓
- 平台命名统一 → Task 3 阶段 1 + Task 4 ✓
- skills 合并 → Task 3 阶段 3 ✓
- AI 补全已入库数据 → Task 3 阶段 4 ✓
- 抽样回归 → Task 5 ✓
- 开发日志 → Task 6 ✓

**2. Placeholder 扫描**
- 无 TBD / TODO / "实现后续" 字样。
- 关键代码块都给了完整内容（SYSTEM_PROMPT 全文、迁移脚本全文、测试全文）。

**3. 类型一致性**
- `normalize_platform` 在 Task 4.1 定义，Task 4.3 import 使用，命名一致。
- `PLATFORM_ALIAS` / `PLATFORM_VALID` 在 Task 4.1 定义并暴露，Task 4.2 测试使用。
- `DROP_COLUMNS` 字段表与 Task 2 Pydantic / ORM 删除清单完全对齐。
- `SYSTEM_PROMPT` 在 Task 1 重写后，Task 2 的 prompt 测试 (`test_prompt_explicitly_forbids_dropped_fields`) 校验它不包含已删字段，前后约束自洽。
