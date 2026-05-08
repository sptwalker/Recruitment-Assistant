# 文档 2：数据库表结构设计（核心，数据持久化）

## 1. 设计目标

数据库用于持久化招聘平台、候选人、简历、工作经历、教育经历、技能、岗位、采集任务、导出记录、评分结果等核心数据。

推荐使用 PostgreSQL，首期已确认不支持 MySQL 分支。PostgreSQL 的 `jsonb` 将用于保存原始平台数据、任务参数、匹配点与缺失点。

## 2. 核心实体关系

```text
platform_account 1 ── n crawl_task
platform_account 1 ── n raw_resume
candidate 1 ── n resume
resume 1 ── n work_experience
resume 1 ── n education_experience
resume 1 ── n resume_skill
resume 1 ── n resume_tag
resume 1 ── n resume_attachment
job_position 1 ── n resume_score
resume 1 ── n resume_score
candidate 1 ── n export_record_item
export_record 1 ── n export_record_item
```

## 3. 表清单

| 表名 | 用途 |
|---|---|
| `platform_account` | 招聘平台账号与登录态元信息 |
| `crawl_task` | 采集任务 |
| `crawl_task_log` | 采集任务日志 |
| `raw_resume` | 平台原始简历数据 |
| `candidate` | 候选人主表，去重后的人员实体 |
| `resume` | 标准化简历主表 |
| `work_experience` | 工作经历 |
| `education_experience` | 教育经历 |
| `project_experience` | 项目经历 |
| `resume_skill` | 技能 |
| `resume_tag` | 标签 |
| `resume_attachment` | 简历附件原文件记录 |
| `job_position` | 企业招聘岗位 |
| `resume_score` | 简历与岗位匹配评分 |
| `export_record` | 导出批次记录 |
| `export_record_item` | 导出明细 |
| `operation_audit_log` | 操作审计日志 |

## 4. 枚举建议

### 4.1 平台 `platform_code`

- `boss`：BOSS 直聘
- `zhilian`：智联招聘
- `qiancheng`：前程无忧
- `liepin`：猎聘

### 4.2 任务状态 `task_status`

- `pending`
- `running`
- `success`
- `failed`
- `cancelled`
- `paused`

### 4.3 简历状态 `resume_status`

- `new`
- `reviewed`
- `contacted`
- `interviewing`
- `rejected`
- `hired`
- `archived`

## 5. 表结构设计

### 5.1 `platform_account`

保存平台账号配置与登录态元信息，不保存明文密码。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `platform_code` | varchar(32) | not null | 平台编码 |
| `account_name` | varchar(128) | not null | 账号显示名/手机号脱敏值 |
| `login_state_path` | varchar(512) | null | Playwright storage state 文件路径 |
| `is_active` | boolean | not null default true | 是否启用 |
| `last_login_at` | timestamptz | null | 最近登录时间 |
| `last_check_at` | timestamptz | null | 最近登录态检查时间 |
| `remark` | text | null | 备注 |
| `created_at` | timestamptz | not null | 创建时间 |
| `updated_at` | timestamptz | not null | 更新时间 |

索引：

- `idx_platform_account_platform_code(platform_code)`
- `uk_platform_account(platform_code, account_name)`

### 5.2 `crawl_task`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `platform_account_id` | bigint | FK | 平台账号 |
| `platform_code` | varchar(32) | not null | 平台编码，便于查询 |
| `task_name` | varchar(128) | not null | 任务名称 |
| `task_type` | varchar(32) | not null | `manual` / `scheduled` |
| `status` | varchar(32) | not null | 任务状态 |
| `query_keyword` | varchar(255) | null | 搜索关键词 |
| `query_city` | varchar(128) | null | 搜索城市 |
| `query_params` | jsonb | null | 其他查询条件 |
| `planned_count` | int | null | 计划采集数量 |
| `success_count` | int | not null default 0 | 成功数量 |
| `failed_count` | int | not null default 0 | 失败数量 |
| `started_at` | timestamptz | null | 开始时间 |
| `finished_at` | timestamptz | null | 结束时间 |
| `next_run_at` | timestamptz | null | 下次运行时间 |
| `error_message` | text | null | 最近错误 |
| `created_at` | timestamptz | not null | 创建时间 |
| `updated_at` | timestamptz | not null | 更新时间 |

索引：

- `idx_crawl_task_status(status)`
- `idx_crawl_task_platform(platform_code)`
- `idx_crawl_task_next_run(next_run_at)`

### 5.3 `crawl_task_log`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `task_id` | bigint | FK | 采集任务 |
| `level` | varchar(16) | not null | `info` / `warning` / `error` |
| `message` | text | not null | 日志内容 |
| `context` | jsonb | null | 上下文 |
| `created_at` | timestamptz | not null | 创建时间 |

