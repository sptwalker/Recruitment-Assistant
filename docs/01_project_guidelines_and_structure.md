# 文档 1：项目开发规范 & 目录架构

## 1. 项目定位

本项目为 `招聘网站助手`，目标是在 Windows 本地环境中，通过 Python 技术栈实现多招聘平台简历采集、清洗、去重、结构化存储、导出与后续评分评价。

> 合规原则：系统应优先支持企业账号授权范围内的数据处理，避免绕过平台风控、验证码、权限控制或服务条款。自动化采集频率、登录方式、数据保存范围需由使用方确认并承担合规责任。

## 2. 推荐技术栈

| 模块 | 技术选型 | 说明 |
|---|---|---|
| 语言 | Python 3.11+ | 兼顾生态、稳定性与类型支持 |
| Web 面板 | Streamlit | 快速构建本地控制台 |
| 浏览器自动化 | Playwright | 登录态维护、页面操作、页面快照 |
| HTML 解析 | Parsel / BeautifulSoup | 页面结构化抽取 |
| 数据校验 | Pydantic v2 | 简历字段模型校验与标准化 |
| 数据处理 | Pandas | 清洗、去重、导出 |
| 数据库 | PostgreSQL | 已确认使用 PostgreSQL，优先利用 JSONB、全文检索与迁移能力 |
| ORM / SQL | SQLAlchemy 2.x + Alembic | 数据访问与迁移管理 |
| 调度 | APScheduler | 定时采集、失败重试 |
| 导出 | openpyxl / python-docx | Excel / Word 导出 |
| 配置 | python-dotenv / pydantic-settings | 本地环境配置 |
| 日志 | logging / loguru | 操作日志、错误追踪 |
| 测试 | pytest | 单元测试、集成测试 |

## 3. 分层架构

```text
[前端交互/控制台]
        ↓
[任务调度]
        ↓
[登录模块]
        ↓
[采集模块]
        ↓
[清洗模块]
        ↓
[存储/导出]
        ↓
[评分评价] ← [招聘岗位]
```

### 3.1 入口层

- 提供 Streamlit 本地 Web 面板。
- 支持平台账号配置、任务启动/暂停、采集进度查看、简历搜索、导出。

### 3.2 调度层

- 维护任务队列、任务状态、失败重试、定时采集。
- 控制采集频率，避免高频请求。

### 3.3 核心层

- 平台登录态维护。
- 页面访问、简历列表抓取、详情页解析。
- 平台差异通过适配器隔离。

### 3.4 处理层

- 字段清洗、标准化、去重、标签生成。
- 将不同平台字段统一为内部标准模型。

### 3.5 输出层

- 数据库存储。
- Excel / Word 导出。
- 后续可对接评分评价模型。

## 4. 推荐目录结构

```text
recruitment-assistant/
├─ app/
│  ├─ main.py                         # Streamlit 启动入口
│  ├─ pages/                          # Streamlit 多页面
│  │  ├─ 01_dashboard.py              # 任务看板
│  │  ├─ 02_candidates.py             # 候选人管理
│  │  ├─ 03_jobs.py                   # 招聘岗位管理
│  │  └─ 04_exports.py                # 导出中心
│  └─ components/                     # 页面组件
│
├─ recruitment_assistant/
│  ├─ __init__.py
│  ├─ config/                         # 配置管理
│  │  ├─ settings.py                  # 全局配置
│  │  └─ logging_config.py            # 日志配置
│  │
│  ├─ core/                           # 核心基础能力
│  │  ├─ browser.py                   # Playwright 浏览器管理
│  │  ├─ context.py                   # 登录上下文/会话管理
│  │  ├─ exceptions.py                # 自定义异常
│  │  └─ security.py                  # 敏感信息处理
│  │
│  ├─ platforms/                      # 招聘平台适配器
│  │  ├─ base.py                      # 平台适配器抽象基类
│  │  ├─ boss/                        # BOSS 直聘
│  │  │  ├─ adapter.py
│  │  │  ├─ login.py
│  │  │  ├─ crawler.py
│  │  │  └─ parser.py
│  │  ├─ zhilian/                     # 智联招聘
│  │  ├─ qiancheng/                   # 前程无忧
│  │  └─ liepin/                      # 猎聘
│  │
│  ├─ scheduler/                      # 任务调度
│  │  ├─ service.py                   # 调度服务
│  │  ├─ jobs.py                      # 任务定义
│  │  └─ retry.py                     # 重试策略
│  │
│  ├─ schemas/                        # Pydantic 数据模型
│  │  ├─ candidate.py                 # 候选人标准模型
│  │  ├─ resume.py                    # 简历标准模型
│  │  ├─ job.py                       # 岗位模型
│  │  └─ task.py                      # 任务模型
│  │
│  ├─ services/                       # 业务服务
│  │  ├─ candidate_service.py
│  │  ├─ resume_service.py
│  │  ├─ job_service.py
│  │  ├─ dedup_service.py
│  │  ├─ tagging_service.py
│  │  └─ scoring_service.py
│  │
│  ├─ cleaning/                       # 清洗与标准化
│  │  ├─ normalizers.py
│  │  ├─ deduplicators.py
│  │  ├─ salary.py
│  │  ├─ education.py
│  │  └─ experience.py
│  │
│  ├─ storage/                        # 数据访问层
│  │  ├─ db.py                        # 数据库连接
│  │  ├─ models.py                    # SQLAlchemy ORM 模型
│  │  ├─ repositories/                # Repository 层
│  │  └─ migrations/                  # Alembic 迁移文件
│  │
│  ├─ exporters/                      # 导出模块
│  │  ├─ excel_exporter.py
│  │  ├─ word_exporter.py
│  │  └─ templates/
│  │
│  ├─ utils/                          # 通用工具
│  │  ├─ datetime_utils.py
│  │  ├─ text_utils.py
│  │  ├─ hash_utils.py
│  │  └─ file_utils.py
│  │
│  └─ tests/                          # 包内测试资源，可选
│
├─ tests/                             # pytest 测试
│  ├─ unit/
│  ├─ integration/
│  └─ fixtures/
│
├─ data/                              # 本地运行数据，不提交敏感内容
│  ├─ exports/                        # Excel / Word 导出文件
│  ├─ attachments/                    # 简历附件原文件，如 PDF / Word
│  ├─ browser_state/                  # Playwright 登录态
│  └─ snapshots/                      # 页面快照，用于调试解析规则
│
├─ docs/
│  ├─ 01_project_guidelines_and_structure.md
│  ├─ 02_database_schema.md
│  └─ 03_development_plan.md
│
├─ scripts/                           # 本地脚本
│  ├─ init_db.py
│  ├─ run_streamlit.py
│  └─ install_playwright.py
│
├─ .env.example                       # 示例配置，不含真实账号密码
├─ .gitignore
├─ pyproject.toml                     # 项目依赖与工具配置
├─ alembic.ini
└─ README.md                          # 项目说明，后续需要时再补充
```

