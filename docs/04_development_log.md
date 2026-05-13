# 开发日志

## 2026-05-13

### V1.34 BOSS 采集页面与扩展识别增强

#### 已完成内容

- 优化 `app/pages/08_BOSS采集.py` 页面布局：
  - 将连接状态、测试轮次整合为“运行状态”紧凑栏目。
  - 将采集配置、采集统计、实时日志、候选人列表整合为“采集与结果”栏目。
  - 移除测试操作循环说明、BOSS 页面地址和日志文件名显示。
  - 将实时日志前置到第一屏内，候选人列表改为与采集任务页一致的表格展示。
  - 调整 WebSocket、扩展连接、BOSS 页面就绪 banner 等高展示，操作按钮等高、不换行、保持间距。

- 增强 Chrome Extension 采集链路：
  - `chrome_extension/background.js` 增加扩展版本上报、15 秒心跳、页面探测命令转发、内容脚本补注入和 Chrome 下载事件归属。
  - `chrome_extension/content.js` 收窄候选人列表、候选人详情区、附件简历按钮和简历下载按钮的 DOM 识别范围。
  - 增加候选人文本清洗和活跃状态过滤，降低姓名被识别为“刚刚活跃”的概率。
  - 增强附件简历按钮状态识别，区分可查看、可索要、已请求和状态未知。
  - 增强简历预览页右上角下载图标定位逻辑。

- 增强 BOSS WebSocket 服务与桥接日志：
  - `recruitment_assistant/services/ws_server.py` 增加连接序号、连接/断开时间、断开原因和连接快照。
  - `recruitment_assistant/services/boss_ws_bridge.py` 增加扩展心跳、页面重检、候选人扫描样例、附件按钮状态和下载保存日志。
  - BOSS 简历保存文件名按姓名、年龄、学历等字段规范化，继续与智联采集模块保持一致。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.34`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.8.0`。

#### 后续验证事项

- 需要在 `chrome://extensions/` 重新加载本地扩展，确认页面显示扩展版本 `1.8.0` 后再测试。
- 继续通过 BOSS 沟通页实测确认候选人姓名识别和附件简历下载是否稳定。

## 2026-05-12

### V1.23 BOSS Adapter/CDP 旧链路清理

#### 已完成内容

- 清理 BOSS 旧 Adapter/CDP 方案残留：
  - 删除 `recruitment_assistant/core/cdp_browser.py`。
  - 删除 `recruitment_assistant/platforms/boss/adapter.py` 与空包入口。
  - 删除 `scripts/boss_login.py`、`scripts/check_boss_login.py`、`scripts/download_boss_chat_resumes.py`。
  - 删除 `tests/test_cdp_browser.py`。

- 收敛 BOSS 当前架构到 Chrome Extension + WebSocket：
  - `recruitment_assistant/services/boss_ws_bridge.py` 移除 `BossAdapter` 注入与导入。
  - BOSS 简历保存继续由 `BossWSBridge` 根据扩展回传的 Chrome 下载路径完成。

- 清理配置残留：
  - 删除 `chrome_executable_path` 与 `boss_cdp_port` 配置项。
  - 删除根目录误生成空文件 `12.0`。

- 验证残留：
  - Python/JS/TOML 代码中已无 `BossAdapter`、`core.cdp_browser`、`connect_over_cdp`、`boss_cdp_port`、`chrome_executable_path`、`boss_login`、`check_boss_login`、`download_boss_chat_resumes` 残留引用。
  - 保留仍在使用的智联 `ZhilianAdapter` 与 Playwright 链路，不纳入本次 BOSS 旧链路删除范围。

- 合并远端提交后的二次清理：
  - `app/pages/05_平台登录.py` 移除合并带回的 BOSS 登录态、Cookie 导入、诊断与 `BossAdapter` 引用，仅保留智联登录设置。
  - `app/pages/06_智联采集.py` 移除合并带回的 BOSS Adapter 采集入口，采集任务页仅保留智联 Adapter 任务；BOSS 继续使用独立“BOSS采集”Chrome Extension + WebSocket 页面。

