# 51 前程无忧：沟通职位识别 + 文件名加职位 + 沟通中导航修复

日期：2026-05-24
平台：51 前程无忧（qiancheng / ehire）
版本：扩展 2.25.0 → 2.30.0；bridge 1.11.0 → 1.16.0

## 一、改动清单

### 扩展（chrome_extension）

- `chrome_extension/manifest.json`
  - `version` 由 `2.25.0` 升至 `2.30.0`，让 Chrome 真正重载 content script。

- `chrome_extension/content.js`
  - `CONTENT_SCRIPT_VERSION` 同步升至 `2.30.0`。
  - 新增 `extractQianchengTalkingPosition()`：用确认 selector
    `#sensor_Bchatinfo_switch` / `.change-position` 直命中沟通职位文本，
    fallback 走 `.chat-user-info / .info-main / ...` 容器 + 严格正则。
  - 新增 `simplifyQianchengTalkingPosition()`：与 bridge `_simplify_talking_position`
    规则对齐（剥括号 / 切首段 / 限 8 字符 / 拒绝纯标点空白）。
  - 新增 `waitForQianchengCandidateDetailReady(prevName)`：以 `.info-main` 姓名节点
    与上轮值不同作为右侧面板切换信号，最长 1.8s + 额外 350ms 渲染窗口，
    避免抓到上一个候选人残留的"沟通职位"。
  - 修复 `ensureQianchengOnChattingPage()`：移除"列表容器存在 → 已在沟通中"的
    短路判定，改为每轮强制点击 `#sensor_talentcommunicate` 主菜单 +
    `#sensor_Bchat_communication` Tab，并用三信号 ≥ 2 投票判定就绪
    （tab 激活 / `.list-item` 出现 / URL+title+面包屑命中沟通中关键词）。
  - 新增 `isQianchengTabActive` / `looksLikeQianchengChattingRoute` 两个就绪辅助函数。
  - 在采集循环里 emit 两类新事件：
    - `qiancheng_talking_position`（识别成功，含 raw + simplified）
    - `qiancheng_talking_position_skip`（未抓到 / 简化后空，含 reason + raw）
  - emit `qiancheng_navigation_status`（`on_chatting_page` / `navigation_failed`）。
  - `info.talking_position` / `info.talking_position_raw` 写入 candidate_clicked 事件。

### Bridge（recruitment_assistant/services）

- `extension_contract.py`：`EXPECTED_EXTENSION_VERSION` 与
  `EXPECTED_CONTENT_SCRIPT_VERSION` 同步升至 `2.30.0`。
- `qiancheng_ws_bridge.py`：
  - `QIANCHENG_BRIDGE_VERSION = "1.16.0"`。
  - 新增字段 `_talking_position_by_sig: dict[str, str]`，
    `start_run` 与 `_finalize_run` 处一并清空。
  - 新增事件 case `qiancheng_navigation_status` → 「已进入沟通中页面」/「导航失败」
    (info / warning 两级)。
  - 新增事件 case `qiancheng_talking_position` → 「识别沟通职位」缓存到
    `_talking_position_by_sig`，关键词「沟通职位」触发蓝色日志。
  - 新增事件 case `qiancheng_talking_position_skip`：默认 debug 级，
    仅 `raw_empty_after_simplify` 才升 info（暴露简化函数潜在 bug）。
  - 文件名拼接处补 fallback：`talking_position` 为空时回查
    `_talking_position_by_sig[candidate_sig]` 再退到 `job_title`。

### 页面（app/pages）

- `app/pages/09_51前程无忧采集.py`：
  - `classify_boss_log` token 集合补充「沟通职位」→ 蓝色 stat 类。
  - 主区与采集面板两处 `.boss-log-stat` CSS 由 `var(--color-primary)` 改为硬编码
    `#2563eb !important`，避免被全局主题色覆盖。

## 二、关键设计决定

1. **`#sensor_Bchatinfo_switch` 单 selector 直命中**
   早期版本曾在右上区域用坐标扫描 + 多个 fallback selector，导致抓错（命中一行
   广告 / 标签）。本轮把 51 沟通页面 DOM 看清楚后发现该 ID 直接就是职位文本节点，
   `textContent.trim()` 即可，去掉冗余的 prefix 剥离逻辑。

2. **简化规则与智联对齐 + 文件名模板统一**
   bridge 的 `_simplify_talking_position` 与扩展的 `simplifyQianchengTalkingPosition`
   两份实现都遵守相同三条规则：剥括号、切首段、限 8 字符。文件名格式与
   `zhilian_ws_bridge` 完全一致：
   `{name}-{age}-{education}-{position}-51前程无忧-{date}-{seq}.pdf`。

3. **导航判定从"列表容器存在"改为三信号 ≥ 2 投票**
   旧判定被"全部候选人"页同名容器骗过，导致整个 ensure 函数空转，
   后续 `resume_preview_not_found` 报错。三信号容忍单一 DOM 改版，且对
   "已经在沟通中"的二次激活无害。

4. **skip 事件 debug 化降噪**
   sensor selector 已确认稳定后，`talking_position_skip` 默认走 logger.debug，
   只把 `raw_empty_after_simplify` 这种逻辑异常路径升 info，UI 日志面板不再被刷屏。

## 三、版本号变化

| 模块 | 旧 | 新 |
| --- | --- | --- |
| `chrome_extension/manifest.json` | 2.25.0 | 2.30.0 |
| `chrome_extension/content.js` (`CONTENT_SCRIPT_VERSION`) | 2.25.0 | 2.30.0 |
| `extension_contract.py` (`EXPECTED_EXTENSION_VERSION` / `EXPECTED_CONTENT_SCRIPT_VERSION`) | 2.25.0 | 2.30.0 |
| `qiancheng_ws_bridge.py` (`QIANCHENG_BRIDGE_VERSION`) | 1.11.0 | 1.16.0 |

## 四、验证状态

- 沟通职位识别 + 文件名拼接：已在曾德渝 / 梁小冰 两份归档简历上跑通，文件名包含
  正确职位段，蓝色日志显示「识别沟通职位」。
- 文件名模板与智联对齐：✅。
- 导航修复（`ensureQianchengOnChattingPage` v2.30.0 三信号投票）：尚未在
  "全部候选人"误入场景下重跑，仅做静态代码审查 + console 日志。
  下一轮启动若仍出现 `resume_preview_not_found`，应先检查浏览器 console
  `[qiancheng nav]` 前缀日志再调整。