## 5. 命名规范

### 5.1 Python 文件

- 文件名使用小写蛇形：`candidate_service.py`。
- 类名使用大驼峰：`CandidateService`。
- 函数、变量使用小写蛇形：`normalize_salary`。
- 常量使用全大写：`DEFAULT_RETRY_TIMES`。

### 5.2 数据库

- 表名使用复数或业务名小写蛇形，建议统一单数业务实体：`candidate`、`resume`、`job_position`。
- 字段名使用小写蛇形：`created_at`、`source_platform`。
- 主键统一为 `id`。
- 时间字段统一为 `created_at`、`updated_at`、`deleted_at`。

### 5.3 平台适配器

每个平台必须实现统一接口：

- `login()`：登录或恢复登录态。
- `is_logged_in()`：检查登录状态。
- `fetch_resume_list()`：获取简历列表。
- `fetch_resume_detail()`：获取简历详情。
- `parse_resume()`：解析为统一简历模型。

## 6. 配置规范

敏感信息不得硬编码。推荐使用 `.env`：

```env
APP_ENV=local
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/recruitment_assistant
PLAYWRIGHT_HEADLESS=false
CRAWLER_MIN_INTERVAL_SECONDS=8
CRAWLER_MAX_INTERVAL_SECONDS=30
CRAWLER_MAX_RESUMES_PER_TASK=50
EXPORT_DIR=data/exports
ATTACHMENT_DIR=data/attachments
```

账号密码、Cookie、登录态文件必须放在本地安全目录，不提交到版本库。首期采用人工扫码/短信登录后保存 Playwright 登录态，不做自动输入账号密码。

首期智联招聘采集入口：`主动搜索候选人` 与 `已投递简历`。采集频率采用保守策略：每次最多 `20-50` 份，每份间隔 `8-30` 秒。

## 7. 日志规范

日志至少分为：

- 应用日志：启动、配置、任务状态。
- 采集日志：平台、页码、候选人、成功/失败。
- 登录日志：仅记录平台和状态，不记录密码、Cookie。
- 错误日志：异常类型、堆栈、上下文。
- 审计日志：导出、删除、批量操作。

禁止在日志中输出：

- 账号密码。
- 完整 Cookie。
- 候选人完整手机号、邮箱、身份证等敏感信息。

## 8. 数据处理规范

### 8.1 字段标准化

所有平台原始字段先进入 `raw_resume`，再转换为统一模型：

- 姓名：去除空格、特殊符号。
- 手机/邮箱：标准化格式，数据库保存明文、哈希和脱敏值；页面列表默认展示脱敏值，详情页可展示明文；通用 Excel 导出默认包含明文。
- 薪资：统一转为月薪范围，单位为 CNY。
- 工作年限：统一转为月份或年份数值。
- 学历：统一枚举值。
- 城市：统一为标准城市名。

### 8.2 去重规则

推荐分层去重：

1. 强匹配：手机号、邮箱。
2. 中匹配：姓名 + 学校 + 公司。
3. 弱匹配：姓名 + 工作年限 + 城市 + 技能相似度。

### 8.3 原始数据保留

建议保存平台原始 JSON / HTML 解析结果，便于后续修复规则、追溯数据来源。

## 9. 采集合规与风控建议

- 不建议绕过验证码、短信验证、人机识别。
- 不建议高频批量请求。
- 仅采集账号权限范围内可见的候选人数据。
- 支持人工登录后保存登录态。
- 每个平台设置独立频率、每日上限、失败退避。
- 导出文件与简历附件原文件应限制访问权限。

## 10. 开发约定

- 先完成单平台闭环，再扩展多平台。
- 平台解析规则必须有页面快照测试。
- 数据模型先稳定，再开发评分评价。
- 每次新增字段必须同步：Pydantic Schema、ORM Model、数据库迁移、导出模板。
- 优先实现可维护规则，不追求一次性覆盖所有页面变体。