- 同步页面版本号：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.23`。

### V1.22 BOSS WebSocket 单例化与端口占用修复

#### 已完成内容

- 修复 `app/pages/08_BOSS采集.py` 中 WebSocket 服务按 Streamlit 会话重复创建的问题：
  - 将 `BossWSServer` / `BossWSBridge` 初始化从 `st.session_state` 改为 `st.cache_resource` 全局单例。
  - 避免多个浏览器会话或页面刷新时重复绑定 `127.0.0.1:8765`。
  - 修复页面显示 `WebSocket 服务：启动失败 [Errno 10048]`，但 Chrome 扩展实际显示“服务端已连接”的状态不一致问题。

- 同步页面版本号：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.22`。

### V1.21 BOSS采集页面紧凑布局优化

#### 已完成内容

- 优化 `app/pages/08_BOSS采集.py` 页面展示样式：
  - 去掉标题区域可能呈现的白色 banner 背景、边框和阴影。
  - 放大 `BOSS直聘采集` 标题字体，增强页面主标题识别度。
  - 所有信息区统一白色卡片背景。
  - 缩小指标、状态、日志、路径、说明文字字号。
  - 收紧卡片 padding、margin、按钮高度和日志区高度，提升单屏信息密度。
  - 在各栏目标题下增加分隔线，并在相邻栏目之间增加视觉分隔。

- 同步页面版本号：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.21`。

### V1.20 BOSS WebSocket 真实监听状态与扩展连接修复

#### 已完成内容

- 修复 `recruitment_assistant/services/ws_server.py`：
  - WebSocket 服务默认绑定从 `localhost` 改为 `127.0.0.1`，避免 Windows/Chrome 在 IPv6 `::1` 与 IPv4 `127.0.0.1` 间解析不一致。
  - 新增 `is_listening` 与 `startup_error`，用于区分“线程已创建”和“端口真实监听”。
  - `start()` 会等待端口服务真正创建，启动失败或超时会记录明确错误。
  - 服务线程异常会保存到 `startup_error`，便于页面诊断。

- 修复 `chrome_extension/background.js`：
  - WebSocket 地址从 `ws://localhost:8765` 改为 `ws://127.0.0.1:8765`。
  - 扩展连接上报版本升级为 `1.2.0`。

- 修复 `app/pages/08_BOSS采集.py`：
  - WebSocket 服务状态不再固定显示“运行中”。
  - 改为显示真实状态：`监听中` / `启动失败` / `未监听`，并展示 `127.0.0.1:8765` 或启动错误。

- 同步版本：
  - `chrome_extension/manifest.json` 更新为 `1.2.0`。
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.20`。

#### 测试提示

需要刷新 `BOSS采集` 页面让新的 WebSocket 服务对象初始化，再到 `chrome://extensions/` 重新加载扩展。页面应显示 `WebSocket 服务：监听中 127.0.0.1:8765`，随后显示 `扩展已连接`。

### V1.19 BOSS采集入口补全与连接故障说明

#### 已完成内容

- 修复 `app/components/layout.py` 左侧菜单缺少 `BOSS采集` 页面入口的问题。
- 顶部快捷导航新增 `BOSS采集` 链接，便于直接进入 Extension 测试页面。
- 明确 Extension 图标 `!` 与 `ws://localhost:8765` 连接拒绝的原因：必须先进入 `BOSS采集` 页面，页面初始化后才会启动本地 WebSocket 服务。
- 同步页面版本号：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.19`。

#### 测试提示

进入 `http://localhost:8501/BOSS采集` 后等待 WebSocket 服务启动，再在 `chrome://extensions/` 重新加载 `Boss直聘采集助手`，图标 `!` 应自动消失并显示扩展已连接。

### V1.18 Boss直聘 Extension 下载归属与识别增强

#### 已完成内容

- 增强 `chrome_extension/background.js`：
  - 扩展版本升级为 `1.1.0`。
  - 透传 Streamlit 下发的 `run_id`，使每轮测试的浏览器事件可归属到同一轮日志。
  - 增加 `download_intent` 登记机制，在页面点击下载前记录候选人上下文。
  - 接入 `chrome.downloads.onCreated` 和 `chrome.downloads.onChanged`，记录 Chrome 真实下载创建、完成和失败事件。
  - 下载完成后向 Python 回传 `download_id`、`download_path`、文件名、URL、MIME 和文件大小。

