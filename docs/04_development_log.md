# 开发日志

## 2026-05-10

### 采集任务稳定性与重复候选人快速跳过优化

#### 已完成内容

- 修复 `app/pages/06_智联采集.py` 任务信息面板运行中刷新不稳定的问题：
  - `sync_runtime_to_session()` 同步 `logs/candidates` 时改为复制列表，避免后台线程原地更新导致 Streamlit 状态变更检测不稳定。
  - `append_collect_log()` 同步日志时也改为复制列表。
  - 运行中跳过历史批次任务列表渲染，减少每秒刷新时的数据库查询和页面负担。
- 继续优化重复候选人跳过流程：
  - 任务启动时一次性加载 `platform_candidate_record` 历史去重索引到内存。
  - `should_skip_resume` 去除逐条数据库查询，仅使用内存索引判断，避免下载后兜底去重再次查库。
  - `ZhilianAdapter._click_next_uncontacted_candidate()` 支持在候选人卡片扫描阶段调用 `should_skip_candidate_signature`。
  - 命中重复候选人时不再点击候选人卡片、不再等待详情加载、不再进入附件按钮流程，直接记录跳过并继续下一个候选人。
  - 缩短采集等待：重复跳过 `50ms`、候选人点击后 `800ms`、点击 `要附件简历` 后 `800ms`、附件轮询 `500ms`、滚动查找等待 `600ms`。
- 增强候选人求职岗位清洗：
  - 从 `工作经历 （2年） 神州数码系统集成服务有限公司深圳分公司 · 需求分析工程师` 中抽取 `需求分析工程师`。
  - 同步增强 `app/pages/06_智联采集.py`、`recruitment_assistant/platforms/zhilian/adapter.py`、`recruitment_assistant/parsers/pdf_resume_parser.py`、`recruitment_assistant/extractors/scrapling_candidate_extractor.py`。

#### 验证状态

- `python -m compileall` 已通过相关文件编译检查。
- `app/pages/06_智联采集.py`、`recruitment_assistant/platforms/zhilian/adapter.py`、`recruitment_assistant/parsers/pdf_resume_parser.py`、`recruitment_assistant/extractors/scrapling_candidate_extractor.py` lint 均无错误。

### 当前阶段

项目推进到 `P6：Streamlit 控制台体验重构 + 新建采集任务端到端闭环`。在已有自动采集流程基础上，整合首页入口、新建任务弹窗、登录态判断与自动采集执行，完成「首页一键启动 → 登录（按需）→ 自动点击采集 → 入库 → 跳转任务页」闭环。

### 已完成内容

#### 全局布局与导航（`app/components/layout.py`）

- 重写左侧侧边栏菜单，从不可点击 `div` 改为真实 `<a target="_self">` 链接：
  - `首页` → `/`
  - `采集任务` → `/智联采集`
  - `简历管理` → `/简历下载解析`
  - `系统设置` → `/平台登录`
- 删除 `标签`、`导出`、`帮助` 菜单项。
- 顶部快捷入口（`vibe-actions`）同步改为 `<a>` 链接，添加 hover 与去下划线样式。
- 新增侧边栏链接 `text-decoration:none` 与 active 样式，避免默认链接颜色与下划线。

#### 首页（`app/main.py`）

- 切换为统一 `vibe` 风格首页，三张主卡片：`采集任务`、`简历管理`、`数据导出`。
- 卡片按钮改为内部 HTML 链接（`vibe-primary-btn` / `vibe-outline-btn`），保持所有卡片样式一致。
- 新增 `新建采集任务` 弹窗（`@st.dialog(width="small")`），可配置：
  - 目标网站：`智联招聘`（占位单选）。
  - 采集目标：`指定数量简历`（数量 1-500）/ `所有新简历`（搜索时间 10-900 分钟，10 分钟步长）。
  - 采集速度：`快速采集（5-15s间隔）` / `慢速采集（10-45s间隔）`。
  - 账号标识：默认 `default`。
- 通过 `st.query_params["new_task"]=1` 触发弹窗，URL 不残留。
- 弹窗按钮根据 `adapter.state_path.exists()` 动态切换文案：
  - 有登录态 → `已登录开始任务`
  - 无登录态 → `登录并开始任务`