索引：

- `idx_crawl_task_log_task_id(task_id)`
- `idx_crawl_task_log_created_at(created_at)`

### 5.4 `raw_resume`

保存平台原始数据，作为可追溯来源。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `platform_code` | varchar(32) | not null | 平台编码 |
| `platform_account_id` | bigint | FK | 平台账号 |
| `task_id` | bigint | FK null | 来源任务 |
| `source_resume_id` | varchar(128) | null | 平台侧简历 ID |
| `source_candidate_id` | varchar(128) | null | 平台侧候选人 ID |
| `source_url` | text | null | 来源页面 URL |
| `raw_json` | jsonb | null | 原始结构化数据 |
| `raw_html_path` | varchar(512) | null | HTML 快照路径 |
| `content_hash` | varchar(64) | not null | 内容哈希，用于去重 |
| `parsed_status` | varchar(32) | not null default 'pending' | 解析状态 |
| `parsed_at` | timestamptz | null | 解析时间 |
| `created_at` | timestamptz | not null | 创建时间 |

索引：

- `uk_raw_resume_platform_source(platform_code, source_resume_id)`
- `idx_raw_resume_content_hash(content_hash)`
- `idx_raw_resume_task_id(task_id)`

### 5.5 `candidate`

去重后的候选人实体。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `name` | varchar(128) | not null | 姓名 |
| `gender` | varchar(16) | null | 性别 |
| `birth_year` | int | null | 出生年份 |
| `age` | int | null | 年龄，允许为空或冗余 |
| `phone_plain` | varchar(64) | null | 手机明文，仅本地本人使用场景保存 |
| `phone_hash` | varchar(64) | null | 手机哈希 |
| `phone_masked` | varchar(64) | null | 脱敏手机 |
| `email_plain` | varchar(128) | null | 邮箱明文，仅本地本人使用场景保存 |
| `email_hash` | varchar(64) | null | 邮箱哈希 |
| `email_masked` | varchar(128) | null | 脱敏邮箱 |
| `current_city` | varchar(128) | null | 当前城市 |
| `highest_degree` | varchar(64) | null | 最高学历 |
| `years_of_experience` | numeric(4,1) | null | 工作年限 |
| `current_company` | varchar(255) | null | 当前/最近公司 |
| `current_position` | varchar(255) | null | 当前/最近职位 |
| `dedup_key` | varchar(128) | null | 去重键 |
| `status` | varchar(32) | not null default 'new' | 候选人状态 |
| `created_at` | timestamptz | not null | 创建时间 |
| `updated_at` | timestamptz | not null | 更新时间 |
| `deleted_at` | timestamptz | null | 软删除时间 |

索引：

- `idx_candidate_name(name)`
- `idx_candidate_phone_hash(phone_hash)`
- `idx_candidate_email_hash(email_hash)`
- `idx_candidate_dedup_key(dedup_key)`
- `idx_candidate_status(status)`

### 5.6 `resume`

标准化简历主表。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `candidate_id` | bigint | FK | 候选人 |
| `raw_resume_id` | bigint | FK null | 原始简历 |
| `platform_code` | varchar(32) | not null | 来源平台 |
| `resume_title` | varchar(255) | null | 简历标题 |
| `summary` | text | null | 个人简介 |
| `expected_position` | varchar(255) | null | 期望职位 |
| `expected_city` | varchar(128) | null | 期望城市 |
| `expected_salary_min` | int | null | 期望月薪下限，单位元 |
| `expected_salary_max` | int | null | 期望月薪上限，单位元 |
| `expected_industry` | varchar(255) | null | 期望行业 |
| `job_status` | varchar(64) | null | 求职状态 |
| `last_active_at` | timestamptz | null | 平台活跃时间 |
| `resume_status` | varchar(32) | not null default 'new' | 简历状态 |
| `quality_score` | numeric(5,2) | null | 简历完整度/质量分 |
| `created_at` | timestamptz | not null | 创建时间 |
| `updated_at` | timestamptz | not null | 更新时间 |

索引：

- `idx_resume_candidate_id(candidate_id)`
- `idx_resume_platform_code(platform_code)`
- `idx_resume_expected_position(expected_position)`
- `idx_resume_status(resume_status)`

### 5.7 `work_experience`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `resume_id` | bigint | FK | 简历 |
| `company_name` | varchar(255) | not null | 公司名称 |
| `position_name` | varchar(255) | null | 职位名称 |
| `department` | varchar(255) | null | 部门 |
| `industry` | varchar(255) | null | 行业 |
| `start_date` | date | null | 开始日期 |
| `end_date` | date | null | 结束日期 |
| `is_current` | boolean | not null default false | 是否当前工作 |
| `description` | text | null | 工作内容 |
| `achievements` | text | null | 工作业绩 |
| `sort_order` | int | not null default 0 | 排序 |
| `created_at` | timestamptz | not null | 创建时间 |

