# 开发日志

## 2026-05-09

### 当前阶段

项目推进到 `P5：智联聊天附件简历自动采集闭环调试`。当前目标是完成自动登录、进入聊天、逐个选择候选人、点击 `要附件简历` / `查看简历附件`，捕获附件下载链接并保存 PDF，连续处理 5 个候选人。

### 已完成内容

- 增强智联登录态维护：
  - `login_manually()` 支持登录后自动进入智联系统首页。
  - 新增 `keep_open` 参数，避免登录态保存后浏览器立即关闭。
  - `scripts/zhilian_login.py` 新增 `--keep-open`、`--no-enter-home` 参数。
  - `app/pages/05_login.py` 调整为登录后保持浏览器窗口打开，便于确认登录状态。
- 增强脚本直接运行能力：
  - `scripts/init_db.py`
  - `scripts/check_zhilian_login.py`
  - `scripts/capture_zhilian_page.py`
  - `scripts/capture_zhilian_manual_pages.py`
  - 以上脚本补充项目根目录导入路径，便于从项目根目录直接执行。
- 新增智联聊天附件简历自动采集入口：
  - `scripts/download_zhilian_chat_resumes.py`
  - 支持手动监听下载链接、自动监听、全自动点击候选人并下载附件简历。
- 增强 Playwright 浏览器上下文：
  - `recruitment_assistant/core/browser.py` 开启 `accept_downloads=True`，支持浏览器下载附件 PDF。
- 大幅增强智联适配器：`recruitment_assistant/platforms/zhilian/adapter.py`
  - 自动进入智联聊天页面。
  - 自动点击左侧聊天入口，包含 DOM 点击和坐标兜底。
  - 自动识别左侧候选人列表。
  - 跳过第一项 `快速处理新招呼`，从真实候选人开始向下处理。
  - 根据实际 DOM 坐标修正候选人卡片点击范围，使用 `elementsFromPoint()` 和真实鼠标点击双重兜底。
  - 支持点击聊天详情区的 `要附件简历`。
  - 支持点击 `查看简历附件`、`查看附件简历`、`下载附件简历` 等按钮文案。
  - 避免误点 `已向对方要附件简历` 等状态文本。
  - 如果未找到 `要附件简历`，继续尝试查看已收到的附件简历。
  - 自动监听智联附件下载链接并保存 PDF。
  - 保存原始附件记录到 `raw_resume`。
  - 增加聊天详情区操作元素打印，便于继续定位真实按钮 DOM。
- 新增 PDF 简历解析能力：
  - `recruitment_assistant/parsers/pdf_resume_parser.py`
  - `scripts/parse_pdf_resumes.py`
  - `pyproject.toml` 新增 `pypdf>=5.0.0` 依赖。

### 本次重点修复

- 修复登录后浏览器窗口自动关闭的问题。
- 修复登录后未自动进入智联系统首页的问题。
- 修复进入聊天页后误把整页容器识别为候选人的问题。
- 修复误点左侧第一项 `快速处理新招呼` 的问题。
- 修复已读取候选人文本但未真正选中候选人聊天的情况。
- 修复 `查看简历附件` 文案未被匹配的问题。
- 修复误把 `已向对方要附件简历` 状态文本当作按钮点击的问题。

### 当前可用命令

```powershell
python scripts/zhilian_login.py --account default --wait 180 --keep-open
python scripts/check_zhilian_login.py --account default
python scripts/download_zhilian_chat_resumes.py --account default --auto-click --max-resumes 5 --wait 900 --per-candidate-wait 60
python scripts/parse_pdf_resumes.py
```

### 验证状态

代码已通过编译检查：

```powershell
python -m compileall recruitment_assistant scripts app
```

### 下一步建议

1. 继续使用真实智联聊天页验证 `查看简历附件` 点击是否能稳定触发下载请求。
2. 如果仍未捕获下载链接，依据新增的聊天详情区操作元素日志继续收窄按钮 DOM 选择器。
3. 完成 5 个候选人附件简历连续下载后，进入 PDF 解析字段准确率优化。