- 增强 `chrome_extension/content.js`：
  - 候选人列表扫描增加可见性、文本长度、候选人特征评分，降低误选聊天消息/页面元素的概率。
  - 附件简历按钮细分为 `view`、`request`、`requested`、`unknown_resume` 状态。
  - 对“要附件简历/索要附件简历”走跳过并记录 `resume_requested` 或 `resume_request_clicked`，避免误当成已下载。
  - 下载前先发送 `download_intent`，再点击下载按钮，由 background 监听真实下载完成。
  - 新增 `candidate_list_scanned`、`resume_button_found` 等诊断事件，方便通过 JSONL 分析页面识别问题。

- 增强 `recruitment_assistant/services/boss_ws_bridge.py`：
  - 识别并记录候选人列表扫描、附件按钮识别、下载意图登记和 Chrome 下载创建事件。
  - 对外部下载记录补充 `download_id` 和 `url`，便于排查真实下载归属。
  - 进度事件不再覆盖 Python 端已保存的真实下载计数，避免下载完成回调与页面预估计数竞争。

- 同步页面版本号：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.18`。

#### 当前状态

已完成进入真实页面测试前的主要开发准备。下一步需要在 Chrome 中重新加载扩展，并在 Boss 沟通页用“单步采集1人”验证 DOM 选择器、附件按钮状态和 `chrome.downloads` 回传路径是否符合预期。

### V1.17 Boss直聘 Extension 测试闭环与结构化日志

#### 已完成内容

- 增强 `recruitment_assistant/services/boss_ws_bridge.py`：
  - 每轮测试自动生成 `run_id`、开始时间、事件序号和 JSONL 日志文件。
  - 日志路径为 `logs/boss_extension/YYYYMMDD/run_<run_id>.jsonl`，记录命令下发、扩展事件、UI 日志、候选人跳过、简历保存和本轮摘要。
  - 增加 `reset_run()` 和 `get_run_summary()`，支持每轮测试快速重置与摘要分析。
  - 运行态增加扩展版本、Boss 页面 URL、最近事件时间、跳过原因统计等字段。

- 完善 `app/pages/08_BOSS采集.py`：
  - 新增“测试轮次”面板，展示 Run ID、开始时间、最近事件、日志事件数和 JSONL 日志文件路径。
  - 新增“重置本轮测试”和“生成本轮摘要”按钮。
  - 新增“单步采集1人”测试模式，便于逐个验证候选人点击、跳过和下载链路。
  - 新增“测试操作循环”清单，明确每轮测试的执行顺序。
  - 展示扩展版本、Boss 页面 URL 和跳过原因统计，便于快速定位问题。

- 同步页面版本号：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.17`。

#### 建议测试循环

1. 点击“重置本轮测试”，生成新的 Run ID 与 JSONL 日志。
2. 打开 Boss 沟通页，确认扩展连接和页面就绪。
3. 先使用“单步采集1人”，验证事件链路是否完整。
4. 点击“生成本轮摘要”，根据下载数、跳过原因和最后事件定位问题。
5. 修改问题后重新加载扩展或刷新 Boss 页面，进入下一轮 Run ID。
6. 单步稳定后切换连续采集，并逐步扩大最大采集数量。

### V1.16 Boss直聘 Chrome Extension 采集方案

#### 背景

经实测确认：Boss直聘能检测 Playwright 的 CDP 连接（即使通过 `connect_over_cdp` 连接真实 Chrome），触发页面后退。纯净 Chrome 启动后页面正常。因此改用 Chrome Extension 方案——在页面内部执行自动化操作，通过 WebSocket 与 Python 后端通信，在 Streamlit 面板统一管理。

#### 已完成内容

- **Chrome Extension**（`chrome_extension/`）：
  - Manifest V3 扩展，自动注入 Boss 沟通页（`www.zhipin.com/web/chat*`）
  - `background.js`：Service Worker，管理与 Python 后端的 WebSocket 连接，转发指令和事件
  - `content.js`：核心采集逻辑，在 Boss 页面内执行 DOM 操作（遍历候选人、点击附件简历、触发下载）
  - `popup.html/js`：扩展弹窗，显示连接状态
  - 支持开始/暂停/继续/停止采集指令