索引：

- `idx_work_experience_resume_id(resume_id)`
- `idx_work_experience_company(company_name)`

### 5.8 `education_experience`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `resume_id` | bigint | FK | 简历 |
| `school_name` | varchar(255) | not null | 学校 |
| `major` | varchar(255) | null | 专业 |
| `degree` | varchar(64) | null | 学历 |
| `start_date` | date | null | 开始日期 |
| `end_date` | date | null | 结束日期 |
| `description` | text | null | 描述 |
| `sort_order` | int | not null default 0 | 排序 |
| `created_at` | timestamptz | not null | 创建时间 |

索引：

- `idx_education_resume_id(resume_id)`
- `idx_education_school(school_name)`

### 5.9 `project_experience`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `resume_id` | bigint | FK | 简历 |
| `project_name` | varchar(255) | not null | 项目名称 |
| `role_name` | varchar(255) | null | 项目角色 |
| `start_date` | date | null | 开始日期 |
| `end_date` | date | null | 结束日期 |
| `description` | text | null | 项目描述 |
| `responsibility` | text | null | 个人职责 |
| `achievement` | text | null | 项目成果 |
| `sort_order` | int | not null default 0 | 排序 |
| `created_at` | timestamptz | not null | 创建时间 |

索引：

- `idx_project_resume_id(resume_id)`

### 5.10 `resume_skill`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `resume_id` | bigint | FK | 简历 |
| `skill_name` | varchar(128) | not null | 技能名称 |
| `skill_level` | varchar(64) | null | 熟练度 |
| `source` | varchar(32) | null | `explicit` / `extracted` |
| `created_at` | timestamptz | not null | 创建时间 |

索引：

- `idx_resume_skill_resume_id(resume_id)`
- `idx_resume_skill_name(skill_name)`

### 5.11 `resume_tag`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `resume_id` | bigint | FK | 简历 |
| `tag_name` | varchar(128) | not null | 标签名 |
| `tag_type` | varchar(64) | null | 标签类型 |
| `confidence` | numeric(5,2) | null | 置信度 |
| `created_at` | timestamptz | not null | 创建时间 |

索引：

- `idx_resume_tag_resume_id(resume_id)`
- `idx_resume_tag_name(tag_name)`

### 5.12 `resume_attachment`

保存简历附件原文件记录，例如 PDF、Word、平台下载文件。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `resume_id` | bigint | FK | 简历 |
| `raw_resume_id` | bigint | FK null | 原始简历 |
| `platform_code` | varchar(32) | not null | 来源平台 |
| `file_name` | varchar(255) | not null | 原始文件名 |
| `file_path` | varchar(512) | not null | 本地保存路径 |
| `file_ext` | varchar(32) | null | 文件扩展名 |
| `mime_type` | varchar(128) | null | MIME 类型 |
| `file_size` | bigint | null | 文件大小，单位字节 |
| `file_hash` | varchar(64) | null | 文件哈希 |
| `download_url` | text | null | 原始下载地址，如可获取 |
| `created_at` | timestamptz | not null | 创建时间 |

索引：

- `idx_resume_attachment_resume_id(resume_id)`
- `idx_resume_attachment_file_hash(file_hash)`

### 5.13 `job_position`

企业招聘岗位。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `job_name` | varchar(255) | not null | 岗位名称 |
| `department` | varchar(255) | null | 部门 |
| `city` | varchar(128) | null | 工作城市 |
| `salary_min` | int | null | 月薪下限 |
| `salary_max` | int | null | 月薪上限 |
| `degree_requirement` | varchar(64) | null | 学历要求 |
| `experience_min_years` | numeric(4,1) | null | 最低经验 |
| `experience_max_years` | numeric(4,1) | null | 最高经验 |
| `required_skills` | jsonb | null | 必备技能 |
| `preferred_skills` | jsonb | null | 加分技能 |
| `description` | text | null | JD 描述 |
| `status` | varchar(32) | not null default 'active' | 状态 |
| `created_at` | timestamptz | not null | 创建时间 |
| `updated_at` | timestamptz | not null | 更新时间 |
| `deleted_at` | timestamptz | null | 软删除时间 |

索引：

- `idx_job_position_status(status)`
- `idx_job_position_job_name(job_name)`

### 5.14 `resume_score`