## 2026-05-08

### 当前阶段

项目已完成到 `P4 初版：智联招聘原始页面采集闭环`。

### 已完成内容

#### P0-P1：项目规划与基础设施

- 完成项目标准文档：
  - `docs/01_project_guidelines_and_structure.md`
  - `docs/02_database_schema.md`
  - `docs/03_development_plan.md`
- 初始化 Python 项目结构。
- 配置 `pyproject.toml`、`.env.example`、`.gitignore`。
- 配置 `Streamlit` 本地 Web 面板。
- 配置 `PostgreSQL + SQLAlchemy`。
- 配置基础日志系统。
- 创建本地脚本：
  - `scripts/init_db.py`
  - `scripts/run_streamlit.py`
  - `scripts/install_playwright.py`

#### P2：核心模型与基础页面

- 完成 17 张核心数据表 ORM：
  - `candidate`
  - `resume`
  - `raw_resume`
  - `crawl_task`
  - `crawl_task_log`
  - `platform_account`
  - `work_experience`
  - `education_experience`
  - `project_experience`
  - `resume_skill`
  - `resume_tag`
  - `resume_attachment`
  - `job_position`
  - `resume_score`
  - `export_record`
  - `export_record_item`
  - `operation_audit_log`
- 完成候选人手动新增、列表展示、姓名搜索。
- 完成岗位 JD `.docx` 上传、文本提取、岗位保存、岗位列表展示。
- 完成候选人 Excel 导出，默认包含手机号、邮箱明文。

#### P3：智联招聘登录态维护

- 实现 Playwright 浏览器封装：`recruitment_assistant/core/browser.py`。
- 实现智联招聘人工扫码/短信登录。
- 实现登录态保存到 `data/browser_state`。
- 实现登录态检测。
- 修复 Windows 下 `Streamlit + Playwright` 的 `asyncio` 事件循环问题。
- 新增登录页面：`app/pages/05_login.py`。
- 新增登录脚本：
  - `scripts/zhilian_login.py`
  - `scripts/check_zhilian_login.py`

#### P4 初版：智联招聘原始页面采集

- 实现页面 HTML 快照保存：`recruitment_assistant/utils/snapshot_utils.py`。
- 实现 `raw_resume` 入库服务：`recruitment_assistant/services/raw_resume_service.py`。
- 实现智联当前页面采集：`capture_current_page()`。
- 实现手动逐页采集候选人页面：`capture_manual_resume_pages()`。
- 新增采集页面：`app/pages/06_zhilian_capture.py`。
- 新增采集脚本：
  - `scripts/capture_zhilian_page.py`
  - `scripts/capture_zhilian_manual_pages.py`

### 本地环境状态

- PostgreSQL 已安装并运行：`postgresql-x64-18`。
- 已创建数据库：`recruitment_assistant`。
- 已初始化 17 张表。
- Python 当前环境为 `3.10.6`，项目已兼容 `>=3.10`。
- 项目依赖已通过 `pip install -e .` 安装。
- 代码已通过：

```powershell
python -m compileall app recruitment_assistant scripts
```

### 当前可用命令

```powershell
python scripts/run_streamlit.py
python scripts/check_zhilian_login.py --account default
python scripts/zhilian_login.py --account default --wait 180
python scripts/capture_zhilian_page.py --account default --url "https://rd5.zhaopin.com/" --wait 30
python scripts/capture_zhilian_manual_pages.py --account default --max-pages 5
```

### 下一步建议

1. 使用 `scripts/capture_zhilian_manual_pages.py` 保存 3-5 个真实候选人详情页快照。
2. 基于快照分析智联详情页 DOM 结构。
3. 实现字段解析：姓名、联系方式、城市、学历、工作经历、教育经历、期望薪资等。
4. 将解析结果写入 `candidate`、`resume`、经历、技能等结构化表。
5. 再开发列表页自动采集与附件下载。