- **WebSocket 服务端**（`recruitment_assistant/services/ws_server.py`）：
  - 后台线程运行 asyncio WebSocket 服务（默认 `localhost:8765`）
  - 线程安全的指令下发接口，供 Streamlit 调用
  - 单连接模式，自动处理扩展断线重连

- **事件桥接层**（`recruitment_assistant/services/boss_ws_bridge.py`）：
  - 将扩展上报的事件（候选人点击、简历下载、跳过、进度）转化为业务操作
  - 管理运行时状态（日志、候选人列表、下载计数）
  - 下载文件自动移动到 `data/attachments/boss/` 并按规范重命名

- **Streamlit 控制页面**（`app/pages/08_BOSS采集.py`）：
  - 连接状态实时显示（WebSocket 服务、扩展连接、Boss 页面就绪）
  - 采集配置（最大数量、点击间隔）
  - 开始/暂停/继续/停止控制按钮
  - 实时日志面板和候选人列表
  - 首次使用安装指引

- **CDP 浏览器模块**（`recruitment_assistant/core/cdp_browser.py`）：
  - 通过 subprocess 启动 Chrome + `connect_over_cdp` 连接（保留作为备用方案）
  - Chrome 路径自动检测（Windows）
  - 端口占用检测和已有实例复用

- **BossAdapter 增强**：
  - 新增 `save_downloaded_resume()` 方法供 WS bridge 调用
  - 会话创建方式改为 CDP 连接（备用方案）

- **Boss 脚本**（`scripts/`）：
  - `boss_login.py`：纯净 Chrome 启动登录（无自动化注入）
  - `check_boss_login.py`：登录态检测
  - `download_boss_chat_resumes.py`：简历下载（CDP 方式，备用）

- **配置新增**：
  - `chrome_executable_path`：Chrome 路径（可选，自动检测）
  - `boss_cdp_port`：CDP 端口（默认 9222）
  - `websockets>=12.0` 依赖

- **单元测试**（`tests/test_cdp_browser.py`）：8 项测试全部通过

#### 架构

```
Streamlit UI (08_BOSS采集) ←→ WebSocket Server (localhost:8765) ←→ Chrome Extension ←→ Boss页面
```

#### 验证状态

- 所有 Python 模块导入正常
- WebSocket 服务启动/停止/通信测试通过
- Chrome Extension JS 语法验证通过
- 智联模块完全未受影响
- 8 项单元测试通过

#### 使用流程

1. 启动 Streamlit → 打开「BOSS采集」页面
2. Chrome `chrome://extensions/` → 开发者模式 → 加载 `chrome_extension/`
3. Chrome 打开 Boss 沟通页（需已登录）
4. Streamlit 面板显示「扩展已连接」→ 点击「开始采集」

### V1.15 BOSS 登录策略重构与多平台采集增强

#### 已完成内容

- 新增 BOSS 直聘平台适配器与多平台采集入口：
  - 新增 `recruitment_assistant/platforms/boss/adapter.py`，支持 BOSS 沟通页候选人扫描、附件简历按钮识别、浏览器下载保存、下载前候选人信息去重与诊断日志。
  - `app/pages/06_智联采集.py` 扩展为多平台采集页，支持 BOSS/智联平台选择、平台目录打开、平台级去重索引清理与历史任务按平台展示。
  - `recruitment_assistant/services/crawl_task_service.py` 增强平台候选人记录，支撑多平台下载前去重。
- 重构 BOSS 登录态判断与失败恢复：
  - 分离“登录态文件存在”和“真实已登录”，系统设置页显示 `已保存，待检测`，采集页显示 `已保存，待验证`。
  - 收紧 BOSS 已登录判定：必须进入有效 `/web/chat` 沟通页，并检测沟通相关 DOM/文本标记。
  - 增加 `/web/user`、安全验证页、`about:blank`、非沟通页等异常识别，避免误报“任务完成，扫描 0 人，保存 0 份”。
