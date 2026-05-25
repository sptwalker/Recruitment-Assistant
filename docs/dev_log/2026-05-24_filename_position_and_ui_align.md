# 三平台文件名加沟通职位 + UI 对齐智联 + 状态颜色统一深绿

- 日期：2026-05-24
- 范围：BOSS / 51 前程无忧 / 智联 三平台采集页 + 三 bridge + Chrome 扩展 + 单一真相源契约模块

## 改动清单

### 扩展（Chrome Extension）

- `chrome_extension/content.js`（+402 / -157 行）
  - `CONTENT_SCRIPT_VERSION` 2.14.0 → 2.25.0
  - 新增 `extractBossTalkingPosition()` + `simplifyBossTalkingPosition()`：从右侧顶部红框区域抓"沟通的职位 - XXX"，做去括号 / 取斜杠前段 / 取中文 / 截断 8 字简化后通过 `boss_talking_position` 事件 emit 给 bridge
  - `clickBossChatMenu()` 新增：先点左侧"沟通"主菜单，再走 `clickBossChattingTab()`，两步均改为 `clickElementDirect`（避免 closest 上跳到错误兄弟节点，参考记忆 zhilian_click_must_be_direct）
  - `resetCandidateListScroll()` 增加 anchor / 多滚动容器枚举 + 详细诊断 emit（`scroll_reset_detail`）
  - `getCandidateItems()` 命中 `.geek-item-wrap` 时跳过 scoring 直接信任结构 + diagnostic snapshot
  - `findBossSvgDownloadIcon()`：仅在没有任何预览根时才 fallback 到 `document.body`，避免在聊天列表 DOM 上做全局 `[class*='card-btn']` 扫描卡死主线程；整体包 try/catch + emit `boss_svg_scan_error`
  - `finalizeDownloadWithPersistAck` 超时 12s → 25s；`persist_ack_timeout` 视为已下载成功（Chrome resultPromise 已 ok），返回 true 让上层结束本候选人，避免重复点击下载
  - 候选人主循环加 try/catch：捕获 `candidate_processing_error` 并 `skipCandidate("per_candidate_exception")`
  - 移除 `isAuthenticated()` / `isBossPageDetected()` 文本标记检查（React 异步渲染 + 平台改文案易误报"未检测到登录态"）；`emitPageStatus()` 改为只看 hostname 匹配
- `chrome_extension/manifest.json`：version 2.14.0 → 2.25.0

### Bridge（Python WebSocket 服务）

- `recruitment_assistant/services/extension_contract.py`（**新文件，纳入 git**）
  - 抽出 `EXPECTED_EXTENSION_VERSION` / `EXPECTED_CONTENT_SCRIPT_VERSION` 单一真相源 = "2.25.0"
  - 三 bridge 共用，content.js / manifest 改一行只需在此 bump 一次
- `recruitment_assistant/services/boss_ws_bridge.py`（+48 / -？）
  - `BOSS_BRIDGE_VERSION` → 1.97.0
  - 新增 `_simplify_talking_position` 静态方法
  - 新增 `_talking_position_by_sig` dict 缓存，监听 `boss_talking_position` 事件写入
  - 文件名拼装链：candidate signature → 缓存 lookup → `{姓名}-{年龄}-{学历}[-{沟通职位}]-BOSS直聘-{YYYYMMDD}-{HHMMSS}-{编号:03d}.pdf`
  - 归档成功日志统一文案："文件下载成功并保存归档"
- `recruitment_assistant/services/qiancheng_ws_bridge.py`（+30 / -？）
  - `QIANCHENG_BRIDGE_VERSION` → 1.11.0
  - 沟通职位 fallback 链：`candidate_info.talking_position → candidate_info.job_title`
  - 归档日志统一文案对齐
- `recruitment_assistant/services/zhilian_ws_bridge.py`（+10 / -？）
  - 直接读 `candidate_info.talking_position`
  - 归档日志文案对齐为"文件下载成功并保存归档"