- 点击逻辑：
  - 无登录态先调用 `adapter.login_manually(wait_seconds=900, keep_open=False)`。
  - 直接调用之前已测试通过的自动采集流程 `adapter.auto_click_chat_attachment_resumes(...)`：
    - `max_resumes` 由 `指定数量简历` 决定，`所有新简历` 时回落到 `settings.crawler_max_resumes_per_task`。
    - `wait_seconds = 搜索时间分钟 * 60`（指定数量时默认 900 秒）。
    - `per_candidate_wait_seconds`：快速 45 秒，慢速 90 秒。
  - 采集结果通过新增的 `save_raw_resume_rows()` 经 `RawResumeService.create_raw_resume(RawResumeCreate(**row))` 入库。
  - 完成后将 `任务状态`、`已保存简历数`、`raw_resume_ids` 写入 `st.session_state.pending_collect_task`，调用 `st.switch_page("pages/06_智联采集.py")` 跳转。
- 异常处理：
  - 登录取消（`登录窗口已关闭或登录流程已取消`）静默处理，不在页面输出错误。
  - 其他异常 `st.error(...)` 提示后清理状态并 `st.rerun()` 重置弹窗。

#### 采集任务页（`app/pages/06_智联采集.py`）

- 激活菜单从 `采集` 改为 `采集任务`。
- 移除原 `打开页面并保存快照` 调试按钮与未使用的 `ZhilianAdapter` 导入。
- `pending_task` 展示改为「最近任务」卡片，提示自动采集已完成并显示已保存份数（`已保存简历数`）。

#### 系统设置页（`app/pages/05_平台登录.py`）

- 激活菜单 `inject_vibe_style("系统设置")`（`系统设置页` → `系统设置`）。
- 在账号 Tab 下新增 `开发工具` 卡片，提供 `智联页面快照保存` 按钮：
  - 调用 `adapter.capture_current_page(...)` 并入库 `raw_resume`。
  - 替代原本散落在采集任务页的 `打开页面并保存快照` 调试入口。
- 修复 `replace_in_file` 多处命中导致开发工具块重复出现的问题（用 `write_to_file` 重写，仅保留账号 Tab 一份）。

#### 平台适配器（`recruitment_assistant/platforms/zhilian/adapter.py`）

- 引入 `playwright.sync_api.Error as PlaywrightError`。
- `login_manually()` 捕获 `PlaywrightError` 并转换为 `RuntimeError("登录窗口已关闭或登录流程已取消")`，使前端可识别并静默处理用户取消登录。

#### 其他菜单同步

- `app/pages/02_候选人管理.py`、`app/pages/07_简历下载解析.py` 的 `inject_vibe_style` 由 `简历` 改为 `简历管理`。

### 本次重点修复

- 修复自定义侧边栏所有菜单点击无效（不可导航）的问题。
- 修复登录放弃/关闭浏览器窗口时页面抛出 Playwright 堆栈错误的问题。
- 修复采集任务卡按钮尺寸过大、撑满宽度、与其他卡片样式不一致的问题。
- 修复系统设置页 `开发工具` 块被错误复制到多个 Tab 下的问题。
- 修复弹窗 `登录开始任务` 在已有登录态时仍重新弹浏览器登录的问题。

### 验证状态

代码已通过编译与 lint 检查：

```powershell
python -m compileall app recruitment_assistant/platforms/zhilian/adapter.py
```

`read_lints` 在 `app/main.py`、`app/pages/06_智联采集.py`、`app/pages/05_平台登录.py` 上均无错误。

### 当前可用入口

```powershell
python scripts/run_streamlit.py
```

- 首页 → `采集任务` 卡 → `新建任务` → 弹窗配置 → 按登录态自动选择 `已登录开始任务` / `登录并开始任务` → 自动采集 → 跳转 `采集任务` 页查看最近任务与历史列表。
- 系统设置 → `账号` Tab → `开发工具` → `智联页面快照保存`，用于开发期保存 HTML 快照入库。

### 下一步建议

1. 在 `采集任务` 页将 `pending_collect_task` 中的 `raw_resume_ids` 与任务列表打通，支持快速跳转到对应简历记录。
2. 将快速/慢速采集映射的 `per_candidate_wait` 与 `auto_click_chat_attachment_resumes` 内部点击节奏（5-15s / 10-45s 间隔）打通，由 adapter 接收 `min_interval`、`max_interval` 参数实现真正的随机等待。
3. 在弹窗启动后增加任务进度提示（基于 `on_resume_saved` 回调实时更新已保存数量）。
4. 增加多账号、多平台扩展位（目前只接入智联）。

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