- 增加 BOSS 登录诊断能力：
  - `BossAdapter.diagnose_login_navigation()` 记录页面导航、标题、文本长度、是否空白页、是否登录页、是否已认证等诊断事件。
  - 系统设置页新增 `BOSS 登录诊断` 和诊断结果展示，定位出 BOSS 首页/城市站/安全验证页之间的反复跳转。
- 废弃 Playwright 打开 BOSS 登录页的手动登录方案：
  - 根据实测，Playwright 启动的 BOSS 登录页会触发安全验证、反复后退或跳空白页，无法稳定完成人工登录。
  - BOSS 登录改为“外部真实浏览器登录 + Cookie JSON 导入”方案。
  - 新增 `BossAdapter.import_cookies_from_json()`，支持导入 `zhipin.com` 域名 Cookie 并生成 Playwright `storage_state`。
  - 系统设置页新增 `BOSS Cookie JSON` 输入框和 `导入 BOSS Cookie 登录态` 按钮。
  - BOSS 采集缺失/失效登录态时不再自动打开 Playwright 登录窗口，而是提示先在系统设置中导入外部浏览器 Cookie。
- 同步页面版本号：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.15`。

#### 验证状态

- 已通过编译检查：

```powershell
python -m compileall app recruitment_assistant
```

- `read_lints` 检查本轮核心文件无新增错误；仅保留既有未使用提示，不影响运行。

#### 下一轮计划

- 评估并实施 CDP 连接真实 Chrome 方案：用户手动启动 Chrome 并完成 BOSS 登录，系统通过 `connect_over_cdp("http://localhost:9222")` 连接当前页面进行登录检测和后续半自动采集。
- 保留 Cookie 导入作为备用登录态导入方式。

### V0.91 智联采集残留清理与边界校准

#### 已完成内容

- 清理附件状态旧逻辑残留：
  - 删除 `recruitment_assistant/platforms/zhilian/adapter.py` 中已不再调用的 `_has_attachment_message_hint()`。
  - 保持 `_wait_for_requested_attachment_ready()` 只等待右下角 `查看附件简历` 按钮，不再读取聊天正文文本作为附件 ready 判断。
- 清理智联采集页岗位字段残留：
  - `app/pages/06_智联采集.py` 丢弃未使用的签名岗位返回值，明确左侧签名岗位不再覆盖右侧详情岗位。
- 同步更新页面版本号：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V0.91`。

#### 验证状态

- 残留关键词复查：`_has_attachment_message_hint` 已无命中；`APP_VERSION` 已为 `V0.91`。
- 已通过编译检查：

```powershell
python -m py_compile "recruitment_assistant/platforms/zhilian/adapter.py" "app/pages/06_智联采集.py" "app/components/layout.py"
```

## 2026-05-11

### V0.74 智联聊天附件采集去重与附件归属修复

#### 已完成内容

- 修复 `V0.73` 日志暴露的附件链接未捕获统计缺口：
  - `recruitment_assistant/platforms/zhilian/adapter.py` 在候选人点击 `要附件简历` / `查看附件简历` 后仍未捕获 `pending_urls` 或 `pending_downloads` 时，不再只输出诊断日志。
  - 新增 `skip_stage="attachment_url_not_captured"`，通过 `on_resume_skipped` 计入跳过统计。
  - `app/pages/06_智联采集.py` 新增跳过原因展示：`附件链接未捕获`。
- 修复附件 URL / 下载内容归属污染风险：
  - 不再将非当前候选人页面的下载请求、下载响应、浏览器原生下载事件自动补入当前候选人范围。
  - 对来源页不属于当前候选人的附件请求/响应/下载事件直接丢弃并记录诊断日志。
  - 新增本轮 `content_hash` 归属保护：若不同候选人下载到相同附件内容 hash，判定为疑似归属污染，删除本地临时附件并拒绝保存为成功候选人。
- 修复历史同名但年龄漂移导致重复下载的问题：
  - 保留强去重键 `姓名 + 年龄 + 学历`。
  - 新增弱去重键 `姓名 + 学历`，当年龄变化但姓名和学历一致时，通过 `weak_hit=True` 拦截。
  - 采集日志新增 `weak_hit`、`weak_key_hash`，便于判断是否由弱去重命中。