- `recruitment_assistant/services/test_run_watchdog.py`（+1 行）
  - `CANDIDATE_TERMINAL_EVENTS` 加入 `resume_persist_rejected`：ack 超时 / hash 冲突等也视为终态，避免 watchdog 在已结束的下载链上继续追

### 页面（Streamlit Pages）

- `app/pages/08_BOSS采集.py`（+110 / -？）/ `app/pages/09_51前程无忧采集.py`（+41 / -？）
  - 参考智联：Run ID 列改为"去重记录数"，删除下方"当前去重记录数" banner
  - 删除"索要简历" checkbox 与 `request_resume_if_missing` 参数
  - 候选人列表 title 后信息精简（沿用智联 `build_candidate_summary` 风格）
  - 实时任务窗口新增 CSS 类 `boss-log-success` / 51 对应类，深绿色 `#0a7d2e`；classify 函数加 token "保存归档"
  - 黄色信息统一改为琥珀色 `#b45309`
- `app/pages/06_智联采集.py`（+16 / -？）
  - 颜色规范同步对齐：WebSocket 监听 / 扩展已连接 / 页面就绪 → 深绿 `#0a7d2e`
  - CSS 双层维护：外层 markdown + iframe `render_auto_scroll_html` 各一份，避免单层修改后 iframe 仍走旧色

### 文档脚本

- `scripts/backfill_candidate_from_filename.py`（**新文件，纳入 git**）
  - 前期文件名回填脚本，从已归档文件名解析"姓名-年龄-学历-沟通职位"回写到 candidate 记录
- `docs/dev_log/2026-05-24_filename_position_and_ui_align.md`（本文件）

## 关键设计决定

1. **沟通职位字段获取链（按平台差异）**
   - BOSS：右侧顶部红框区域无固定 selector，扩展端用文本扫描"沟通的职位 - XXX"，emit 专用事件 `boss_talking_position` 让 bridge 缓存按 candidate signature 查；候选人列表卡片不展示沟通职位，只能在右侧详情打开后采集
   - 51 前程无忧：扩展端 candidate_info.talking_position 不一定有，bridge fallback 到 `candidate_info.job_title`
   - 智联：candidate_info.talking_position 已稳定，bridge 直接读

2. **三平台归档日志统一文案**：所有 bridge 在简历落盘成功后均输出"文件下载成功并保存归档"。页面侧 classify 函数把"保存归档"作为深绿色（`boss-log-success` / 同名类）的 token，无需匹配多种文案

3. **颜色规范 + CSS 双层维护**
   - 深绿 `#0a7d2e`：WebSocket 监听 / 扩展已连接 / 页面就绪 / 简历归档成功
   - 琥珀 `#b45309`：警告信息（替代之前散落的多种黄色）
   - **双层维护规则**：每个页面有外层 markdown 注入的 CSS + iframe `render_auto_scroll_html` 内自带的 CSS，改色必须两处同步，否则实时任务窗口（iframe）会保留旧色

4. **Triple-bump 体现**
   - boss bridge 1.95.0 → 1.97.0
   - qiancheng bridge → 1.11.0
   - 扩展 content.js + manifest 同步 2.14.0 → 2.25.0
   - 单一真相源 `extension_contract.EXPECTED_EXTENSION_VERSION` = "2.25.0"，三 bridge 共用，遵循记忆 feedback_bump_version_on_module_change 与 feedback_codify_extension_learning

## 验证状态

- 静态：代码已落盘、版本号三处一致（content.js / manifest / extension_contract）、bridge 与页面均已 bump
- 端到端：本轮"先固化提交，后续约时间 UI 验证"

## 待办

- UI 验证三平台端到端：
  - BOSS：登录 → 沟通菜单 → 沟通中 tab → 候选人列表回顶 → 抓沟通职位 → 下载 → 归档文件名包含沟通职位 → 实时任务窗口看到深绿色"文件下载成功并保存归档"
  - 51 前程无忧：candidate_info.job_title fallback 链生效 / 黄色 → 琥珀色
  - 智联：双 CSS 层颜色一致 / 实时任务窗口归档行深绿
- 跑一次 `scripts/backfill_candidate_from_filename.py` 对历史归档做回填（按需）