简历与岗位匹配结果。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `resume_id` | bigint | FK | 简历 |
| `job_position_id` | bigint | FK | 岗位 |
| `total_score` | numeric(5,2) | not null | 总分 |
| `skill_score` | numeric(5,2) | null | 技能匹配分 |
| `experience_score` | numeric(5,2) | null | 经验匹配分 |
| `education_score` | numeric(5,2) | null | 学历匹配分 |
| `salary_score` | numeric(5,2) | null | 薪资匹配分 |
| `city_score` | numeric(5,2) | null | 城市匹配分 |
| `evaluation` | text | null | 评价说明 |
| `matched_points` | jsonb | null | 匹配点 |
| `missing_points` | jsonb | null | 缺失点 |
| `created_at` | timestamptz | not null | 创建时间 |

索引：

- `idx_resume_score_resume_id(resume_id)`
- `idx_resume_score_job_position_id(job_position_id)`
- `uk_resume_score(resume_id, job_position_id)`

### 5.15 `export_record`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `export_type` | varchar(32) | not null | `excel` / `word` |
| `file_path` | varchar(512) | not null | 导出文件路径 |
| `filters` | jsonb | null | 导出筛选条件 |
| `item_count` | int | not null default 0 | 导出数量 |
| `created_by` | varchar(128) | null | 操作人 |
| `created_at` | timestamptz | not null | 创建时间 |

索引：

- `idx_export_record_created_at(created_at)`

### 5.16 `export_record_item`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `export_record_id` | bigint | FK | 导出批次 |
| `candidate_id` | bigint | FK | 候选人 |
| `resume_id` | bigint | FK | 简历 |
| `created_at` | timestamptz | not null | 创建时间 |

索引：

- `idx_export_item_record_id(export_record_id)`
- `idx_export_item_candidate_id(candidate_id)`

### 5.17 `operation_audit_log`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigint | PK | 主键 |
| `action` | varchar(64) | not null | 操作类型 |
| `target_type` | varchar(64) | null | 对象类型 |
| `target_id` | bigint | null | 对象 ID |
| `detail` | jsonb | null | 操作详情 |
| `created_by` | varchar(128) | null | 操作人 |
| `created_at` | timestamptz | not null | 创建时间 |

索引：

- `idx_audit_action(action)`
- `idx_audit_created_at(created_at)`

## 6. PostgreSQL 建表 SQL 草案

后续编码阶段建议通过 SQLAlchemy + Alembic 生成正式迁移文件。以下为核心草案：

```sql
CREATE TABLE candidate (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    gender VARCHAR(16),
    birth_year INT,
    age INT,
    phone_plain VARCHAR(64),
    phone_hash VARCHAR(64),
    phone_masked VARCHAR(64),
    email_plain VARCHAR(128),
    email_hash VARCHAR(64),
    email_masked VARCHAR(128),
    current_city VARCHAR(128),
    highest_degree VARCHAR(64),
    years_of_experience NUMERIC(4,1),
    current_company VARCHAR(255),
    current_position VARCHAR(255),
    dedup_key VARCHAR(128),
    status VARCHAR(32) NOT NULL DEFAULT 'new',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE INDEX idx_candidate_name ON candidate(name);
CREATE INDEX idx_candidate_phone_hash ON candidate(phone_hash);
CREATE INDEX idx_candidate_email_hash ON candidate(email_hash);
CREATE INDEX idx_candidate_dedup_key ON candidate(dedup_key);
```

完整 SQL 建议在确定数据库类型后进入编码阶段生成。

## 7. 数据安全建议

- 手机、邮箱已确认保存明文；同时保留哈希和脱敏值，便于去重、检索和列表脱敏展示。
- 登录态文件只保存在本地，不入库、不提交。
- 简历附件原文件默认保存到 `data/attachments`，按平台和日期分目录存放。
- 导出文件默认保存到本地 `data/exports`，定期清理。
- 删除候选人建议使用软删除，避免误删。
- 对导出、删除、批量修改写入 `operation_audit_log`。

## 8. 已确认决策

1. 首期优先平台：`智联招聘`。
2. 数据库：`PostgreSQL`。
3. 使用场景：Windows 本地本人单用户使用，首期不做用户权限系统。
4. 手机号、邮箱：保存明文，同时保存哈希和脱敏值。
5. 简历附件原文件：需要保存。
6. Excel 导出：先设计通用模板，默认包含手机号、邮箱明文。
7. 岗位 JD：已有企业岗位 JD，格式为 Word，首期支持 Word 文档读取与文本提取。
8. 登录方式：人工扫码/短信登录后保存 Playwright 登录态。
9. 采集入口：智联招聘 `主动搜索候选人` 与 `已投递简历`。
10. 附件命名：`平台_候选人姓名_手机号后四位_日期_原文件名`。
11. 采集频率：每次最多 `20-50` 份，每份间隔 `8-30` 秒。
12. 评分评价：首期建议先做规则评分，后续再扩展大模型评分。