- 增强下载前个人信息历史诊断：
  - `历史同名` 不再只展示 `pre_key`，改为展示姓名、年龄、学历、key 前缀和 `task_id`。
  - `platform_candidate_record` 写入时补充 `candidate_signature`、`job_title`、`phone`、`resume_file_name`、`content_hash`，便于后续排查重复与附件归属问题。
- 同步更新页面版本号：`app/components/layout.py` 中 `APP_VERSION` 更新为 `V0.74`。

#### 验证状态

- 已通过编译检查：

```powershell
python -m py_compile "d:/Users/walker/Documents/walker/Videcode/Recruitment-Assistant/recruitment_assistant/platforms/zhilian/adapter.py" "d:/Users/walker/Documents/walker/Videcode/Recruitment-Assistant/app/pages/06_智联采集.py" "d:/Users/walker/Documents/walker/Videcode/Recruitment-Assistant/app/components/layout.py"
```

- `read_lints` 检查 `adapter.py`、`06_智联采集.py`、`layout.py` 无新增错误。

#### 下一轮验证重点

- 观察是否出现 `weak_hit=True` 并正确拦截历史同名年龄漂移候选人。
- 观察 `附件链接未捕获` 是否正确进入累计跳过数。
- 观察是否仍出现不同候选人相同 `content_hash` 或相同电话的附件归属异常。

### 简历管理页、解析入库与调试清库

#### 已完成内容

- 将原 `app/pages/07_简历下载解析.py` 改造为简历管理页：
  - 按 `data/attachments/zhilian/YYYYMMDD` 日期目录加载已保存简历。
  - 展示加载概览：总数、已解析、待解析、失败数。
  - 支持批量解析 PDF/DOC/DOCX，失败自动重试 1 次。
  - 按文件 `SHA256` 去重，重复文件自动跳过。
  - 对解析结果进行清洗、规范化并写入 `raw_resume`、`candidate`、`resume`、`resume_attachment`、`resume_skill`。
  - 展示原始简历列表，包含解析状态、失败原因、原文链接。
  - 恢复并保留 `导出 Excel` 功能。
- 增加临时调试按钮 `调试：清除当前解析库`：
  - 清除当前智联解析库相关的 `resume_score`、`resume_tag`、`resume_skill`、`project_experience`、`education_experience`、`work_experience`、`resume_attachment`、`resume`。
  - 删除 `raw_resume` 前先清理 `platform_candidate_record`，避免外键约束报错。
  - 删除不再被任何简历引用的孤立 `candidate`。
  - 清除后重置页面状态并刷新解析状态。
- 修复解析入库异常：
  - `Decimal` 写入 JSONB 导致 `Object of type Decimal is not JSON serializable`，新增 `json_safe()` 递归转换。
  - 年份被误识别为工作年限导致 `NumericValueOutOfRange`，限制工作年限仅接受 `0~80.0`。
  - 清除解析库时 `raw_resume` 被 `platform_candidate_record` 引用导致外键报错，补充依赖表删除顺序。
- 提升 `recruitment_assistant/parsers/pdf_resume_parser.py` 解析准确率：
  - 增强姓名提取，避免把章节标题、城市、状态词、项目标题误识别为姓名。
  - 增强城市提取，优先识别 `工作地区`、`目标地点`，避免籍贯覆盖求职城市。
  - 增强期望职位提取，支持同一行多标签文本中的 `期望职位`。
  - 增强当前公司/职位提取，限定在工作/实习经历区域，避免教育经历和项目描述误填。
  - 扩展技能词典，补充机器学习、深度学习、RAG、大模型、LangChain、Milvus 等技能。
- 移除“信息测试”页面入口，`app/components/layout.py` 版本更新为 `V0.36`。

#### 验证状态

- `python -m py_compile app/pages/07_简历下载解析.py` 已通过。
- `python -m py_compile recruitment_assistant/parsers/pdf_resume_parser.py` 已通过。
- `app/pages/07_简历下载解析.py`、`recruitment_assistant/parsers/pdf_resume_parser.py` lint 均无错误。
- 已使用 `data/attachments/zhilian/20260511` 下样例 PDF 重新验证解析结果：姓名、当前公司、当前职位、期望职位、城市等核心字段明显改善。

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
