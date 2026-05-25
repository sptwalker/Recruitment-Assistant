# 开发日志

## 2026-05-23

### 智联采集闭环：扩展 v1.88.0 → v2.14.0，bridge v1.1.0 → v1.21.0

本日围绕智联采集页面（沟通中聊天页）做了一整轮迭代，从"附件下载失败 / 30s 超时"一路改到"10/10 全成功 + 文件名带简化沟通职位 + 日志精简琥珀化"。

**核心 bug 修复链路**

- **附件主动下载**（`chrome_extension/background.js`）：内联 PDF 服务器不下发 `Content-Disposition: attachment`，`chrome.downloads.onDeterminingFilename` 被动监听永不触发。改为在 `chrome.tabs.onUpdated` 捕获 `attachment.zhaopin.com/.../downloadFileTemporary?file=...` 后主动调 `chrome.downloads.download({url, filename, conflictAction: "uniquify"})` 强制保存，再 500ms 关闭弹出 tab。
- **列表滚顶**（`chrome_extension/content.js::scrollZhilianCandidateListToTop`）：`zhilianCollectLoop` 进入扫描前找到 `.im-session-item.km-list__item` 向上首个可滚父节点，`scrollTop = 0`，确保从列表真正首位开始采集。
- **冗余去重清理**（`recruitment_assistant/services/zhilian_ws_bridge.py::_save_resume`）：删除下载成功后再跑一次 `candidate_key in self._seen_candidate_records` 的判定（11 行），下载前 dedup 已经拦截过，重复判定还会在第一份归档完成 + 源文件 unlink 后让第二次走 `_save_resume` 报 "未找到可归档文件"。同时去掉 content.js 内 `notifyDownloadResult` 回执后重复 emit `resume_downloaded` 的代码块（BOSS/前程无忧没这层重复 emit）。
- **识别耗时跳变**：上游版本中 `extractZhilianContactInfo` 用 `document.querySelectorAll("aside, section, header, article, div")` 全文档遍历 + 节点 `.innerText` / `getBoundingClientRect`，详情面板首次填充后 1k+ 节点强制 layout flush 把单次 extract 推到 1.5–3.5s；`waitZhilianDetailSwitch` 200ms 轮询累计放大到 33s / 74s。
  - **B（去冗余）**：`waitZhilianDetailSwitch` 改成返回完整 info 对象（超时也返回最近一次 lastInfo），调用方一次 await 拿到 name/age/education/job_title 全字段，删除循环结束后多余的二次 extract 调用。
  - **C 第一次尝试（已回滚）**：把 selector 收窄成 `[class*="candidate-info"]` 等 5 个模糊候选 → 命中错误的 placeholder 容器，所有姓名都变 "待识别"，立即回退。
  - **C 第二次（v2.14.0 固化）**：用户提供真实 outerHTML `<div class="im-three-list__panel--job--title" title="...">...`，改用 O(1) 精确 selector `document.querySelector(".im-three-list__panel--job--title")` 拿 `title` 属性（CSS ellipsis 下仍是完整文本）做沟通职位提取；删除上一轮加的 banner 全文档扫描循环。
- **文件名加沟通职位**（`zhilian_ws_bridge.py::_save_resume`）：在 stem 拼装处插入 `simplified_position = self._simplify_talking_position(...)`，规则：去括号 → 取 `/` 前 → trim → ≤8 字符。非空时 append 进 `stem_parts`，空时跳过避免出现 "--"。新文件名格式：`{姓名}-{年龄}-{学历}-{沟通职位简化}-智联招聘-{YYYYMMDD}-{HHMMSS}-{NNN}{后缀}`。

**UI/日志规范化**

- 实时日志区新增 `boss-log-success` 绿色 class（`#0a7d2e`，AA 对比度）+ font-weight:700；归档成功 `已归档简历: {签名} → {文件名}` 用该 class。
- "黄色"高亮 `.boss-log-highlight` 改琥珀 `#b45309 !important`（替换原 `var(--color-accent)`）。
- 删除蓝色沟通职位日志（同信息已固化进文件名，UI 日志不再赘述），同步删除 `boss-log-blue` CSS + `classify_zhilian_log` 路由分支。
- 删除冗余事件日志：`zhilian_nav` 全部 step、`zhilian_attachment_button_found`、`zhilian_view_attachment_clicked`、`zhilian_attachment_tab_captured` 改为 `pass`（事件统计 / runtime_state 不动）。
- 任务初始化块去掉 `执行日期：...`、`当前会话：扩展已连接 ...`（已在版本信息行）、`扫描间隔：默认`（仅非默认时输出）。
- 重复 echo `内容脚本已启动采集 v2.14.0` 去重：bridge 新增 `_last_content_script_ready_signature: str | None`，相同 `version|key_count|signature_count` 不再重复打。
- 删除 `下载前去重命中，跳过附件识别: {签名}`（前一条 `跳过: {签名} (去重命中...)` 已表达）。

**索要简历彻底清理**

智联沟通中页基本都已聊起来，"识别到没附件就索要简历"分支无业务价值。删除：

- `app/pages/06_智联采集.py` 复选框 + collect_start payload 字段
- `zhilian_ws_bridge.py` payload 读取 + "配置：request_resume_if_missing=..." 日志 + event_log 字段 + `case "zhilian_resume_request_clicked"`
- `chrome_extension/content.js` 智联流程中 `btnState === "request"` 分支的索要简历点击代码块 + `clickZhilianRequestAttachment()` 函数
- BOSS / 前程无忧的同名字段全部保留（未受影响）

**页面布局调整**

- 去重 banner 从底部独立卡片移到"运行状态"区 `status_cols[3]`，复用 `st.metric` 与扩展版本/最近事件同尺寸；Run ID metric 已废弃。
- 删除已无用 `.zhilian-dedup-banner` CSS（5 行）。

**沟通职位识别**

- content.js `extractZhilianContactInfo` 新增 `info.talking_position`（精确 selector + `title` 属性优先）。
- emit `candidate_clicked` 把字段透传到 `candidate_info`，事件链经 `download_intent` → background.js `pendingDownloads` 展开 → bridge `resume_downloaded` data，bridge 端 `_save_resume` 拿 `candidate_info["talking_position"]` 直接用，无需新增独立事件。
- bridge 新增 `_simplify_talking_position(raw) -> str` 静态方法用于文件名和（已删除的）日志输出。

**版本号最终态**

- `chrome_extension/manifest.json` `2.14.0`
- `chrome_extension/content.js` `CONTENT_SCRIPT_VERSION = "2.14.0"`
- `recruitment_assistant/services/zhilian_ws_bridge.py` `ZHILIAN_BRIDGE_VERSION = "1.21.0"`，`ZHILIAN_EXTENSION_EXPECTED_VERSION = "2.14.0"`，`ZHILIAN_CONTENT_SCRIPT_EXPECTED_VERSION = "2.14.0"`

**测试结果**

`#164` 批次目标 10 份：识别耗时全程稳定 ~2s（之前 33–74s 抖动彻底消失）、10/10 下载 + 归档成功、文件名样本：

```
许高健-38岁-本科-资深产品策划-智联招聘-20260523-220551-001.pdf
周江超-37岁-本科-Golang后台-智联招聘-20260523-220607-002.doc
姚朝芳-32岁-本科-用户及市场调研经-智联招聘-20260523-220638-005.pdf
李首亿-22岁-本科-UI设计师-智联招聘-20260523-220710-008.pdf
```

总耗时 123s / avg 12.4s/份，Chrome 下载目录与归档目录对账无遗漏。

### 周边修复（同日批次）

- `app/main.py` + `recruitment_assistant/services/crawl_task_service.py`：新增 `CrawlTaskService.reap_stale_running_tasks(platform_code=None)`，Streamlit 启动时 `@st.cache_resource` 跑一次，把 `status='running'` 但进程已死的孤儿 CrawlTask 收尾为 `cancelled`，避免首页"运行中"误报。
- `recruitment_assistant/services/boss_ws_bridge.py` / `qiancheng_ws_bridge.py`：跟智联同步小幅调整（与本日主线弱相关，详见 diff）。

## 2026-05-22

### V2.50 主题接管深度补全 + WS 桥接自愈 + 采集页 UI 对齐

页面版本保持 `V2.50`（前期已升），本次补齐先前积累但未写入日志的改动 + 本会话新增内容。

**主题系统：原生 Streamlit 组件全面接管**

- `app/styles/components.css`：
  - 禁用按钮（`div.stButton > button:disabled` 等 3 类）从写死 `var(--gray-300)` 改为 `var(--color-surface-muted)` + `var(--color-text-muted)` + 1px 主题边框 + `opacity:.7` + `cursor:not-allowed`，与系统设置预览区的禁用按钮 demo 完全一致。
  - `st.number_input` 完整接管：`[data-testid="stNumberInput"]` 容器走 `color-surface` + 主题描边；输入框文字 `color-text` 居中加粗；+/- 步进按钮默认态 `color-primary-soft` + `color-primary` 字色，hover 反转为主色实底白字；disabled 走与禁用按钮一致的灰白态；兜底 `svg { fill:currentColor }` 防 BaseWeb 内置图标颜色逃出主题。
  - `st.selectbox` 弹层接管：触发器外观 + 下拉箭头随主题；popover portal 出去的 `[role="listbox"]` 用主题面板色 + `shadow-md` 阴影；选项默认 / hover（`color-primary-soft` 底 + 主色字）/ aria-selected（主色实底 + 白字加粗）三态完整。

- `app/components/layout.py`：`APP_VERSION` 从 V2.48 升 V2.50；`page_header()` 增加 `icon: str | None` 参数与 `icon_data_uri()` 缓存函数，支持页面标题左侧渲染 base64 数据 URI 图标。
- `app/styles/global.css`：新增 `.vibe-page-title-lede` 与 `.vibe-page-icon` 布局规则，配套 page_header 图标位。

**采集页面主题预览区扩展**

- `app/pages/05_平台登录.py` 主题预览区在原有"主按钮 / 次按钮 / 输入框 / 下拉框"基础上新增：
  - 第三种按钮态 `禁用按钮`（`disabled` 属性），用 `color-surface-muted` 背景演示主题如何处理 disabled。
  - `每页数量` 旁的 `+/- 数字框` 演示，三态着色与新增 `stNumberInput` 全局接管完全一致。
  - 预览 iframe 高度从 430 提到 490 容纳新元素，caption 同步更新。

**WebSocket 桥接：启动失败缓存自愈**

- `app/components/bridges.py`（新增文件）：`@st.cache_resource` 会把 `BossWSServer.start()` 失败时产生的 broken bridge 实例永久缓存住，导致 8765 / 8766 端口冲突解除后页面仍报 `[Errno 10048]`。新设计将 `cache_resource` 装饰下沉到 `_build_*_bridge` 内部函数，外层 `get_*_bridge()` 检查 `is_listening / startup_error`，发现失败缓存时主动 `clear()` + 重建一次，问题再发时自愈，不影响正常路径性能。
- `recruitment_assistant/services/boss_ws_bridge.py`：`BOSS_BRIDGE_VERSION` `1.93.0 → 1.94.0`。
- `recruitment_assistant/services/qiancheng_ws_bridge.py`：`QIANCHENG_BRIDGE_VERSION` `1.7.0 → 1.8.0`。

**采集页面 UI 细节对齐**

- `app/pages/06_智联采集.py`：`.zhilian-status-idle` 字色从 `var(--color-success-soft)`（被误当字色用的浅绿底色变量，白底上几乎不可读）改为 `var(--color-text-secondary)`，修复"等待启动"文字在白底主题下不可识别问题。
- `app/pages/08_BOSS采集.py`：
  - 实时日志 / 候选人列表标题（`.boss-result-title strong`）字号 14px → 18px、`font-weight:700`、`line-height:1.3`、`min-height:26px`，与下方"BOSS直聘历史批次任务列表"标题视觉一致。
  - `采集模式` selectbox label 加 `padding-left:12px`，与下方下拉控件内边距对齐。
  - `索要简历` checkbox label 字号 14px → 15px，整体 `padding-top:14px / padding-left:8px` 向右下推移，与右侧 metric banner 水平基线对齐。
- `app/pages/09_51前程无忧采集.py`：
  - 顶部 `打开51Job网站 / 重新检测 / 重置日志 / 清除去重` 4 按钮列宽从 `[1.4, 1.6, 1.1, 1.25, 4.65]` 改为 `[1, 1, 1, 1, 4]`，统一加 `use_container_width=True`，按钮本体等宽且间距由 `gap="medium"` 单点管理。
  - `采集模式` selectbox label 与结果标题字号同步对齐 BOSS 页面规则。

**简历解析鲁棒性提升（先前积累）**

- `recruitment_assistant/parsers/pdf_resume_parser.py`：新增 pymupdf 备选提取通道、`_score_pdf_extraction()` 评分函数与 CJK 私用区乱码检测（嵌入子集字体常把数字 / 拉丁映射到 U+7700-U+77FF），`ParsedResume` 新增 `parsing_warnings` 字段对外暴露提取阶段告警。

**页面图标接入（先前积累）**

- `app/main.py / app/pages/07_简历管理.py / app/pages/10_面试管理.py` 调用新版 `page_header(..., icon=...)` 传入图标路径，页面顶部标题左侧渲染 48×48 圆角图标。

---



- 页面版本同步升级为 `V2.30`。
- `BOSS采集` 页面采集与结果栏中 `开始采集`、`暂  停`、`继  续`、`停  止` 按钮统一使用容器宽度。
- `51前程无忧采集` 页面采集与结果栏中 `开始采集`、`暂  停`、`继  续`、`停  止` 按钮统一使用容器宽度。
- 将 `暂停`、`继续`、`停止` 调整为两字中间双空格显示，保持与 `开始采集` 的文字视觉宽度一致。

### V2.29 主题风格首行操作对齐


- 页面版本同步升级为 `V2.29`。
- `主题风格` Tab 中的 `应用主题` 与 `统一保存设置` 按钮移动到主题下拉框同一行。
- 保持主题下拉框、应用按钮、保存按钮与右侧留白在同一横向布局中展示。
- 移除主题预览区下方重复操作行，减少页面纵向占用。

### V2.28 同排按钮等宽等间距优化


- 页面版本同步升级为 `V2.28`。
- 全局统一同一横向排列中的按钮列宽，按钮自动铺满所在列。
- 统一按钮组横向间距，保持同排按钮视觉节奏一致。
- 按钮文字居中显示并保留空白字符宽度，减少不同字数按钮造成的视觉宽度差异。
- 优化 `.vibe-card-button-row` 中卡片按钮组的等宽展示。

### V2.27 主题风格设置区布局优化


- 页面版本同步升级为 `V2.27`。
- `主题风格` Tab 中的主题下拉框改为窄列显示，减少横向占用。
- `应用主题` 与 `统一保存设置` 按钮调整为同一行，提升设置操作区一致性。
- 移除页面底部独立 `统一保存设置` 按钮，避免与主题操作区重复。

### V2.26 系统主题风格管理


- 页面版本同步升级为 `V2.26`。
- `系统设置` 页面新增 `主题风格` Tab，可通过下拉菜单选择预设主题。
- 新增主题预览区，包含 Banner、标题、正文、按钮、输入框、下拉框、标签与进度条等常见网站元素。
- 新增 `app/styles/themes/` 统一主题目录，系统自动读取目录下独立 CSS 文件并展示。
- 新增 10 套独立主题：轻奢商务风格、清新淡雅风、莫兰迪高雅风、黄金沙漠风、简洁素雅风、酷炫科技风、寒冰冷酷风、精致糖果风、大厂后台风、厚重金属风。
- `app/components/layout.py` 新增主题读取、当前主题保存与全站注入机制，应用主题后全站统一生效。

### V2.25 全局 UI 主题样式分层接入


- 页面版本同步升级为 `V2.25`。
- 新增 `app/styles/theme.css`，集中管理主题颜色、字号、间距、圆角、阴影与浅色/深色/极简/科技主题变量。
- 新增 `app/styles/global.css`，统一页面基础布局、排版、滚动条、顶部导航、侧边栏和响应式规则。
- 新增 `app/styles/components.css`，统一按钮、表单、输入框、表格、卡片、弹窗、标签、提示框等组件视觉样式。
- `app/components/layout.py` 改为读取独立 CSS 文件并统一注入，不改变页面 DOM、路由、交互和业务逻辑。
- 保留 `THEME_CSS_HOOK` 作为后续主题 CSS 扩展入口。

### V2.24 标准化清理与公共布局优化

- 页面版本同步升级为 `V2.24`。
- 清理首页空 `st.write`、面试管理未使用函数/变量、智联采集重复样式声明等低风险冗余。
- 重构 `app/components/layout.py`：拆分全局样式、顶部导航、侧边栏渲染函数，集中管理顶部链接。
- 在全局样式中预留 `UI_THEME_EXTENSION_HOOK`，作为后续统一主题 CSS 接入入口。
- 保持业务逻辑、页面路由、按钮交互、数据渲染与接口请求不变。

### V2.23 面试评价弹窗双栏布局调整

- 页面版本同步升级为 `V2.23`。
- `记录面试评价` 弹窗按参考图调整为左侧填写区、右侧面试历史的双栏布局。
- 左侧顶部显示候选人姓名、基础信息和当前环节，填写区调整为面试官、面试时间、面试形式、面试评语、星级评价与面试结论。
- 右侧固定展示面试历史，包含轮次、时间、方式、面试官和结论。

### V2.22 面试管理第三轮以上分类命名调整

- 页面版本同步升级为 `V2.22`。
- `面试管理` 页面将 `第三轮面试` 分类名称调整为 `第三轮以上面试`。

### V2.21 面试评价弹窗结构化改版

- 页面版本同步升级为 `V2.21`。
- `面试管理` 候选人卡片在 `生成面试大纲` 后新增 `查看面试评价` 按钮。
- 新增 `查看面试评价` 弹窗，按历史记录展示轮次、面试官、评分、结论、记录和时间。
- 重构 `记录面试评价` 弹窗：左上角显示候选人基本信息，右上角显示当前进度和面试历史进度节点。
- 面试历史节点按结论着色：绿色=`通过`，黄色=`待定`，红色=`淘汰`。
- 本轮填写区调整为：面试官、自动轮次、面试方式、面试记录、五星评分、面试结论。
- 保存时将面试记录写入 `InterviewEvaluation.strengths`，面试方式写入 `notes`，评分按 1-5 星保存。

### V2.20 面试管理分类与取消后操作修正

- 页面版本同步升级为 `V2.20`。
- 修正 `面试管理` 左侧分类互斥逻辑，简历不再同时出现在 `待面试` 和 `第一轮面试`。
- 分类规则调整为：0 次评价=`待面试`，1 次评价=`第一轮面试`，2 次评价=`第二轮面试`，3 次及以上=`第三轮面试`，取消状态=`已取消`。
- `已取消` 分类中的候选人将 `记录面试评价` 改为 `恢复面试`，点击后恢复为 `pending`。
- `已取消` 分类中的候选人将 `取消面试` 改为 `放弃招聘`，点击后弹出确认框。
- 确认 `放弃招聘` 后删除该候选人的所有面试评价记录，并保留邀约为 `cancelled` 状态。
- `ResumeArchiveService` 新增 `delete_interview_evals(candidate_id)` 用于彻底清理候选人的面试评价记录。

### V2.19 独立面试管理页面

- 左侧导航新增 `面试管理`，页面版本同步升级为 `V2.19`。
- 新增 `app/pages/10_面试管理.py`，采用左 1/3、右 2/3 分栏布局。
- 左侧按面试进度分组：`待面试`、`第一轮面试`、`第二轮面试`、`第三轮面试`、`已取消`。
- 面试进度由候选人面试评价次数自动推算：0 次=`一面`，1 次=`二面`，2 次及以上=`三面`。
- 右侧简历摘要卡片去掉 `邀约面试`、`打开文件`、`打开目录` 小按钮，右上角改为 `面试进度`。
- 每份面试摘要下新增 4 个工具按钮：`打开简历`、`生成面试大纲`、`记录面试评价`、`取消面试`。
- `记录面试评价` 使用弹窗，提交后保存到现有 `InterviewEvaluation` 表。
- `取消面试` 将当前邀约状态更新为 `cancelled`，不删除历史记录。
- `简历管理` 页面移除原 `面试邀约` / `面试评价` Tab，保留简历详情里的 `📧 面试邀约` 按钮。

### V2.18 简历库浏览大改 + 面试邀约 + 智能匹配重构

#### 背景

V2.17 完成 AI 解析层优化后，使用流程暴露三个 UI/数据层短板：
1. Tab 2 简历库浏览只显示一半字段、用 expander 折叠不直观、需翻页
2. 没有从浏览到邀约的闭环 — 看到合适候选人只能记下来再操作
3. Tab 3 AI 匹配只是把 50 行候选人摘要丢给 AI 取 top 10，结果不持久化、信息量不够、刷新即丢

#### 数据库改动

**新增 2 张表 + 1 列**：
- `interview_invitations`：面试邀约（候选人/岗位/状态/备注），candidate_id CASCADE，position_id SET NULL
- `position_matches`：岗位匹配评分（position_id+candidate_id 唯一约束），CASCADE 删除
- `candidates.is_favorite`：⭐ 关注标记
- `job_positions.min_education` / `min_experience`：学历/年限要求枚举

所有 ALTER 通过 `init_resume_database()` 的 `create_all()` + 手动 ALTER 落地，幂等。

#### Service 层新增方法

```
update_candidate_field        - 浏览页 ⭐ 关注开关实时落库
has_pending_invitation        - 邀约去重（同人同时只能 1 条 pending）
create_invitation             - 发起邀约
list_invitations              - 按 status 筛选
update_invitation_status      - completed / cancelled
update_position               - 编辑岗位
clear_position_matches        - 清空岗位匹配（重跑前调用）
save_position_match           - 保存单条匹配（merge 幂等）
list_position_matches         - 按 score 降序读取（min_score 阈值过滤）
```

#### Tab 2 简历库浏览：完全重写

| 改动 | 之前 | 现在 |
|---|---|---|
| 列表布局 | expander 折叠 + 翻页 | 左 1/4 滚动列表 1400px + 右 3/4 详情 |
| 字段展示 | 只 14 个字段 | 全部 30+ 字段平铺，无截断 |
| 标签格式 | 全角空格分隔 | `姓名 ｜ 28岁 ｜ 本科` 左对齐白底黑字 |
| 关注高亮 | 无 | 关注候选人 label 加 ⭐ 前缀 |
| 候选人操作 | 删除 | ⭐ 关注 + 📧 邀约 + 🗑️ 删除 |
| 文件操作 | 显示路径 | 📄 打开 + 📁 访问目录 |
| 过滤条 | 姓名/城市/学历/来源 + 翻页 | + 标记下拉（全部/关注） |

新增 `_render_candidate_detail()` 函数渲染右侧详情，按 9 段平铺（基本信息 / 教育 / 工作 / 项目 / 技能 / 求职意向 / 荣誉 / 自我评价 / 简历来源）。

#### Tab 3 招聘岗位/匹配：完全重写

**两栏布局**：左 1/3 岗位 expander 列表，右 2/3 匹配结果 Banner。

**录入岗位表单升级**：
- 岗位要求 `text_area` 高度 100→300（3 倍）
- 薪资改两个下拉框（下限 / 上限）
- 学历下拉（不限 / 大专 / 本科 / 硕士以上）
- 工作年限下拉（不限 / 1-3年 / 3-5年 / 5-10年 / 10年以上）
- 删除工作城市输入框

**抽出 `_render_position_form()` 复用**：录入和编辑共用同一个表单。新增 `@st.dialog` `_open_edit_position_dialog()` 用于编辑。

**左栏岗位 expander**：
- 标题：`岗位名（薪资）`，选中加 `▶` 前缀触发手风琴效果
- 内容：部门/学历/年限 caption + 岗位要求 markdown（CSS 缩字号 13px 但保留 markdown 排版）
- 按钮：🎯 智能匹配 / 🧹 清除匹配 / ✏️ 编辑岗位 / 🗑️ 删除
- 未选中时顶部加「查看匹配结果 >>>」淡绿色按钮（`#dcfce7`）

**右栏匹配 Banner**：
- 头部：候选人姓名（22px 黑体）+ 主信息（| 分隔）+ 匹配度（32px，HSL 渐变 50%红棕→95%亮绿）+ 📧 邀约
- AI 评语：深蓝 `#1e3a8a`
- 简要履历：教育 + 最近 3 段工作（`「2022 至 至今」`，避免 markdown `~` 删除线）
- 联系方式：黑色，不灰化
- 底部：📎 文件路径 + [📄] [📁] 32x32 图标按钮 + 来源/入库时间右下角

**AI 匹配分批进度**：
```python
chunk_size = max(3, min(20, total // 5))
```
自适应批次：5 人 → 3/批，74 人 → 14/批，500 人 → 20/批。每批后实时显示 `0/74 → 14/74 → 28/74` + 进度条。

#### Tab 4 面试邀约：新增

新增 5 个 Tab 中第 4 个（Tab 重排：入库 / 浏览 / 岗位 / 邀约 / 评价）。

- 顶部 metric：进行中 / 已完成 / 已解除
- 列表卡片：姓名（关注加 ⭐）+ 拟招岗位 + ✓完成 / ✗解除 + 主信息行 + 全部联系方式 + 创建时间 + 备注
- 邀约入口：浏览页详情头部 📧 按钮 → `@st.dialog` 选岗位 → 去重检查 → 写库

#### AI 服务改动 (`resume_ai_service.py`)

`match_candidates`：
- 移除 `top_n` 限制，对 **所有** 候选人评分（持久化由调用方负责）
- candidate dict 字段从 5 个扩成 7 个（加 `skills` / `work_summary` 两段摘要）
- 增加 `_unwrap_candidate_envelope` 兜底：AI 偶尔返回 `{"candidates": [...]}` 包装时自动解包
- prompt 强调"完整 JSON 数组，不要遗漏任何候选人"

#### CSS Scope 技术沉淀

为给特定 widget 染色又不影响其他按钮，固化了一个模式：
```python
container = st.container(key=f"unique_X")
st.markdown(f"<style>div.st-key-unique_X button {{ ... }}</style>", ...)
with container:
    st.button(...)
```

`.st-key-XXX` 是 Streamlit 1.36+ 给 `st.container(key=...)` 自动生成的稳定 class，是目前唯一能 scope CSS 到特定 widget 的可靠方式。已用于：
- 「查看匹配结果 >>>」按钮淡绿染色
- 文件路径行 [📄] [📁] 32x32 图标按钮强制等大居中
- 文件路径行 container 上 margin -8px 消除空行

#### 踩坑记录

1. **Markdown `~` 渲染删除线**：工作经历 `start~end` 直接被渲染成删除线。改全角「 至 」。
2. **CSS `:contains` `:has` 跨浏览器支持差**：用 `.st-key-XXX` 容器 class 替代。
3. **CSS 优先级被 Streamlit 主题覆盖**：默认 `.st-key-X button` 不够，需要 `div.st-key-X button` + 同时覆盖 `:focus / :active / button p` 状态。
4. **手风琴效果的副作用**：依赖 expander label 变化（`▶ ` 前缀）触发 widget key 变化让 streamlit 重置 expanded 状态，意外地解决了"展开多个岗位"的问题。
5. **`_render_candidate_detail` 函数 forward 引用 dialog**：`_open_invite_dialog` 必须在 detail 渲染函数之前定义，否则 NameError。
6. **`st.container(key=...)` 默认有 padding**：导致紧贴上方内容时出现空行。`margin-top:-8px` 拉回去。
7. **AI 单次评估 70+ 人 prompt 超长**：分批 + chunk_size 自适应，5-20 之间。

#### 文件变更

| 文件 | 改动 |
|---|---|
| `recruitment_assistant/storage/resume_models.py` | +50 行（InterviewInvitation + PositionMatch + 3 列） |
| `recruitment_assistant/services/resume_archive_service.py` | +95 行（9 个新方法） |
| `recruitment_assistant/services/resume_ai_service.py` | match_candidates 重写 |
| `app/pages/07_简历管理.py` | +1038 行（Tab 2/3/4 全部重写 + dialog + 表单复用） |

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
- `age` 显式要求"只有生日时用当前年减去出生年"（动态注入 `datetime.now().year`）。
- `skills` 加合并规则："相同 skill_type 的多技能用顿号合并到一条"。
- 区分 `education_level`（学历层次）和 `degree`（具体学位）。
- `raw_text[:8000]` → `raw_text[:MAX_RESUME_TEXT_CHARS]`（25000）。

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
- `merge-skills`: 同候选人+同 type 合并 skill_name（659 → 165 条）
- `ai-fill`: phone 作 key 用现有附件二次过 AI 补全缺失字段（待用户手动执行）

**入库规范化（`07_简历管理.py`）：**
- 引入 `normalize_platform()` 函数 + `PLATFORM_ALIAS` 别名表，写库时统一为三个枚举值之一。

#### 测试

- `tests/services/test_resume_ai_service_prompt.py` 5 个测试，回归 prompt 字段覆盖。
- `tests/services/test_platform_normalize.py` 5 个测试，验证别名映射。
- `tests/services/test_unwrap_envelope.py` 7 个测试，验证 AI 嵌套输出兜底解包。
- `scripts/test_parse_one.py` 抽样工具 + `scripts/audit_resume_db.py` 字段填充率审计。

#### 实测收益（候选人 75 → 74，删张三 sample 后）

| 字段 | 改造前 | 改造后 | 变化 |
|---|---|---|---|
| candidates.gender | 52.0% | **90.7%** | ↑ +38.7 |
| candidates.age | 52.0% | **72.0%** | ↑ +20.0 |
| candidates.current_city | 41.3% | **53.3%** | ↑ +12.0 |
| 无年龄候选人 | 36/75 | **21/75** | ↓ 减 41% |
| skills 人均条数 | 8.79 | **2.20** | ↓ 减 75% |
| skills 总条数 | 659 | 165 | 合并去重 |
| 0% 填充率字段数 | 19 | **0** | 全删 |
| candidates 字段数 | 18 | 13 | 精简 |
| 平台命名 | BOSS/BOSS直聘 混用 | 统一 BOSS直聘 | |

仍偏低但属现实约束（**简历本身不写**，prompt 已说"留 null 不要硬填"）：
- candidates.wechat 11%（平台简历少给微信）
- candidates.birth_date 24%（简历多写年龄不写出生日）
- education.degree 10%（中文简历少写"工学学士"等学位名）
- work.industry 16%（简历少明确写行业）
- job_intention.job_status 13%（多数简历不写求职状态）

#### 踩坑记录

1. **AI 嵌套包装层** — DeepSeek 偶尔把候选人字段嵌进 `{"candidates": {...}}` 子对象。根因是初版 prompt 用了 `## candidates 主信息` 标题。修复：
   - prompt 顶部加"输出结构"段，给出 16 个顶层字段名 + `{"candidates":{...}}` 反例。
   - service 层加 `_unwrap_candidate_envelope()` 兜底，遇到 `candidates / candidate / data / result` 包装层自动解开。
2. **scripts 缺 sys.path 注入** — `scripts/migrate_resume_db.py` 直接 `python` 调用时 import `recruitment_assistant.*` 报 ModuleNotFoundError。修复：与 `scripts/init_db.py` 同模式，顶部 `sys.path.insert(0, parents[1])`。
3. **`pyproject.toml` 漏写 `openai`** — streamlit 那边能用是因为某次手动 `pip install` 装到了别的环境；新建 venv 跑 `--phase ai-fill` 报 `No module named openai`。修复：补进 `dependencies`。

### V2.17.1 后续修补

- **prompt 漏字段补齐**：把 `project_date` / `project_duty` / `honor_date` 加进 prompt（旧版漏掉，导致这 3 字段填充率 0%）；prompt 测试 `REQUIRED_FIELDS` 同步增加防回归。
- **删 V2.17 前手工 sample**：`DELETE FROM candidates WHERE candidate_id=1`（张三/13800138000，实测无任何业务数据）。
- **honors 段在 prompt 中位置变化**：现在排在 project 之后、skills 之前。json_object 模式下字段顺序对 AI 输出影响很小，未观察到回归。

#### 后续观察点

- 下一批入库后再跑 `audit_resume_db.py`，确认 P0 补的 3 个字段（project_date/project_duty/honor_date）实际填充率。
- candidates.current_city 53% 仍不达 75%，部分简历只在工作经历里隐含现居城市。下一版 prompt 可加"若无明示则从最近工作经历推断"，但风险是猜错（异地工作）—— 暂不动。


## 2026-05-18

### V2.08–V2.16 BOSS 采集稳定性修复 + 候选人识别重构

#### 问题总览

经过多轮测试（#41–#50），暴露并修复了 scroll-rescan 循环中的多个静默崩溃 bug、候选人姓名误识别问题、以及下载策略短路问题。

#### 已修复 Bug 列表

**1. `findResumeAttachmentButton()` 未定义（V2.12）**
- 根因：scroll-rescan 循环引用了不存在的函数，导致 ReferenceError 静默崩溃
- 修复：`findResumeAttachmentButton()` → `findResumeButton()`（与主循环一致）

**2. scroll-rescan 重复候选人跳过无日志（V2.14）**
- 根因：`seenSignatures.has(signature)` 命中时只做 `results.skipped++` 但未调用 `skipCandidate()`
- 修复：改为调用 `await skipCandidate(candidateId, signature, "duplicate_in_run", { fast_skip: true })`

**3. 英文/非常规名称导致三字段全部"待识别"（V2.14）**
- 根因：`getTopProfileTokens` 过滤器只允许中文字符；`parseTopProfileName` 无英文名匹配
- 修复：token 过滤器增加 `[A-Za-z]{2,}`；名称解析增加英文名正则和单字中文名兜底

**4. 职位名称"数据标注""游戏"被误识别为候选人姓名（V2.14）**
- 根因：BOSS"沟通职位"区域的岗位名称（2-4 中文字符）通过了 token 过滤和名称解析
- 修复（双保险）：
  - 新增 `isJobTitleContext(el)` 检查元素父级/兄弟是否含"沟通职位|期望职位|求职意向"标签
  - 扩充 `isInvalidCandidateNameToken` 黑名单：15 个 2 字职业词 + 40+ 多字职位模式

**5. 候选人识别策略重构：基于字体大小的主检测（V2.15）**
- 新增 `findProfileByFontSize()` 作为主策略优先执行，利用 BOSS 页面视觉层次：
  - TreeWalker 高效定位 "XX岁" 文本锚点
  - 向上遍历找到含 `|` 分隔符的信息栏容器
  - `getComputedStyle().fontSize` 找同行最大字号元素 = 姓名
  - 性别符号（♂/♀）软验证
  - 管道符分割提取学历
- 原有 token 扫描保留为 fallback
- 纯只读 DOM 检测，不触发面板刷新

**6. `clickLearnedDownload` 失败后短路阻断后续策略（V2.16）**
- 根因：学习记录（SVG）在 dom_text 预览中找到匹配元素但点击无效，随后直接进入手动学习模式，后续策略永不执行
- 修复：下载未触发时返回 `false`，让 `clickBossSvgDownloadIcon`、`waitForDownloadButton` 继续尝试；手动学习仅作最后防线

#### 版本同步

- `manifest.json` 1.73.0 → **1.82.0**
- `content.js` `CONTENT_SCRIPT_VERSION` 1.73.0 → **1.82.0**
- `boss_ws_bridge.py` 1.80.0 → **1.89.0**；期望扩展/脚本 → **1.82.0**
- `qiancheng_ws_bridge.py` 1.3.0（不变）；期望扩展/脚本 → **1.82.0**
- `app/components/layout.py` V2.07 → **V2.16**

#### 测试结果

- 测试 #50（V2.15）：5/5 100% 达成率，avg 15.8s/份
- 候选人姓名识别：字体大小策略成功识别所有中文名（朱月明、王芳、陈伟妹、黄进、程小波、张书豪）
- 下载策略：PDF iframe 直接下载 3 份 + 手动学习 2 份（dom_text 类型）
- V2.16 修复后 dom_text 类型应可通过后续策略自动下载，减少手动介入

### V2.07 UI 文案精简 + 修复扩展版本号显示 bug

#### 已完成内容

**09 页 UI 调整：**

- 按钮文案精简：
  - `打开 51前程无忧（ehire）登录页` → **`打开51Job网站`**
  - `重新检测 ehire 页面` → **`重新检测51Job页面`**
- 移除"清除学习记录"按钮及其交互链路（学习模式作为冷启动一次性流程，正常采集流不需要清除入口；如需重学，用户可在 F12 Console 手动删除 `qiancheng_*` localStorage key）
- action_cols 6 列 → 5 列，宽度比例 `[1.4, 1.6, 1.1, 1.25, 4.65]`（迭代两次：第一次按短文案缩到 1.0 后偏短，调整为 1.4/1.6）

**移除清除学习记录链路（3 处代码删除）：**

- `qiancheng_ws_bridge.py`：删除 `clear_qiancheng_learning()` 方法
- `qiancheng_ws_bridge.py`：删除 `case "qiancheng_learning_cleared"` 处理
- `content.js`：删除 `case "clear_qiancheng_learning"` 命令处理
- `clearQianchengLearningKeys()` 函数保留（作为 F12 Console 调用工具）

**修复扩展版本号显示 bug：**

- 根因：`background.js:24` 硬编码 `EXTENSION_VERSION = "1.68.0"`，多次 manifest bump 时从未跟着升级
- 修复：改为运行时动态读取 `chrome.runtime.getManifest().version`
- 影响：所有历史上"扩展版本不匹配 warning"的根源问题；今后 manifest 升版即自动同步上报版本，不再需要双向手动同步

**版本同步（含 background.js 已动态化，不再列在同步项）：**

- `manifest.json` 1.72.0 → **1.73.0**
- `content.js` `CONTENT_SCRIPT_VERSION` 1.72.0 → **1.73.0**
- `background.js` `EXTENSION_VERSION` 硬编码 → **动态读 manifest**（永远跟随 manifest 同步）
- `qiancheng_ws_bridge.py` 1.1.0 → **1.2.0**；期望扩展/脚本 1.72.0 → **1.73.0**
- `boss_ws_bridge.py` 1.79.0 → **1.80.0**；期望扩展/脚本 1.72.0 → **1.73.0**
- `app/components/layout.py` V2.06 → **V2.07**

#### 后续观察点

- 重启 streamlit + 完整重载扩展后，09 页第一栏 banner 应显示扩展版本 **1.73.0**（不再卡在 1.68.0）；任何后续版本 bump 都会被 background.js 自动同步上报

### V2.06 51前程无忧采集器开荒收尾：去除索要简历控件 + 归档目录 51job + 死代码清理

#### 已完成内容

**清理死代码：**

- 删除 `chrome_extension/content.js` 行 2131-2231 旧 4 步学习模块（`runQianchengLearningSession` 4 步版 + `waitForUserClick` + 2 参版 `deriveStableSelector` + `detectPreviewFormKind`）—— Phase 4 已被 7 步版完全覆盖
- 删除 `qiancheng_ws_bridge.py` 行 988-1004 旧学习事件 case（`qiancheng_learning_step_started` / `qiancheng_learning_failed` 等 5 个 case）——content.js 已不再 emit
- 同时修复 `needsQianchengLearning()` ：从只检查 `learned_candidate_card` 一个 key 改为调用 `isQianchengLearningComplete()` 检查全部 8 个 key

**索要简历控件：**

- 09 页删除"索要简历" checkbox（51job ehire 不需要此交互）
- top_cols 列布局 5 列 → 4 列
- start_collect 不再下发 `request_resume_if_missing`

**归档目录：**

- 简历归档目录 `data/attachments/qiancheng/` → `data/attachments/51job/`（3 处：09 页、bridge 行 278、bridge 行 1189）
- 数据库 `platform_code` 仍保持 `qiancheng`，与历史数据兼容
- Chrome 下载临时目录 `Downloads/51前程无忧/` 保持不变（与 background.js download_dir_name 对齐）

**测试结果：**

- 10/10 100% 达成率，avg 10.1s/份（与首次 3/3 的 11.2s/份基本持平，**无规模膨胀**）
- 去重命中验证通过（黄心钰被正确跳过）
- 候选人信息提取健壮性验证（含英文名/特殊名/中文名混排 26 人全识别）
- Chrome 下载目录对账无遗漏

**版本同步：**

- `manifest.json` 1.71.0 → **1.72.0**
- `content.js` 1.71.0 → **1.72.0**
- `qiancheng_ws_bridge.py` 1.0.0 → **1.1.0**；期望扩展/脚本 1.71.0 → **1.72.0**
- `boss_ws_bridge.py` 1.78.0 → **1.79.0**；期望扩展/脚本 1.71.0 → **1.72.0**
- `app/components/layout.py` V2.05 → **V2.06**

### V2.05 51前程无忧采集器端到端跑通

#### 已完成内容

**Phase 5：固化 selector + 自动采集主循环（Chrome 扩展模式）**

- 在 `content.js` 加入 `QIANCHENG_SELECTORS` 常量（13 项 selector）和 6 个 qiancheng 专用 helper：
  - `extractQianchengContactInfo()`：从 `.info-main` 提取姓名/年龄/学历，文本"女 | 27岁 | 3年 | 本科 | 柳州"用 `|` 切分 + 关键字白名单识别
  - `getQianchengCandidateItems()`：从 `#conversation-list .content-list` 抓取候选人卡片
  - `findQianchengAttachmentButton()`：在 `.chat-user-operate` 范围内按文本"附件简历"匹配
  - `waitForQianchengPreviewReady()`：轮询 `.annex-resume .container-options-item.item-download` visible（51 ehire 是就地切换内容，不发新 DOM 节点）
  - `findQianchengDownloadButton()`：优先 `#sensor_Bchatinfo_xiazai`（埋点 id），回退 class
  - `findQianchengClosePreviewButton()`：`.annex-resume .container-close`
- 新增 `qianchengCollectLoop()` 完整主循环（约 200 行）：导航 → 列表抓取 → 候选人遍历 → 去重检查 → 附件检测 → 下载触发 → 关闭弹窗 → 间隔
- `collectLoop()` 入口加平台分发：qiancheng 走 qiancheng 主循环，BOSS 维持原路径
- `ensureQianchengOnChattingPage()`：自动用学到的 `#sensor_talentcommunicate` + `#sensor_Bchat_communication` 跳转到沟通中页面
- 去重 key 命名空间 `qiancheng|profile|name|age|education`，避免与 BOSS 撞库
- 复用 BOSS 已实战的下载链路：`waitForDownloadResult` / `finalizeDownloadWithPersistAck` / `clickElementReliably` / `normalizeBossCandidateSignature`

**关键 selector**（3 个 sensor 埋点 id 极稳）：
- 左侧"人才沟通"菜单：`#sensor_talentcommunicate`
- 顶部"沟通中"标签：`#sensor_Bchat_communication`
- 预览页"下载"按钮：`#sensor_Bchatinfo_xiazai`

**测试结果：**

- 首次跑通 3/3，avg 11.2s/份
- 候选人信息提取 100% 正确（黄心钰/27岁/本科、王剑/30岁/本科、聂雨欣/24岁/本科）
- Chrome 下载链路全程跑通：下载创建 → 持久化确认 → 归档目录正确命名

**版本同步：**

- `content.js` 1.70.0 → **1.71.0**
- `manifest.json` 1.70.0 → **1.71.0**
- `qiancheng_ws_bridge.py` 0.3.0 → **1.0.0**（首版正式可用）；期望 1.70.0 → **1.71.0**
- `boss_ws_bridge.py` 1.77.0 → **1.78.0**；期望 1.71.0
- `layout.py` V2.04 → **V2.05**

### V2.04 扩展端 DOM 学习模式（7 步引导）

#### 已完成内容

**Phase 4：扩展内嵌学习模式**

针对 51 ehire 全新平台，content.js 无法预知 DOM 结构——通过浮动 banner 引导用户首次采集时手动点击 7 个 DOM 锚点，抓取 selector + DOM 链 + 客户端坐标存入 localStorage，供 Phase 5 自动采集复用。

**7 步学习流程：**

| Step | 学习内容 | localStorage key |
|---|---|---|
| 1 | 左侧"人才沟通"菜单 | `qiancheng_nav_menu_chat_selector` |
| 2 | 顶部"沟通中"标签 | `qiancheng_tab_chatting_selector` |
| 3 | 候选人卡片 | `qiancheng_candidate_card_selector` |
| 4 | 个人信息区（点姓名） | `qiancheng_profile_info_container_selector` |
| 5 | "附件简历"按钮 | `qiancheng_attachment_btn_selector` |
| 6 | 预览形态（自动检测 iframe/dialog/window.open） | `qiancheng_preview_form_kind` |
| 7 | 预览页"下载"按钮（复用 BOSS `learned_click` key） | `qiancheng_resume_download_learned_click` |
| 7+ | 关闭弹窗按钮 | `qiancheng_close_preview_selector` |

**关键设计：**

- 浮动 banner 固定右上角，蓝边框 + 进度条 + ✕关闭按钮
- 每步监听 click（useCapture=true），抓 element + 6 级祖先链 + 客户端坐标
- selector 推导优先级：`data-*` 属性 → 稳定 class（避开 webpack hash）→ role + tag → tag
- Phase 5 在抓取的 chain_detail 里找 `sensor_*` 埋点 id，比推导出的 selector 更稳

**bridge 端事件处理：**

- 7 个 case 处理学习生命周期：`qiancheng_learning_required` / `_started` / `_step_completed` / `_step_failed` / `_finished` / `_cleared`
- 09 页加"清除学习记录"按钮：bridge 发命令到扩展清空 8 个 localStorage key

**版本同步：**

- `manifest.json` 1.68.0 → **1.69.0** → 1.70.0（途中调整）
- `content.js` 1.68.0 → **1.69.0** → 1.70.0
- `qiancheng_ws_bridge.py` 0.1.0 → **0.2.0** → 0.3.0
- `boss_ws_bridge.py` 1.75.0 → **1.76.0** → 1.77.0（保持双 bridge 期望版本一致）
- `layout.py` V2.00 → V2.04

#### V2.04 期间识别并写入项目记忆的规则

- "改代码必 bump 模块版本号" 记忆：任何 content.js / manifest / bridge / layout 改动都要同步升对应版本号常量，否则 Chrome 不重载扩展、bridge 输出版本不匹配警告

## 2026-05-17

### V2.03（已被 V2.04~V2.06 替代）新增 51 前程无忧采集页 + 智联页状态命名空间隔离

#### 已完成内容

**新增 qiancheng 平台骨架适配器：**

- 新文件 `recruitment_assistant/platforms/qiancheng/__init__.py`（空）+ `adapter.py`
- 实现接口：`login()`、`login_manually()`、`is_logged_in()`、`_open_authenticated_session()`、`_is_login_or_security_page()` 等——结构镜像 ZhilianAdapter，URL 替换为 `https://ehire.51job.com/`
- 登录失效标记词改为 51 站点特征：`login.51job.com`、`passport.51job.com`、`/login`、`扫码登录`、`账号登录`、`请输入用户名`、`请输入密码`、`短信验证`、`图形验证`、`verify`
- 已登录标记：`ehire.51job.com`、`/ehire/`、`ehire/home`、`51job`、`前程无忧`
- `fetch_resume_list` / `fetch_resume_detail` 为 base 契约桩实现（返回空列表/字典）
- `auto_click_chat_attachment_resumes` 为骨架版本：步骤 1（打开沟通页）真实执行，步骤 2-6（找候选人列表 / 点击候选人 / 找附件简历按钮 / 识别预览弹窗 / 点击保存按钮）通过 `on_diagnostic` 发出 `status="todo"` 事件占位，最终返回空列表

**新增页面 `app/pages/09_51前程无忧采集.py`：**

- 基于 `06_智联采集.py` 全文复制
- 批量替换：`zhilian` → `qiancheng`、`Zhilian` → `Qiancheng`、`智联招聘` → `51前程无忧`、`智联采集` → `51前程无忧采集`、`rd5.zhaopin.com/` → `ehire.51job.com/`、`passport.zhaopin.com/` → `ehire.51job.com/`、`打开智联登录页` → `打开 51前程无忧登录页`
- `page_title` / `page_header` / 侧边栏 active 标识同步改为 51前程无忧采集
- 顶部加 `st.warning` 横幅说明骨架状态，避免用户以为是 bug

**菜单与登录页注册：**

- `app/components/layout.py` `MENU_ITEMS` 增加 `("51前程无忧采集", "◈", "/51前程无忧采集")`
- 顶部导航栏 `vibe-actions` 同步加 1 项
- `app/pages/05_平台登录.py` 增加 `QianchengAdapter` 导入与 `platform_options["51前程无忧"]` 配置项

**智联页 session_state 命名空间隔离：**

- 全部 9 个顶层 session_state 键加 `_zhilian` 后缀：`collect_runtime_state_zhilian`、`collect_task_logs_zhilian`、`collect_candidates_zhilian`、`collect_paused_zhilian`、`collect_stopped_zhilian`、`collect_running_zhilian`、`pending_collect_task_zhilian`、`collect_action_feedback_zhilian`、`auto_start_collect_task_zhilian`
- 不动 runtime dict 内部字段（`logs`、`candidates`、`scanned_count` 等）
- 51 前程无忧页同样字段为 `_qiancheng` 后缀，两个采集页 session_state 不会互相干扰

#### 后续需要的实际页面信息（等待用户提供）

要从骨架升级为可工作版本，需要 51 ehire 招聘者端**以下 5 个 DOM 锚点的截图/HTML 片段**：

1. 沟通/聊天页候选人列表的卡片元素 DOM 结构
2. 点击候选人卡片后右侧详情区切换的标志（URL 变化 / 关键元素出现）
3. "附件简历"按钮的 selector 与状态判断（启用 / 暂无附件 / 已索要）
4. 简历预览弹窗结构（iframe / 新窗口 / DOM 浮层）
5. 预览页"保存/下载"按钮的 selector

#### 风险点

- 51 ehire 反爬策略可能与智联不同，`login_manually` 等待跳转的 while 循环未必能正确识别"登录完成"——已留 `重新校验登录态` 按钮作为人工兜底
- 若两个采集页 session_state 命名空间隔离漏改某个键，可能导致按钮状态错乱——批量 replace_all 覆盖完整，语法已过；首次启动验证时重点关注智联页面的按钮交互是否正常

### V2.02 智联招聘采集页重构

#### 已完成内容

**页面布局重写为两栏（仿 BOSS 直聘采集页风格）：**

- 头部标题改为"智联招聘采集"，副标题"创建、执行并追踪智联招聘平台的简历采集任务"
- 第一栏"初始化状态"：
  - 三栏布局：登录态 banner / 运行状态 banner / 平台信息 banner（合并平台名/页面版本/最近事件）
  - 登录态"已保存"绿色（#16A34A），"未保存"深红（#B91C1C）；运行状态"等待启动"淡绿（#6EE7B7），"采集中/登录中"深绿（#168A45）
  - 操作按钮三列：登录并保存登录态（有登录态时禁用） / 打开智联登录页（外链）/ 重新校验登录态
  - 移除"目标网站选择"控件（智联页默认且唯一）
- 第二栏"采集与结果"：
  - 参数行 5 列：采集模式（下拉）/ 目标数量或搜索时间（数字输入）/ 采集速度（下拉）/ 索要简历（下拉）/ 每候选人最大等待秒数（数字输入）
  - 按钮行 6 列：开始采集任务（无登录态禁用）/ 暂停 / 停止 / 打开简历目录 / 清空任务记录 / 清空去重记录
  - 双栏结果区参考 BOSS 页：左 1.15 实时日志、右 1 候选人列表，title 字号 18px 黑体，副标题为蓝色统计文案
- 实时日志副标题：`已扫描N位候选人，已扫描M分SS秒，每人平均耗时X.Xs`
- 候选人列表副标题：`已记录N位候选人，跳过M位候选人（其中去重K人），向J位候选人索要了简历，成功下载D份简历`

**新增"登录态独立按钮"流程：**

- 新增 `run_login_only_task` / `start_login_only_task`：单独打开浏览器走 `adapter.login_manually(wait_seconds=900)`，登录态保存后即结束
- runtime 增加 `login_busy` 标志位，区别于 `running`，UI 期间会显示"登录中"
- 登录态判定保留旧的 `check_login_state(verify=False)` 文件存在判定，新增"重新校验登录态"按钮按需走 headless 验证

**"索要简历"开关贯通三层：**

- UI 默认值 `False`（与 BOSS 页一致）
- `auto_click_chat_attachment_resumes` 新增形参 `request_resume_if_missing: bool = False`；遇到"索要附件简历"按钮可点击但开关关闭时，将 `request_button_state` 强制改写为 `disabled` 复用既有跳过链路
- `collect_kwargs` 透传该参数，并加 `inspect.signature` 兼容判断防止旧版本 adapter 出现 TypeError

**新增 runtime 计数：**

- `run_started_at`：start_collect_task 写入，供 summary 计算耗时
- `resume_request_count`：on_diagnostic 看到 `attachment.request_wait status=ready` 时增计
- `dedup_skipped_count`：on_resume_skipped 命中 `before_download_profile` / `before_click_signature` / `duplicate_content_hash` 时增计

**版本同步：**

- 仅 UI / Python 后端调整，未涉及扩展端代码与契约版本

### V2.01 任务初始化/结束语义完善 + HTML弹窗selector固化

#### 已完成内容

**Python 端（boss_ws_bridge.py）：**

- 新增 `_log_task_initialization(config, collect_mode, collect_minutes)`：采集开始时输出 9 行任务初始化信息块（执行日期 / 运行 ID / 任务目标 / 配置 / 去重基线 / 版本四件套 / 当前会话），并写 `boss_task_initialization_logged` 事件
- `collect_finished` 处理器区分三种结束语义：`success`（达成目标）/ `partial`（候选人列表已逛完但未达目标）/ `cancelled`（用户手动停止），不再把 5/10 这种欠完成情况误标 success
- 结束日志改为"采集结束：{原因}（n/m，达成率 X%）"格式，没有目标数时回退到旧格式
- 指标块增强：增加"结束原因"行；下载行加 `下载=N/M（达成率 X%）`；跳过分布按业务语义合并（去重命中 / 待候选人上传 / 索要未确认 / 无附件且未索要 / 未识别 / 其他），不再直接显示扩展端的英文 reason；`download_failed` 类从"跳过"挪到独立"失败"行
- 仅 `partial` 状态时打印针对性 warning 提示（去重命中过半 → 建议刷新列表；待候选人上传过半 → 建议等几小时再来）
- Chrome 下载目录对账遗漏文件由 `highlight` 升级为 `warning` 级别，以 `├─/└─` 树形列出，超过 10 个折叠

**Chrome 扩展端（content.js）：**

- 把 HTML 弹窗形态的下载控件特征固化进自动查找链：`findBossSvgDownloadIcon` selector 集合追加 `span.card-btn, [class*='card-btn']`，并增加硬性过滤要求 descriptor 命中 `card-btn` + 文案命中"附件简历/下载" + 位于弹窗容器内（`[class*='preview/dialog/modal/drawer/popup/layer']` 或 `[role='dialog']`），命中给 +30 评分
- `findDownloadButton` 评分块同步加 `card-btn` + "附件简历/下载" 文案命中 +24 评分作为兜底
- 这条规则来自 2026-05-17 宗绪杰候选人 HTML 弹窗形态首次自动失败后用户手动点学到的特征（`SPAN, descriptor: 点击预览附件简历 card-btn card-btn`），固化进代码后不再依赖 localStorage 跨机器/跨用户共享
- 弹窗容器约束防止聊天列表中同名 class 误中

**实战验证（任务 #93，目标 15 份）：**

- 达成率 15/15 = 100%，平均 3.4s/份（V2.00 长跑批 ~9.2s/份的 2.7 倍）
- 全部下载策略 `pdf_iframe_direct`，零次学习触发，零次人工介入
- 跳过分布：待候选人上传 9 + 无附件且未索要 9（业务正常）
- Chrome 下载目录对账无遗漏

**版本同步：**

- `recruitment_assistant/services/boss_ws_bridge.py` BOSS_BRIDGE_VERSION → 1.74.0；期望扩展 → 1.67.0；期望内容脚本 → 1.68.0
- `chrome_extension/manifest.json` 扩展版本 → 1.67.0
- `chrome_extension/content.js` CONTENT_SCRIPT_VERSION → 1.68.0
- `app/components/layout.py` APP_VERSION 保持 V2.00（页面行为未变）

#### 未实战验证 / 后续观察点

- 新加的 `card-btn` HTML 弹窗 selector：本轮 15 份全部命中 PDF-iframe 形态，没机会验证。下次遇到 `dom_text` 来源的候选人时确认是否自动通过，不再触发学习
- 候选人列表懒加载：`content.js` 行 2082 `getCandidateItems()` 仍只取一次快照，列表只有 N 个 DOM 节点扫完即退出。本轮没复发是因为去重基线低，可下载候选人足够覆盖目标。当去重数据库继续增长、单屏可下载候选人少于目标时会再次跑成 `partial`，届时需要在 collectLoop 里加滚动加载
- "新窗口"形态从未在生产日志出现，manifest 当前不匹配新标签页，遇到再评估

## 2026-05-17

### V2.00 BOSS 采集流程定型 + HTML弹窗下载修复

#### 已完成内容

**流程定型（Python 端）：**

- 日志精简：`manual_download_learning_success` 12行→2行、`learned_download_click_used` 7行→1行、5个纯诊断事件降级为 logger.debug 不再进 UI 实时日志
- `resume_button_found` 仅"暗淡"状态进 UI 日志，"明亮"降级 debug
- `resume_attachment_click_dispatched` 简化为单行摘要
- 新增 `_on_task_finished(status)` 方法：任务结束时发送关闭弹窗指令、Chrome 下载目录对账、输出本轮采集指标汇总
- 新增"打开简历目录"按钮（`08_BOSS采集.py`），复用智联采集页的 `os.startfile` 模式

**HTML弹窗下载修复（content.js）：**

- 问题：BOSS 附件简历的 HTML 弹窗（dom_text 形态）中，下载按钮 `use[xlink:href="#icon-attacthment-download"]` 无法被 `findBossSvgDownloadIcon` 识别，原因是 SVG 元素缺少 `boss-svg svg-icon` 类名导致 `isBossSvgDownloadDescriptor` 检查失败
- 修复1：在 `findBossSvgDownloadIcon` 中增加 xlink:href 回退路径——当元素的 href/xlink:href 含 "download" 且位于 `attachment-resume-btns` 内时，绕过 `isBossSvgDownloadDescriptor` 和 `isStrictResumeActionArea` 的严格检查
- 修复2：新增 `tryVueDirectDownload(target)` 函数——通过 Vue 组件实例 `__vue__.$parent.href` 直接提取下载 URL，用 `<a download>` 触发下载，彻底绕过合成事件无法触发 Vue 处理器的问题
- 修复3：`clickBossSvgDownloadIcon` 优先尝试 Vue 直链下载，失败时回退 `clickElementReliably`
- 诊断发现：`.click()` 和 `dispatchEvent(MouseEvent)` 均无法触发 BOSS 的 Vue 事件处理器，仅真实浏览器输入事件或直接调用下载 URL 有效

**版本同步：**

- `app/components/layout.py` APP_VERSION → V2.00
- `recruitment_assistant/services/boss_ws_bridge.py` BOSS_BRIDGE_VERSION → 1.73.1
- Chrome 扩展端版本未变（content.js 逻辑修改不涉及版本号协商）

#### 扩展端清理待办（后续执行）

- 删除 `results.downloaded` 字段（content.js:57，从未读取）
- 删除 `emitResumePreviewDiagnostics`、`clickPointReliably`、`collectResumePreviewDiagnostics` 三个未调用函数
- 删除 `emitAttachmentDebug` 空壳及其调用点
- `candidateResourceIdMap.clear()` 在 stop_collect 分支补齐

#### 长跑稳定性观察点

- 每轮跑完读 `<run_id>_events.jsonl` 末尾的 `run_metrics_summary`
- 关注 avg 下载时长是否随候选人数上升劣化
- 关注"持久化拒绝"是否反复出现（hash/命名冲突）
- 关注 Chrome 下载目录遗漏数是否非零（扩展 ack 链路偶发丢失）

## 2026-05-16

### V1.99 BOSS 下载失败手动示范学习

#### 已完成内容

- 实现自动下载失败后的手动示范学习流程：当 boss-svg、已学习按钮或通用自动下载点击后仍未触发 Chrome 下载事件时，内容脚本暂停采集并提示用户手动点击当前页面真实下载按钮。
- 新增紫色实时日志提示：`无法触发下载按钮，请你手动点击下载按钮供系统分析学习。`
- 用户手动点击后，系统捕获点击组件的 DOM 路径、描述、窗口相对坐标、iframe 相对坐标、iframe src 等信息，并等待 Chrome 下载完成事件确认该点击确实触发下载。
- 下载确认成功后保存学习结果到浏览器 `localStorage`，后续候选人优先复用已学习下载按钮；实时日志输出：`刚才用户的手动点击 ... 完成下载，我已经记录了如下信息...`。
- Chrome 后台将 `manual_user_click` 下载意图有效期扩展到 90 秒，避免用户手动操作耗时较长导致下载意图提前过期。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.99`。
  - `recruitment_assistant/services/boss_ws_bridge.py` BOSS 后端桥接版本更新为 `1.69.0`，期望扩展与内容脚本版本更新为 `1.65.0`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.65.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.65.0`。

### V1.98 BOSS 下载失败诊断与旧预览兜底

#### 已完成内容

- 针对叶宇琪、赵女士下载点击后无 Chrome 下载事件的问题，增加下载按钮候选与点击后诊断日志：记录首选按钮路径、描述、SVG/use 图标线索、相邻按钮、可见 iframe 与页面提示，便于确认 `popover icon-content` 是否真实下载按钮。
- 针对简永杰、蔡金昌出现 PDF iframe 但被判定为 stale 的问题，增强旧预览关闭诊断：记录关闭按钮候选数量与剩余预览信息。
- 加入旧预览兜底判断：当预览 fingerprint 未变化，但预览内容中的姓名/年龄与当前候选人匹配时，允许继续使用该预览进入下载链路，减少关闭旧预览失败导致的误跳过。
- 后端实时日志显示关键诊断摘要，包括 boss-svg 命中路径、自动下载按钮路径、下载点击后 frame/toast 状态、旧预览 stale 判断结果。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.98`。
  - `recruitment_assistant/services/boss_ws_bridge.py` BOSS 后端桥接版本更新为 `1.68.0`，期望扩展与内容脚本版本更新为 `1.64.0`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.64.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.64.0`。

### V1.97 BOSS 下载控件识别与扫描统计修复

#### 已完成内容

- 收紧 BOSS 简历下载控件识别：`boss-svg` 下载图标只在简历弹窗的 `attachment-resume-btns`、`resume-footer`、`resume-detail` 等区域内匹配，避免误点右侧栏 `rightbar-item.add-to-label` 等非下载图标。
- 加固通用下载按钮识别：排除 `rightbar`、`page-content`、聊天主容器等大面积非按钮节点，降低将页面容器误当下载按钮点击的概率。
- 保留并强化点击真实交互父级的策略，优先点击 `icon-content`、`popover`、按钮等可交互节点。
- 修复实时日志标题栏扫描人数比候选人列表少一人的问题：内容脚本上报 `scanned_count`，后端保存该字段，页面显示时优先使用明确扫描人数，并兼容候选人列表数量兜底。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.97`。
  - `recruitment_assistant/services/boss_ws_bridge.py` BOSS 后端桥接版本更新为 `1.67.0`，期望扩展与内容脚本版本更新为 `1.63.0`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.63.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.63.0`。

### V1.96 Streamlit / WebSocket 单实例启动保护

#### 已完成内容

- 修复重复启动两套 Streamlit / BOSS WebSocket 服务导致 `8765` 端口占用并触发 `[Errno 10048]` 的问题。
- `scripts/run_streamlit.py` 启动前会检测 `8501` 与 `8765` 端口；只要发现页面服务或 BOSS WebSocket 已运行，就直接提示访问现有页面并阻止重复启动。
- Streamlit 启动参数固定为 `--server.address 127.0.0.1 --server.port 8501`，减少访问地址和端口漂移。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.96`。

### V1.95 BOSS 下载候选人绑定防串档

#### 已完成内容

- 加固 Chrome 扩展下载事件与候选人绑定：为每次下载生成 `download_request_id`，内容脚本仅接受当前请求 ID 与当前轮次匹配的下载结果，避免上一位候选人的下载事件污染后一位候选人。
- 将后台脚本的下载意图从单个 `lastDownloadIntent` 扩展为短时队列，下载事件绑定后立即消费；未匹配到下载意图的 Chrome 下载事件不再强行绑定到最近候选人。
- 直接 PDF iframe 下载改为以 `chrome.downloads.download()` 回调返回的 `downloadId` 显式绑定，`downloads.onCreated` 发现已有绑定时不再覆盖候选人信息。
- 点击附件简历前主动关闭旧简历预览，并在点击后只接受 fingerprint 变化的新预览，降低旧 PDF iframe / 旧弹窗污染后续候选人的风险。
- 后端保存简历时增加本轮内容 hash 防串档兜底：同一份简历内容如果已归属其他候选人，本次保存会被拦截且不会计入下载。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.95`。
  - `recruitment_assistant/services/boss_ws_bridge.py` BOSS 后端桥接版本更新为 `1.65.0`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.61.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.61.0`。

### V1.94 BOSS 历史批次任务记录

#### 已完成内容

- 参考智联采集模块的 `CrawlTaskService` 方式，为 BOSS 采集建立历史批次任务记录。
- BOSS 采集开始时自动创建 `platform_code="boss"` 的 `crawl_task` 记录，记录任务名称、任务类型、采集配置与目标下载份数。
- BOSS 采集完成、停止、扩展断开或扩展错误时，自动更新历史任务状态、获取数量、跳过数量、完成时间与错误信息。
- BOSS 简历保存时将当前历史任务 ID 写入 BOSS 候选人去重记录，便于候选人与批次任务关联追溯。
- 优化页面底部 `BOSS直聘历史批次任务列表`：显示真实任务记录、中文状态、跳过数量、任务名称和错误信息。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.94`。
  - `recruitment_assistant/services/boss_ws_bridge.py` BOSS 后端桥接版本更新为 `1.64.0`。
  - Chrome 扩展与内容脚本未修改，期望版本保持 `1.60.0`。

### V1.93 BOSS 暗淡附件简历索要流程加固

#### 已完成内容

- 将 BOSS 采集页复选框 `需要时索要简历` 改名为 `索要简历`，保持配置字段 `request_resume_if_missing` 不变。
- 暗淡 `附件简历` 按钮流程继续先检查聊天窗口是否存在 `简历请求已发送` 等已索要标记，命中时直接跳过，避免重复索要。
- 加固自动索要简历结果判定：点击暗淡附件按钮并点击确认弹窗后，只有检测到新增 `简历请求已发送` 等文本时才上报 `resume_request_success` 并计入索要人数。
- 新增 `resume_request_unconfirmed` 事件：区分“已点击确认但未检测到请求发送成功”和“未找到确认按钮”，页面候选人列表与实时日志同步显示明确原因。
- 同步扩展版本，确保下一轮测试可识别浏览器实际加载的新内容脚本。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.93`。
  - `recruitment_assistant/services/boss_ws_bridge.py` BOSS 后端桥接版本更新为 `1.63.0`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.60.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.60.0`。

### V1.92 BOSS 结果窗口自动滚动加固与实时日志精简

#### 已完成内容

- 加固 `实时日志` 与 `候选人列表` 的自动滚动：不再依赖锚点相邻关系，改为在 iframe 内直接查找 `.boss-log-box` / `.boss-candidate-box` / `.boss-empty-box` 并滚动到底部，同时对候选人表格最后一行执行 `scrollIntoView()`。
- 自动滚动增加多次延迟触发：`requestAnimationFrame`、50ms、200ms，避免 Streamlit iframe 内容尚未完成布局时滚动失效。
- 根据本轮测试日志精简页面实时日志：隐藏下载链路中的 Chrome 后台请求/响应握手、PDF iframe 地址解析、直接下载创建等中间过程，只保留候选人识别、去重、附件按钮状态、预览识别、下载创建、保存和最终统计等关键信息。
- 将 `最大采集数量` 调整为 `目标下载份数`，并将日志中的 `最大数量` 调整为 `目标下载数`，避免用户误解为最多扫描候选人数；当前逻辑是持续扫描，直到成功下载目标份数或任务结束。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.92`。
  - `recruitment_assistant/services/boss_ws_bridge.py` BOSS 后端桥接版本更新为 `1.62.0`。
  - Chrome 扩展与内容脚本未修改，期望版本保持 `1.59.0`。

### V1.91 BOSS 采集结果标题统计增强

#### 已完成内容

- `实时日志` 与 `候选人列表` 继续使用 iframe 内部容器自动滚动到底部，采集刷新时随最新输出自动定位。
- `实时日志` 标题栏右侧新增扫描摘要：已扫描候选人数、已扫描耗时、每人平均耗时。
- `候选人列表` 标题栏右侧新增结果摘要：已记录候选人数、跳过人数、去重跳过人数、索要简历人数、成功下载份数。
- BOSS 后端运行状态新增 `resume_request_count`，在成功索要简历时累计，供页面标题统计和本轮摘要使用。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.91`。
  - `recruitment_assistant/services/boss_ws_bridge.py` BOSS 后端桥接版本更新为 `1.61.0`。
  - Chrome 扩展与内容脚本未修改，期望版本保持 `1.59.0`。

### V1.90 BOSS 日志窗口样式修复与耗时日志精简

#### 已完成内容

- 修复 `实时日志` 与 `候选人列表` 改为 `components.html()` 后样式丢失的问题：将日志/候选人列表所需 CSS 注入到 iframe 内部，恢复颜色、字号、固定高度和内部滚动条。
- 修正自动滚动方式：改为滚动 iframe 内部的 `.boss-log-box` / `.boss-candidate-box` 容器，避免只滚动外层页面导致内部滚动条不可见。
- 精简页面实时日志中的冗余调试信息，去掉候选人识别位置、DOM path、rect、具体下载链接、iframe src 等长文本。
- 新增关键步骤耗时输出：候选人信息识别耗时、下载前去重检查耗时、附件简历按钮查找耗时，便于后续定位采集速度瓶颈。
- 保留完整事件数据写入 `logs/boss_extension/YYYYMMDD/run_*.jsonl`，页面只显示适合观察流程的简洁日志。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.90`。
  - `recruitment_assistant/services/boss_ws_bridge.py` BOSS 后端桥接版本更新为 `1.60.0`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.59.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.59.0`。

### V1.89 BOSS 采集结果窗口自动滚动

#### 已完成内容

- 将 BOSS 采集页面的 `实时日志` 输出窗口改为自动滚动到最新日志，采集刷新时默认显示底部最新内容。
- 将 `候选人列表` 从 `st.dataframe` 改为自定义滚动表格，按采集顺序展示并自动滚动到最新候选人。
- 保留候选人列表 300px 高度、固定表头、下载/跳过状态颜色区分，减少长任务时手动滚动查看最新结果的操作。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.89`。
  - `recruitment_assistant/services/boss_ws_bridge.py` BOSS 后端桥接版本更新为 `1.59.0`。
  - Chrome 扩展与内容脚本未修改，期望版本保持 `1.58.0`。

### V1.88 BOSS 测试轮次模块版本日志

#### 已完成内容

- 每次重置 BOSS 测试轮次后，实时日志在 `新测试轮次已创建` 后立即输出模块版本信息。
- 版本信息包含：页面版本、BOSS 后端桥接版本、期望 Chrome 扩展版本、期望内容脚本版本、当前已连接扩展版本。
- 事件日志同步写入 `module_versions`，便于通过 `logs/boss_extension/YYYYMMDD/run_*.jsonl` 追溯测试时实际模块版本。
- 扩展连接时输出当前扩展版本与期望版本；版本不匹配时提示在 `chrome://extensions/` 重新加载扩展。
- 内容脚本启动采集时输出当前内容脚本版本、期望版本、下载前去重 key/签名数量和后端确认状态；版本不匹配时提示刷新 BOSS 页面。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.88`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.58.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.58.0`。
  - `recruitment_assistant/services/boss_ws_bridge.py` BOSS 后端桥接版本更新为 `1.58.0`。

### V1.87 BOSS 下载前去重未确认时强制阻断采集

#### 已完成内容

- 针对测试中后端运行实例未加载最新 `boss_ws_bridge.py`，导致前端收到的采集配置缺少 `boss_candidate_keys` / `boss_candidate_signatures` 的问题，增加前端强制保护。
- 后端最新采集配置新增 `boss_pre_dedup_ready=True` 标记，表示下载前去重数据已由后端确认下发。
- 内容脚本收到采集命令后，如果没有收到 `boss_pre_dedup_ready=True`，立即停止采集并输出错误：`BOSS 下载前去重数据未由后端确认下发，已阻止采集以避免重复下载；请重启后端服务后重试`。
- 该保护可防止后端未重启或运行旧代码时，候选人继续进入附件按钮、PDF iframe 和 Chrome 下载链路。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.87`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.57.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.57.0`。

### V1.86 BOSS 下载前去重诊断与强制签名拦截

#### 已完成内容

- BOSS 采集开始时，后端除下发 `boss_candidate_keys` 外，新增下发 BOSS 已入库候选人签名集合 `boss_candidate_signatures`。
- 内容脚本在点击候选人并识别 `姓名/年龄/学历` 后，先执行下载前去重诊断，输出 `BOSS 下载前去重检查: 候选人；key=...；下发key=N 条；下发签名=M 条；key命中=True/False；签名命中=True/False`。
- 下载前去重命中条件扩展为 `candidate_key` 命中或候选人签名命中，避免 JS/Python 归一化差异导致重复候选人进入 PDF iframe 下载链路。
- 下载前去重未命中时，新增日志 `BOSS 下载前去重未命中，开始查找附件简历按钮: 候选人`，用于确认附件按钮状态判断一定发生在去重之后。
- 内容脚本收到采集命令后输出版本和去重数据数量，便于确认浏览器实际加载的是最新扩展脚本。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.86`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.56.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.56.0`。

### V1.85 BOSS 附件简历按钮明暗态流程修正

#### 已完成内容

- 将 BOSS 附件简历按钮状态统一清理为两类：`明亮`、`暗淡`，不再在附件按钮流程中使用“索要简历状态”。
- 去重检查通过后，在右上角寻找“附件简历”按钮，并输出按钮状态日志：`附件简历按钮状态: 候选人；状态=明亮/暗淡`。
- 暗淡按钮时先检查聊天窗口是否已有 `简历请求已发送`：有则直接跳过并输出 `跳过已索要简历的候选人xxx`。
- 暗淡按钮且未发送过请求时，如果用户勾选“索要简历”，自动点击附件按钮并在弹窗点击确认，输出 `根据用户需求，将候选人xxx索要了简历`。
- 暗淡按钮且用户未勾选索要简历时，直接跳过并输出 `跳过无简历候选人xxx`。
- 明亮按钮时直接点击“附件简历”，继续进入 PDF iframe 弹窗识别和下载链路。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.85`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.55.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.55.0`。

### V1.84 BOSS 下载前去重与真实下载统计

#### 已完成内容

- BOSS 采集开始时后端加载 `boss_candidate_record` 已有去重 key，并下发到 Chrome 内容脚本。
- 内容脚本在点击候选人并从右侧顶部红框识别 `姓名/年龄/学历` 后，先按同一规则生成 BOSS 候选人 key；如果命中去重库，立即输出 `boss_dedup_hit` 跳过记录，不再点击“附件简历”、不再识别 PDF iframe、不再触发 Chrome 下载。
- 修正候选人结果列表：下载前去重命中会记录为 `dedup_skipped`，并输出 `BOSS 下载前去重命中，跳过附件识别` 日志。
- 修正完成统计：`collect_finished` 改用后端真实 `downloaded_count` 输出，只有简历成功复制到 `data/attachments/boss/YYYYMMDD/` 后才计入已下载；Chrome 下载完成但后端找不到归档文件时不再计入下载。
- 任务完成摘要新增本轮新增去重数量。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.84`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.54.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.54.0`。

## 2026-05-15

### V1.83 BOSS 去重入库成功日志

#### 已完成内容

- 确认 BOSS 简历成功下载并归档后，会调用 `_upsert_boss_candidate_record()` 写入 BOSS 专用去重数据库。
- 新增去重记录写入成功日志：`BOSS 去重记录已写入: 姓名/年龄/学历`，便于在页面实时日志中确认候选人已入库。
- 保留重复记录日志：`BOSS 去重记录已存在，未新增: 姓名/年龄/学历`，明确区分新增写入和已存在未新增。
- 同步事件日志新增 `resume_saved_dedup_record_created`，记录候选人签名、去重 key、归档文件、路径和内容 hash。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.83`。

### V1.82 BOSS 专用去重表、清库按钮与自动归档

#### 已完成内容

- 新增 BOSS 专用去重表 `boss_candidate_record`，结构参考智联去重记录，但不与智联共用表，避免影响智联采集模块。
- 新增 `BossCandidateRecordService`，支持读取去重 key、写入候选人记录、自动建表和清空 BOSS 去重记录。
- BOSS 简历下载成功后自动归档到项目根目录 `data/attachments/boss/YYYYMMDD/`，归档后删除浏览器下载目录中的源文件。
- BOSS 采集页面新增“清除去重数据库”按钮，位于“生成本轮摘要”右侧，并在清除后输出页面提示。
- 页面新增自动保存提示：如果要自动保存简历，请在 Chrome 浏览器设置中关闭“下载前询问每个文件的保存位置”。
- 任务统计和摘要中保留“新增去重”数量，用于确认本轮新增入库记录。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.82`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.53.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.53.0`。

### V1.79 BOSS 无附件快跳与自动下载兜底

#### 已完成内容

- 修正 BOSS 无附件候选人的跳过判定：附件简历按钮为置灰/暗淡/不可用状态时，直接按无可下载附件处理，不再点击按钮，也不再进入弹窗识别。
- 当未勾选“需要时索要简历”时，置灰附件按钮直接跳过并记录 `need_request_resume`，避免无附件候选人误触发 `resume_preview_not_found`。
- 优化自动下载流程：`boss-svg` 下载图标点击后若未捕获到 Chrome 下载完成事件，不再立即终止为跳过，继续进入已学习下载控件和通用下载按钮兜底识别，提高自动保存成功率。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.79`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.52.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.52.0`。

### V1.77 BOSS 跳过日志每次输出

#### 已完成内容

- 调整 BOSS 跳过记录逻辑：每次收到 `candidate_skipped` 事件都会输出实时日志 `跳过: 候选人签名 (原因)`，不再因为候选人签名已记录而静默返回。
- 重复跳过候选人时仅写入 `candidate_skipped_seen` 事件日志，标记 `duplicate_record=true`，用于追踪每一次跳过触发。
- 候选人结果列表、`skipped_count` 和 `skip_reason_counts` 仍保持按候选人签名去重，避免重复写入结果列表和重复累计统计。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.77`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.50.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.50.0`。

### V1.76 BOSS 顶部信息分字段识别与未识别熔断

#### 已完成内容

- 修复顶部红框区域姓名、年龄、学历分属不同 DOM 节点时无法识别的问题：改为在右侧沟通页顶部同一行内分别定位姓名节点、年龄节点和学历节点，再组合为候选人信息。
- 点击候选人后新增最多 2.2 秒的顶部信息等待，等待右侧详情区切换完成后再读取红框信息，避免刚点击后读到旧页面或空白状态。
- 当右侧顶部红框区域仍无法识别完整个人信息时，立即结束本轮采集，不再继续跳过大量候选人，避免在错误候选人页面继续识别弹窗。
- 简历预览页姓名识别改为始终优先使用当前候选人顶部红框识别出的姓名，避免 DOM 弱识别时把弹窗按钮文案如“确定向牛”等当作姓名。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.76`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.49.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.49.0`。

### V1.75 BOSS 个人信息唯一识别区域限定

#### 已完成内容

- 按截图确认的唯一位置，将 BOSS 候选人个人信息识别限定为右侧沟通页顶部基础信息栏的三个字段：姓名、年龄、学历。
- 移除个人信息识别中的左侧候选人列表兜底和页面正文前 80 行兜底，避免聊天内容、列表摘要或页面其他文本被误识别为姓名。
- 新增 `top_profile_red_boxes` 识别来源，日志明确标记“右侧沟通页顶部红框区域：姓名、年龄、学历”。
- 当右侧顶部红框区域未识别到完整姓名、年龄、学历时，直接返回 `待识别/待识别/待识别`，不再从其他区域猜测。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.75`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.48.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.48.0`。

### V1.74 BOSS 弹窗等待 3 秒与姓名识别位置日志

#### 已完成内容

- 将 BOSS 附件简历弹窗识别默认等待周期从 12 秒缩短为 3 秒，同步修正附件调试日志中的等待超时值，避免页面显示仍为 12000ms。
- 候选人姓名/年龄/学历识别结果新增来源位置字段：识别来源、说明、元素矩形坐标、DOM 路径、class 和文本样本。
- BOSS 实时日志中的“点击候选人”现在会输出姓名识别的具体页面位置，便于确认当前姓名来自右侧详情区、左侧候选人列表，还是正文兜底文本。
- 当前识别优先级为：右侧详情/聊天区域上半部分候选人信息 > 左侧候选人列表当前点击项 > 页面正文前 80 行兜底文本。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.74`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.47.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.47.0`。

### V1.73 BOSS 弹窗弱识别与候选人信息修正

#### 已完成内容

- 修复 BOSS 附件简历点击后只有普通 DOM 弹窗、没有 PDF iframe 时被判定为 `resume_preview_not_found` 的问题：未命中 PDF iframe 时改用最大疑似弹窗继续后续下载按钮识别。
- 候选人姓名识别不再从“无年龄信息的文本块”里硬猜姓名，避免把聊天内容如“可以看一”“有过相近”“学院的计”“数据”等误当作姓名。
- PDF iframe 扫描日志由等待循环每轮输出改为每个候选人只记录首次扫描，减少重复日志噪音。
- 说明：重复扫描原因为系统在 12 秒等待窗口内轮询弹窗是否加载完成，此机制保留，但日志不再重复刷屏。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.73`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.46.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.46.0`。

### V1.72 BOSS 归档目录固定到项目根目录

#### 已完成内容

- 按要求将 BOSS 简历归档目录固定为项目根目录下 `data/attachments/boss/YYYYMMDD/`，路径格式对齐智联采集模块的日期子目录结构。
- 归档路径不再依赖运行进程当前工作目录或外部配置覆盖，避免保存到非项目根目录下的 `data/attachments`。
- 保持文件命名格式为 `姓名-年龄-学历-BOSS直聘-YYYYMMDD-HHMMSS-序号.pdf`，同名时追加 `-1`、`-2`。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.72`。

### V1.71 BOSS 下载文件强制落盘与本地归档

#### 已完成内容

- 修复 BOSS 附件下载窗口已打开、但 Chrome 下载被取消或未稳定进入本地归档的问题。
- Chrome 后台直接下载时显式指定下载文件名到 `Boss直聘/姓名-年龄-学历-BOSS直聘-时间.pdf`，避免浏览器因 URL 无文件名、非法候选人签名或路径分隔符触发保存异常。
- Python 侧收到 `resume_downloaded` 后改为从 Chrome 下载目录复制文件到 `data/attachments/boss/YYYYMMDD/`，不再移动浏览器下载原文件，降低跨盘符/文件占用导致归档失败的概率。
- 保留原有 `resume_downloaded` 成功事件驱动，只有 Chrome 真实下载完成后才计入已保存。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.71`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.45.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.45.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认页面版本为 `V1.71`、扩展版本为 `1.45.0` 后再测试。
- 本版下载成功后，Chrome 默认下载目录应出现 `Boss直聘/...pdf`，同时系统归档目录应出现 `data/attachments/boss/YYYYMMDD/...pdf`。
- 如果仍出现 `download_error:USER_CANCELED`，优先检查 Chrome 是否开启了“每次下载前询问保存位置”或下载权限弹窗。

### V1.70 BOSS PDF iframe 下载链路日志增强

#### 已完成内容

- 强化 BOSS 附件简历 PDF iframe 识别：支持从 `/bzl-office/pdf-viewer-b?url=...` 中解析真实 `preview4boss` 地址，并统一转换为 `https://www.zhipin.com/...` 绝对下载 URL。
- 增强 Content Script 到 Chrome 后台的直接下载请求链路日志：新增请求发送、后台响应、响应超时、下载结果捕获等事件，便于定位 direct download 卡住位置。
- 增强 Chrome 后台直接下载可观测性：记录请求接收、URL 校验、下载启动、响应发送、回调超时和失败原因，并对 `chrome.downloads.download()` 增加超时兜底。
- 扩大 `boss-svg svg-icon [object SVGAnimatedString]` 下载组件扫描范围，覆盖 iframe 根节点、父容器、弹层、抽屉、viewer、body 等候选区域。
- BOSS 采集页面日志高亮新增 `PDF iframe`、`boss-svg`、`捕获下载链接` 等关键字，关键下载链路更容易识别。
- 候选人姓名解析增加 UI 文案过滤，剔除 `下载简历`、`查看简历`、`下简历`、`附件简历` 等非姓名词，降低姓名误识别概率。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.70`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.44.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.44.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认页面版本为 `V1.70`、扩展版本为 `1.44.0` 后再测试。
- 本版命中 PDF iframe 后应看到真实 `https://www.zhipin.com/wflow/zpgeek/download/preview4boss/...` 下载地址。
- 若后台下载仍未创建，应根据新增的 `Chrome 后台收到直接下载请求`、`Chrome 后台准备启动直接下载`、`Chrome 后台直接下载响应` 或超时日志继续定位。

## 2026-05-14

### V1.51 BOSS 附件点击后直达真实弹窗识别

#### 已完成内容

- 调整 BOSS 附件简历按钮命中后的执行顺序：找到按钮并上报 `resume_button_found` 后，不再进入 `enabled/request/requested` 等中间状态判断。
- 无论按钮状态为 `view`、`request`、`requested` 还是 `unknown_resume`，都会先直接点击附件入口，再立即调用 `startResumePreviewRecognition()`。
- `startResumePreviewRecognition()` 继续进入 `waitForResumePreview()`；只有 `waitForResumePreview()` 真实入口会上报 `resume_preview_recognition_started`。
- 页面实时日志中的 `开始识别弹出页面: xxx；真实等待入口=wait_entered` 仍然只代表已经真实进入弹窗识别循环，不再来自页面渲染伪造。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.51`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.25.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.25.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认页面版本为 `V1.51`、扩展版本为 `1.25.0` 后再测试。
- 本版看到 `附件按钮:` 后，下一步应直接出现 `已点击附件简历入口` 与 `开始识别弹出页面: xxx；真实等待入口=wait_entered`。
- 如果出现 `弹出页面识别等待完成: xxx；结果=未发现`，说明已经进入真实识别流程，但当前弹窗定位规则仍未匹配到 BOSS 页面里的实际弹层。

### V1.50 BOSS 识别提示绑定真实等待入口

#### 已完成内容

- 撤销 `V1.49` 中在页面渲染层伪造插入“开始识别弹出页面”的做法，避免日志显示与真实流程脱节。
- 将“开始识别弹出页面”严格绑定到 `waitForResumePreview()` 的真实入口：只有进入真实弹窗等待循环时，才会上报 `resume_preview_recognition_started` 并显示粉色提示。
- 移除发现附件按钮后、点击前等非真实识别阶段的“开始识别弹出页面”上报，避免误导。
- 点击附件按钮后立即调用 `startResumePreviewRecognition()`，并由其进入 `waitForResumePreview()`；不再先等待旧弹窗消失或发送伪阶段提示。
- 新增 `resume_preview_wait_result`：真实等待结束后明确输出“已发现”或“未发现”，用于判断流程确实跑完。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.50`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.24.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.24.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认页面版本为 `V1.50`、扩展版本为 `1.24.0` 后再测试。
- 本版如果看到 `开始识别弹出页面: xxx；真实等待入口=wait_entered`，就表示代码已经实际进入 `waitForResumePreview()` 识别循环。
- 如果后续出现 `弹出页面识别等待完成: xxx；结果=未发现`，说明流程已进入识别但识别规则没有找到弹窗，需要继续根据诊断日志修正弹窗定位规则。

### V1.49 BOSS 实时日志强制插入识别开始提示

#### 已完成内容

- 修正“附件按钮”行虽然变粉，但没有单独出现“开始识别弹出页面”的问题。
- 在 BOSS 实时日志渲染层增加强制插入逻辑：只要日志中出现 `附件按钮:` 且包含 `unknown_resume` 或 `附件简历`，页面会立即在其下一行插入粉色 `开始识别弹出页面`。
- 该提示不再依赖扩展事件、WebSocket 事件顺序或后端日志级别，确保用户看到“附件按钮”后必定看到明确的识别流程开始提示。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.49`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.23.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.23.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认页面版本为 `V1.49`、扩展版本为 `1.23.0` 后再测试。
- 本版看到 `附件按钮: xxx [unknown_resume] 附件简历` 后，其下一行必须出现粉色 `开始识别弹出页面`。

### V1.48 BOSS 附件按钮日志直接高亮

#### 已完成内容

- 将 `resume_button_found` 的“附件按钮”日志本身改为粉色高亮，并在同一行追加“开始识别弹出页面”，不再依赖后续单独一行粉色日志。
- 页面日志分类新增兜底：只要日志包含 `附件按钮:` 且包含 `unknown_resume`、`附件简历` 或 `开始识别弹出页面`，即使后端日志级别未生效，前端也会强制显示粉色。
- 保持点击附件后先进入弹出页面识别流程；未识别到强弹窗时仍按 `resume_preview_not_found` 跳过，不应进入下载学习。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.48`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.22.0`。
  - `chrome_extension/content.js` 内容脚本版本更新为 `1.22.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认页面版本为 `V1.48`、扩展版本为 `1.22.0` 后再测试。
- 本版看到 `附件按钮: xxx [unknown_resume] 附件简历 —— 开始识别弹出页面` 这一行时，它本身就应是粉色。
- 如果这一行仍不是粉色，说明浏览器页面不是最新 Streamlit 服务、页面未刷新，或实际打开的是旧服务进程。

### V1.47 BOSS 识别入口日志前移与日志窗口扩容

#### 已完成内容

- 将“开始识别弹出页面”的粉色提示进一步前移到 `resume_button_found` 事件：只要发现 `view/unknown_resume` 类型的附件按钮，Python 端立即输出粉色提示，不再依赖后续点击事件链路。
- Content Script 新增 `boss_ui_stage` 关键阶段事件，并在发现附件按钮、等待旧弹窗消失前、正式点击前重复上报“开始识别弹出页面”。
- 关键事件重复上报从 2 次增加到 4 次，降低 Chrome 扩展消息偶发丢失导致实时日志缺失的概率。
- 实时日志显示窗口从最近 45 条扩展到最近 120 条，避免关键粉色日志被候选人诊断日志挤出可视范围。
- 保持未识别到强弹窗时只输出诊断并跳过，不进入下载意图或人工点击学习。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.47`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.21.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认扩展版本为 `1.21.0` 后再测试。
- 本版只要实时日志出现 `附件按钮: xxx [unknown_resume] 附件简历`，下一行附近就应出现粉色“开始识别弹出页面”。

### V1.46 BOSS 弹窗识别入口强制兜底与旧脚本重载

#### 已完成内容

- 强化“附件简历”点击后的弹窗识别入口：
  - 点击附件前、点击后、进入等待函数时都会重复上报 `resume_preview_recognition_started`，确保实时日志优先出现粉色“开始识别弹出页面”。
  - `resume_attachment_clicked` 改为关键事件重复发送，降低 Chrome 消息回调丢失导致前端无粉色提示的概率。
  - 修复 WebSocket 重置轮次时 `reset_content_script` 命令缩进错误，避免扩展未连接时出现未定义命令异常。

- 强化弹窗定位诊断与兜底：
  - 放宽弹窗根节点识别范围，加入 `drawer/popup/pop/layer/iframe/object/embed` 等候选。
  - 弱候选只输出“疑似发现弹出页面”和诊断，不再直接进入人工下载学习，避免未确认弹窗时出现 `manual_download_click_timeout`。
  - 若未识别到强弹窗，统一按 `resume_preview_not_found` 跳过，并输出诊断，不再进入下载意图或人工点击学习。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.46`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.20.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认扩展版本为 `1.20.0` 后再测试。
- 本版测试中，看到“附件按钮”后必须紧接着出现粉色“开始识别弹出页面”。如果仍不出现，优先判定为浏览器仍运行旧 content script 或扩展消息链路异常。
- 在出现“发现弹出页面”并暂停确认前，不应再出现“下载意图已登记”或 `manual_download_click_timeout`。

### V1.45 BOSS 附件点击后强制先识别弹窗

#### 已完成内容

- 修正附件点击后的流程顺序：点击“附件简历”后立即进入 `waitForResumePreview` 弹窗识别等待，不再先走索要确认判断或下载学习链路。
- `unknown_resume` 状态下，只有在完整弹窗识别等待结束且仍未发现弹窗时，才继续判断是否属于索要简历成功。
- 修复历史学习状态干扰测试的问题：每次开始新采集时重置弹窗识别学习状态，避免旧的 `learnedClick` 直接触发 `download_intent`，绕过弹窗识别流程。
- 将“开始识别弹出页面”同时绑定到附件点击事件和独立识别开始事件，确保点击入口后实时日志能立刻看到粉色提示。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.45`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.19.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认扩展版本为 `1.19.0` 后再测试。
- 本版测试中，看到“附件按钮”后，点击附件入口应紧接着出现粉色“开始识别弹出页面”；在识别流程结束前不应出现“下载意图已登记”。

### V1.44 BOSS 弹出页识别开始提示与人工辅助识别

#### 已完成内容

- 点击明亮的“附件简历”入口后，立即进入弹出页寻找流程，并向实时日志输出粉色提示“开始识别弹出页面”。
- 该提示在附件入口点击事件之后立即上报，早于 1 秒即时诊断、索要确认判断和后续下载学习流程，用于确认系统确实开始寻找弹窗。
- 保留后续“开始尝试获取弹出页面中的信息……”“发现弹出页面”“成功获取以下信息：……”流程，便于区分开始识别、识别成功和信息提取成功。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.44`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.18.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认扩展版本为 `1.18.0` 后再测试。
- 测试“陈柱荣”或“李子志”时，点击附件简历入口后应立即看到粉色“开始识别弹出页面”。若随后仍没有“发现弹出页面”，请保留后续“弹出页识别诊断”日志。

### V1.43 BOSS 附件点击即时诊断与停止竞态修复

#### 已完成内容

- 修复停止后异步流程仍继续进入人工学习的问题：
  - 在等待简历弹出页、点击附件后确认、进入下载学习前增加停止状态检查。
  - 用户点击“停止”后，不再继续发出下载意图或进入“正在记录你的操作”。

- 强化附件简历点击后的即时诊断：
  - 点击附件简历入口 1 秒后立即输出一次“弹出页识别诊断”，不再只等最终超时。
  - 简历弹出页等待时间从 10 秒延长到 45 秒，并在 5 秒时输出早期诊断。
  - 诊断日志现在始终输出候选弹层、`iframe/object/embed` 和大块 DOM 前 5 项摘要。
  - 对已确认有附件简历的“李子志”和“陈柱荣”，未发现弹出页时会明确提示判定为识别失败。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.43`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.17.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认扩展版本为 `1.17.0` 后再测试。
- 请优先测试“陈柱荣”：点击附件简历后 1 秒内应看到“弹出页识别诊断”，随后若 5 秒仍未识别会再次输出早期诊断。

### V1.42 BOSS unknown_resume 误判索要修复与弹出页诊断触发

#### 已完成内容

- 修复 `unknown_resume` 状态误跳过有附件简历候选人的问题：
  - 点击“附件简历”后，不再因为检测到确认弹窗点击就直接按 `resume_request_clicked` 跳过。
  - 只有页面明确出现“已索要/请求已发送”等索要成功文案时，才按 `resume_requested` 跳过。
  - 若未检测到索要成功，会继续进入简历弹出页识别流程，从而触发“发现弹出页面”或弹出页识别失败诊断。

- 增加附件入口点击后的诊断日志：
  - 点击附件简历入口后输出“已点击附件简历入口”。
  - `unknown_resume` 未确认索要成功时输出粉色提示“附件简历状态不明确，未检测到索要成功，继续尝试识别弹出页面”。
  - 后续若仍未识别到弹出页，会继续输出 V1.41 增加的弹出页识别失败诊断。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.42`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.16.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认扩展版本为 `1.16.0` 后再测试。
- 请优先测试“李子志”：本次应不再直接跳过为 `resume_request_clicked`，而是继续尝试识别弹出页面，并在失败时输出弹出页诊断。

## 2026-05-13

### V1.41 BOSS 简历弹出页识别失败诊断日志

#### 已完成内容

- 增加 BOSS 附件简历弹出页识别失败诊断：
  - 点击附件简历后若未识别到“发现弹出页面”，Content Script 会上报当前 URL、页面标题、视口尺寸、正文样例。
  - 同步采集可见弹层候选、`iframe/object/embed` 内嵌页候选、大块可见 DOM 的标签、类名、位置、尺寸、文本样例和 DOM 路径。
  - 实时日志输出“弹出页识别失败诊断”，并展示候选弹层数量、内嵌页数量、大块 DOM 数量以及前几个候选节点摘要。
  - 对已确认有附件简历的“李子志”，如果点击附件简历后仍未发现弹出页，会在实时日志中明确提示判定为识别失败。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.41`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.15.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认扩展版本为 `1.15.0` 后再测试。
- 请优先测试候选人“李子志”：若仍未出现粉色“发现弹出页面”，请保留“弹出页识别失败诊断”后续日志，用于定位真实简历页所在 DOM、iframe 或新页面。

### V1.40 BOSS 弹出页确认暂停与人工点击学习闭环

#### 已完成内容

- 按新的学习流程重构 BOSS 简历弹出页识别与人工点击学习：
  - 发现弹出页面后实时日志输出粉色提示“发现弹出页面”。
  - 开始解析弹出页信息时输出“开始尝试获取弹出页面中的信息……”。
  - 成功获取姓名、电话、邮箱后输出粉色提示“成功获取以下信息：……”。
  - 获取信息后采集任务自动暂停，等待人工确认；用户点击“继续”后才进入下载点击学习。
  - 继续后输出粉色提示“正在记录你的操作……”。
  - 用户手工点击下载按钮后，记录点击组件、坐标，并在捕获到下载链接后输出粉色学习成功提示。
  - 学习任务成功后采集任务自动结束。

- 优化下载学习链路：
  - 下载意图有效期延长到 120 秒，覆盖人工确认与点击学习场景。
  - 学习完成时避免重复发送普通采集完成事件。
  - BOSS 实时日志新增粉色高亮样式，用于突出关键人工确认和学习节点。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.40`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.14.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认扩展版本为 `1.14.0` 后再测试。
- 测试流程：开始采集 → 查看粉色“发现弹出页面/成功获取以下信息” → 点击页面“继续” → 手工点击下载按钮 → 查看粉色“学习任务成功”并确认采集自动结束。

### V1.39 BOSS 简历页面识别与人工点击学习定位

#### 已完成内容

- 将 BOSS 简历下载从“猜测下载图标直接点击”调整为分阶段学习式定位：
  - 点击“附件简历”后先识别简历预览/弹出页面。
  - 从简历页面文本中提取候选人姓名、电话、邮箱，并输出“发现弹出页面...”实时日志，便于确认识别结果。
  - 首次识别后记录已确认状态，下一次打开简历页面时暂停自动点击并提示“已识别到简历页面，请点击下载”。

- 增加人工点击学习：
  - 在等待人工点击期间捕获用户点击的坐标、相对位置、元素描述、DOM 路径、标签、类名、标题、`aria-label` 和尺寸信息。
  - 将学习到的下载控件信息持久化到页面本地存储，刷新后仍可复用。
  - 人工点击触发真实 Chrome 下载时继续沿用下载意图与下载完成回传链路。

- 增加已学习控件自动点击：
  - 优先使用已记录 DOM 路径定位下载控件。
  - 路径失效时按元素描述做模糊匹配。
  - 仍失败时回退到记录的相对坐标命中元素。
  - 点击前输出“使用已学习下载控件尝试点击”实时日志。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.39`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.13.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认扩展版本为 `1.13.0` 后再测试。
- 第一次打开简历页面时请观察实时日志中的姓名、电话、邮箱是否对应真实简历页面。
- 第二次打开简历页面时请按提示手工点击下载，系统会记录点击控件；后续同类页面将尝试使用该记录自动点击。

### V1.38 BOSS 下载按钮、索要日志与历史批次列表修复

#### 已完成内容

- 继续增强 BOSS 简历预览/弹出页下载按钮点击：
  - `chrome_extension/content.js` 扩展下载控件识别属性，补充 `id`、`data-icon`、`data-name`、`data-testid`、父级按钮/工具栏等线索。
  - 增加预览层/弹窗根节点优先搜索，并对右上角常见工具栏坐标增加 fallback 点击候选。
  - 下载按钮未找到时同步输出可见控件样例与 `iframe/object/embed` 信息，便于继续定位真实 PDF/预览容器。
  - 可靠点击函数改为对命中点元素、原始元素及父级可点击元素逐一触发 `pointer/mouse/click` 事件。

- 增加成功索要简历日志：
  - 索要确认成功或页面出现已索要文案后，Content Script 上报 `resume_request_success`。
  - `BossWSBridge` 输出“成功索要简历: 候选人签名”实时日志。

- 优化 BOSS 候选人列表详情：
  - `boss_ws_bridge.py` 为跳过原因写入 `reason_text` 中文描述。
  - `app/pages/08_BOSS采集.py` 详情列优先展示文件名或中文原因，不再直接暴露 `resume_requested`、`download_timeout` 等内部原因码。

- 优化 BOSS 页面底部：
  - 移除“首次使用？查看扩展安装指引”。
  - 增加“BOSS直聘历史批次任务列表”，字段与智联采集历史批次列表保持一致：批次ID、时间、目标网站、目标数量、获取数量、耗时、状态。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.38`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.12.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认扩展版本为 `1.12.0` 后再测试。
- 若仍未点击到下载图标，请保留“未找到下载图标；候选控件”和 `frames` 诊断信息。

### V1.37 BOSS 重复日志、顶部扫描与确认/下载点击增强

#### 已完成内容

- 修复 BOSS 扩展重复注入与重复候选人记录：
  - `chrome_extension/content.js` 增加版本化单例守卫，避免同一页面重复注入同版本脚本。
  - 开始新采集时清理旧下载等待器，避免旧轮次状态影响新轮次。
  - `recruitment_assistant/services/boss_ws_bridge.py` 改为同一候选人签名只保留第一个最终结果，防止同一候选人先 `resume_requested` 后又记录 `duplicate`。

- 修复候选人列表起点：
  - 每轮采集扫描前自动定位左侧候选人列表容器并滚动到顶部。
  - 新增“候选人列表已回到顶部”诊断日志，便于确认是否从顶部开始。

- 增强无简历候选人的索要流程：
  - 点击暗淡“附件简历”时使用更可靠的鼠标事件点击。
  - 索要简历确认按钮限定在弹窗/浮层区域内查找，并输出“已点击索要简历确认”或“未找到索要简历确认按钮”诊断日志。
  - 已索要文案判断改为对当前聊天区域做前后计数比较，减少被其它聊天记录误判为当前候选人已索要。

- 增强简历预览页下载图标点击：
  - 扩大下载图标识别范围，补充 `download` 类名、顶部右侧工具栏、PDF/预览容器等线索。
  - 下载点击改为组合 `pointer/mouse/click` 事件，提高 SVG 图标和父级按钮的点击成功率。
  - 下载图标未找到时输出候选控件诊断日志，便于定位宋文滔、喻强胜等简历预览页的真实控件结构。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.37`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.11.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，并刷新 BOSS 沟通页，确认扩展版本为 `1.11.0` 后再测试。
- 若仍出现下载图标未点击，请保留“未找到下载图标；候选控件”日志，用于继续精确匹配页面控件。

### V1.36 BOSS 候选人去重、下载图标与日志颜色修复

#### 已完成内容

- 修复 `chrome_extension/content.js` 候选人列表重复识别：
  - 扫描候选人列表时按姓名、年龄、学历签名去重，避免每位候选人被记录两次。
  - 遇到运行内重复签名时静默跳过，不再输出重复的 `duplicate` 跳过日志。

- 修复候选人姓名识别：
  - 保留“先生/女士”称谓，避免“梁先生”被截断为“梁”。

- 增强附件简历处理与下载图标定位：
  - 点击 `unknown_resume` 后不再只要确认弹窗出现就立即判定索要成功，改为等待页面真实出现已索要文案。
  - 若未出现已索要文案，则继续尝试寻找简历页面下载按钮。
  - 下载按钮识别增加 `svg/use`、`icon-download`、`aria-label/title/href/xlink:href` 等线索，并扩大预览页右上角下载图标搜索范围。

- 修复 BOSS 桥接日志重复记录：
  - `recruitment_assistant/services/boss_ws_bridge.py` 增加候选人结果去重，防止同一候选人同一原因重复写入候选人列表和实时日志。

- 对齐智联采集模块日志颜色规范：
  - `app/pages/08_BOSS采集.py` 增加成功、失败、跳过、统计颜色分类。
  - 跳过类日志显示为棕黄色，保存/下载成功显示绿色，失败显示红色，统计/完成显示蓝色。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.36`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.10.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，确认页面显示扩展版本 `1.10.0` 后再测试。
- 建议优先验证“梁先生”姓名、宋文滔/喻强胜简历预览页下载图标、无简历候选人的附件按钮索要行为和实时日志颜色。

### V1.35 BOSS 暗淡附件简历索要与下载等待修复

#### 已完成内容

- 修复 `chrome_extension/content.js` 暗淡“附件简历”按钮处理：
  - `unknown_resume` 不再直接按可下载简历处理。
  - 当聊天记录未出现“简历请求已发送”等已索要文案时，会点击附件简历入口并尝试确认弹窗。
  - 确认成功或页面出现已索要文案后，按 `resume_requested` 跳过并继续下一位候选人。

- 修复下载失败导致采集卡住的问题：
  - 点击下载按钮后不再立即增加 `results.downloaded`。
  - `content.js` 新增下载结果等待机制，只有收到 background 回传的真实下载完成消息后才计入下载成功。
  - 下载按钮未出现、下载失败或等待超时均按跳过处理，并继续下一位候选人，避免最大数量为 1 时未下载成功却流程停住。

- 修复 `chrome_extension/background.js` 下载结果回传：
  - Chrome 下载完成时向服务端发送 `resume_downloaded`，同时向 content script 回传 `download_completed`。
  - Chrome 下载失败时向服务端发送 `candidate_skipped`，同时向 content script 回传 `download_failed`。

- 同步版本：
  - `app/components/layout.py` 中 `APP_VERSION` 更新为 `V1.35`。
  - `chrome_extension/manifest.json` 与 `chrome_extension/background.js` 扩展版本更新为 `1.9.0`。

#### 测试提示

- 需要在 `chrome://extensions/` 重新加载本地扩展，确认页面显示扩展版本 `1.9.0` 后再测试。
- 建议继续使用“单步采集1人”验证暗淡附件按钮会触发索要简历；若无真实下载完成事件，应跳过当前候选人并继续下一位。

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

## 2026-05-17 BOSS 采集流程定型（bridge 1.73.0 / 页面 V2.00）

经 23:50 批次实测 3/3 全部下载成功后，把流程"定型"为长期可跑状态。本轮只动 Python 端，扩展端清理另列待办。

### Python 端落地
- `boss_ws_bridge.py`
  - `BOSS_BRIDGE_VERSION` → `1.73.0`
  - 日志精简：`resume_attachment_click_dispatched` / `manual_download_learning_success` / `learned_download_click_used` 三个块从多行 UI 日志压成 1-2 行，详细字段降级到 `logger.debug`
  - 诊断事件 `boss_svg_download_icon_scan_started` / `boss_svg_download_icon_not_found` / `download_button_candidates_detailed` / `download_click_post_diagnostics` / `stale_preview_close_diagnostics` 全部改为 `logger.debug`（仍写入 JSONL 事件日志，不进 UI 实时日志）
  - 新增 `_on_task_finished(status)`，在 `collect_finished` 与 `error` 处理末尾调用，承担：
    1. 向扩展下发 `close_all_resume_previews` 指令（扩展端待支持，先把命令发出去）
    2. 扫描 `~/Downloads/Boss直聘/` 中 `mtime >= run_started_at` 且未在 `data/attachments/boss/YYYYMMDD/` 归档的 PDF，输出高亮提示
    3. 计算并输出 `run_metrics_summary`（总耗时、avg/份、跳过分布、失败分布、Chrome 下载遗漏数），同时写入 JSONL 事件日志
- `app/pages/08_BOSS采集.py`：按钮行从 4 列扩为 5 列，新增"打开简历目录"按钮（复用 `06_智联采集.py` 的 `os.startfile` 模式）
- `app/components/layout.py`：`APP_VERSION` → `V2.00`

### 扩展端清理待办（待解除限制后单独提交）
- `chrome_extension/content.js`
  - 删除 `results.downloaded` 字段（仅写未读）
  - 删除未调用函数 `emitResumePreviewDiagnostics`、`clickPointReliably`、`collectResumePreviewDiagnostics`
  - 删除空壳 `emitAttachmentDebug` 及其 6 个调用点
  - 在 `stop_collect` 分支补齐 `candidateResourceIdMap.clear()`（与 `pendingPersistAcks` 一致）
  - 增加 `close_all_resume_previews` 指令监听：收到后调一次 `forceRemoveStalePdfPreviewFrames("")`
- `chrome_extension/background.js` / `manifest.json`：无需变更

### 后续长跑观察点（埋点已落地）
每轮跑完读 `<run_id>_events.jsonl` 末尾的 `run_metrics_summary`，关注：
- avg 时长是否随候选人数上升而劣化（揣测：候选人列表滚动后 DOM 重渲变慢）
- 失败分布中"持久化拒绝"是否反复出现（说明 hash 冲突 / 命名冲突没解决干净）
- Chrome 下载目录遗漏数是否非零（说明扩展回传 ack 偶发丢失）

攒 3-5 轮可看出趋势，下一轮针对性优化。

## 2026-05-19 简历 AI 解析 ValidationError 修复

### 现象

林鸿俊 / 任果 / 文国斌 / 陈启屏 4 份简历 AI 解析全部抛 `1 validation error for CandidateCreate`，整份候选人无法入库，进入 "AI 解析异常" 失败桶。日志只看到笼统的首行计数，看不到字段路径，难以定位。

### 根因

`recruitment_assistant/schemas/resume_archive.py` 中 4 个嵌套子结构的主标识字段被定义成了**必填 str**：

| 子结构 | 必填字段 | AI 漏识别场景 |
|---|---|---|
| `EducationCreate` | `school_name` | 简历只写"本科 / 计算机专业"没写学校 |
| `WorkExperienceCreate` | `company_name` | 工作段只有岗位描述没标公司抬头 |
| `ProjectExperienceCreate` | `project_name` | 项目段只有正文没标题 |
| `HonorCreate` | `honor_name` | 荣誉段只有"校级 / 三等"等级别 |

只要 AI 在 `educations / work_experiences / project_experiences / honors` 任一数组里返回了一条**主标识缺失**的元素，整份 `CandidateCreate` 就被 pydantic 整体打回 → 候选人主体（姓名、电话、学历、自评等都正常识别到了）也跟着丢。

另外 `app/pages/07_简历管理.py` 的失败分支只 `log(f"... {exc}")`，pydantic ValidationError 多行细节（字段路径 / 实际值 / 错误类型）被吞，只剩首行 "1 validation error for CandidateCreate"，没法直接看到是哪个字段触发的。

### 修复

#### 1. `recruitment_assistant/schemas/resume_archive.py` — 放宽 4 个嵌套字段为 Optional

`school_name / company_name / project_name / honor_name` 全部改为 `str | None = None`，让 AI 偶尔漏识别这些字段时，候选人主体仍能通过 pydantic 校验进入入库流程。

#### 2. `recruitment_assistant/services/resume_archive_service.py` — service 入库前丢弃空壳子记录

ORM 层的 `school_name / company_name / project_name / honor_name` 仍保留 `nullable=False`（不动 DB schema、不需要迁移、保持完整性约束）。在 `create_candidate` 的 4 个 `for` 循环各加一道 `if not xxx: continue`：

- 没学校名的"空壳教育条目"丢弃
- 没公司名的"空壳工作经历"丢弃
- 没项目名的"空壳项目经历"丢弃
- 没荣誉名的"空壳荣誉条目"丢弃

候选人主体 + 联系方式 + 自评 + 完整子条目照常落库，空壳条目不污染数据库，也不会触发 SQLite IntegrityError。

#### 3. `app/pages/07_简历管理.py` — ValidationError 多行细节进日志

```python
err_lines = str(exc).splitlines() or [repr(exc)]
log(f"           ❌ AI 解析异常：{err_lines[0]}")
for sub in err_lines[1:8]:
    log(f"              {sub}")
```

首行进 "AI 解析异常" 计数和摘要，后续最多 7 行（字段路径 / 实际值 / 错误类型）单独缩进打到日志窗口。下次再遇到疑难简历，直接能在 UI 日志里看到 `educations.0.school_name | Field required | input_value=...` 这种定位信息，不用再让 AI 反查代码。

### 设计取舍

考虑过 3 种方案：

- **A. 改 ORM 也允许 NULL** — 数据库已有数据，需要迁移，性价比低
- **B. service 层兜 sentinel "未填写"** — 会污染数据库，且后续过滤需要专门处理
- **C. service 层丢弃空壳子记录**（采用） — 空壳条目本来就没价值，丢掉更合理；DB 完整性约束保留；不需要迁移

### 验证

```powershell
python -c "import ast; [ast.parse(open(p, encoding='utf-8').read()) for p in ['recruitment_assistant/schemas/resume_archive.py','recruitment_assistant/services/resume_archive_service.py','recruitment_assistant/services/resume_ai_service.py','app/pages/07_简历管理.py']]"
```

→ SYNTAX OK。需重启 streamlit 让 Pydantic 模型重新加载，再把 4 份简历重跑验证。

### 顺手收下的两个小改动（同批次）

- `recruitment_assistant/services/resume_ai_service.py`
  - `parse_resume_text` 调用 LLM 时加 `response_format={"type": "json_object"}`，强约束 DeepSeek / OpenAI 兼容端点返回合法 JSON 对象，减少 markdown 代码块剥离的边界情况。
  - `match_candidates` 也试加过 `json_object` 模式，但因为返回的是 JSON **数组**（`json_object` 只支持顶层 object），已回滚并加注释说明。

## 2026-05-25

### 简历解析鲁棒性：docx 文本框 XML 兜底 + PaddleOCR 图像 PDF 回退

本日围绕"简历自动解析"链路里两类长期落入"AI 解析异常 / 文本不足"失败桶的场景做了根因修复 + 兜底通路：

| 失败样本 | 提取结果（修复前） | 失败原因 | 修复后 |
|---|---|---|---|
| 任珮瑜-25 岁-本科-电商运营经理-智联招聘-...002.docx | 0 字符 | 全部正文塞在 `<w:txbxContent>` 文本框，python-docx 不下钻 | 3177 字符（XML 兜底） |
| 文国斌-31 岁-本科-UI 设计师-智联招聘-...005.pdf | 86 字符 | 纯图像 PDF，pypdf/pymupdf 抽不出文字 | 977 字符（PaddleOCR） |
| 周辉-29 岁-本科-UI 设计师-智联招聘-...003.pdf | — | 单页 539×13177 pt 屏滚长图，OpenCV warpPerspective 触发 SHRT_MAX 断言 | 直接跳过该页（阈值 8000pt）|

---

#### A. docx 文本框排版兜底

**根因**：python-docx 的 `Document().paragraphs` / `tables` 只遍历 body 顶层段落和表格，**不下钻** `<w:txbxContent>`（Word 文本框）/ `<w:sdt>`（内容控件）/ `<w:pict>` / `<w:drawing>`。设计感强的简历模板（智联/前程模板尤其常见）经常把全部正文塞进文本框做版式，结果 `paragraphs/tables` 全空 → 外层 `is_empty_or_corrupted` 因 `< 50` 字符把文件误判为"空白/损坏"，整份简历直接归入失败桶。

任珮瑜.docx 验证：zip 完整、`Document()` 能打开、`word/document.xml` 196 KB、`<w:txbxContent>` × 22、`<w:t>` 标签数百个 —— 但 paragraphs/tables 都为 0，全部 3058 字符正文在文本框里。

**修复**：

新增 `recruitment_assistant/utils/docx_utils.py::docx_xml_text_fallback`，直接用 `zipfile` 打开 docx，读 `word/document.xml` + 所有 `word/header*.xml` / `word/footer*.xml`，正则 `<w:t(?:\s[^>]*)?>([^<]*)</w:t>` 抓全部文本节点拼接。**3 处** docx 提取函数统一接入兜底（常规提取 `< 50` 字符时自动回退到 XML 兜底，取更长者）：

- `recruitment_assistant/utils/docx_utils.py::extract_docx_text`
- `recruitment_assistant/parsers/pdf_resume_parser.py::extract_docx_text`（L177）
- `recruitment_assistant/parsers/pdf_resume_parser.py::extract_text_from_docx`（L676，归档落档路径）

**设计取舍**：

- 不替换 python-docx —— paragraphs/tables 在有标题/表格的常规简历下输出更干净；XML 兜底用于"常规提取产能不足"时
- 不对 `<w:t>` 做 namespace-aware XML 解析 —— 简历模板的 namespace prefix 经常被工具改写（`<w:t>` / `<w14:t>` / 带 `xml:space`），正则比 ElementTree 更鲁棒
- 阈值 50 字符 —— 真正的空 docx 会落到 0–10 字符，有标题但无正文的会落到 30–50；阈值 50 能区分"几乎空"和"全文本"

---

#### B. PaddleOCR 图像 PDF 回退

**根因**：智联 / BOSS 部分简历是"截屏拼接型" PDF（HTML 简历转 PDF 时把页面整体渲染成位图嵌入），pypdf/pymupdf 抽不出文本，进入 AI 解析时整份 prompt 都是无内容字段，AI 输出 nullable 全空 → pydantic 校验失败 → 失败桶。

**修复**：

新增 `recruitment_assistant/parsers/ocr_service.py`，作为**可选模块**通过 `pip install ".[ocr]"` 启用。核心结构：

| 函数 | 作用 |
|---|---|
| `is_paddleocr_available()` | 延迟检查 + 缓存，未安装时不抛错只返回 False |
| `_get_ocr()` | PaddleOCR 单例懒加载，逐级尝试 4 套构造参数兼容 2.x / 3.x |
| `_cache_path(pdf)` | `<pdf>.ocr.txt` 同目录缓存路径 |
| `_extract_text_from_paddle_result()` | 兼容 3.x `OCRResult.rec_texts` + 2.x 嵌套 list `[[box, (text, conf)], ...]` |
| `ocr_pdf_to_text(pdf, log, use_cache, dpi=200)` | 主入口：命中缓存直接返回；否则 pymupdf 渲染每页 → PaddleOCR.predict → 拼接 + 写回 `.ocr.txt` |

`app/pages/07_简历管理.py` 在 `raw_text = extract_text(path)` 后加 OCR 回退分支（PDF 且 `< 200` 字符时触发）：

```python
if path.suffix.lower() == ".pdf" and len(raw_text.strip()) < 200:
    from recruitment_assistant.parsers.ocr_service import (
        is_paddleocr_available, ocr_pdf_to_text,
    )
    log(f"           🖼️ PDF 文本过短（{len(raw_text.strip())} 字符），疑似图像简历，启动 OCR 回退…")
    if not is_paddleocr_available():
        log("           ⚠️ PaddleOCR 未安装（pip install paddlepaddle paddleocr），跳过 OCR")
    else:
        try:
            ocr_text = ocr_pdf_to_text(path, log=log)
            if len(ocr_text.strip()) > len(raw_text.strip()):
                log(f"           ✅ OCR 完成，得到 {len(ocr_text)} 字符")
                raw_text = ocr_text
            else:
                log("           ⚠️ OCR 未识别出更多文本")
        except Exception as exc:
            log(f"           ❌ OCR 异常：{exc}")
```

**Windows 部署痛点（已沉淀到 memory）**：

- `paddlepaddle` 3.3.x 系列（含 3.3.1）在 Windows 上有 PIR/OneDNN 不兼容 bug：`NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]`。环境变量 `FLAGS_use_mkldnn=0` / `FLAGS_enable_pir_in_executor=0` 不能绕过。
- 强制锁定 `paddlepaddle==3.0.0`，并补装 `decorator>=5.3.0`、`astor>=0.8.1`（3.3.x 自带这两个间接依赖，3.0.0 需要显式装）。
- `pyproject.toml` 新增 `[project.optional-dependencies.ocr]`：

```toml
ocr = [
    "paddlepaddle==3.0.0",
    "paddleocr>=3.5.0",
    "decorator>=5.3.0",
    "astor>=0.8.1",
]
```

模型首次自动下载到 `C:\Users\<user>\.paddlex\official_models\`（PP-OCRv5_server_det + rec + textline_ori + UVDoc + doc_ori，合计约 200 MB+）。单页 CPU 模式 20–100 秒（首次含模型加载），后续 < 30 秒；命中 `.ocr.txt` 缓存直接读，二次扫描 0 秒。

---

#### C. 超长图简历跳过策略

**问题**：周辉.pdf 实测物理尺寸 539 × 13177 pt（约 4.65 米高的屏滚截图）。dpi=200 渲染 → 1498 × 36604 像素，触发 OpenCV warpPerspective 的 `dst.cols < SHRT_MAX` (32767) 断言。中间尝试过 `text_det_limit_side_len=8000` 放宽 PaddleOCR 内置 4000 上限 + 自适应 dpi + 分段 OCR，但**这类长图本身识别价值低**：PaddleOCR 强缩放后行高失真，输出准确率掉到几乎不可用。

**修复**：

`ocr_service.py` 设 `SKIP_LONG_PAGE_PT = 8000`（标准 A4 仅 595 × 842 pt，阈值已经很宽松），超过直接跳过该页 + log 提示：

```python
SKIP_LONG_PAGE_PT = 8000
for idx, page in enumerate(doc, 1):
    max_pt = max(page.rect.width, page.rect.height)
    if max_pt > SKIP_LONG_PAGE_PT:
        skipped += 1
        if log:
            log(f"           ⏭️ 第 {idx}/{page_count} 页物理尺寸过大（最长边 {max_pt:.0f}pt > {SKIP_LONG_PAGE_PT}pt），跳过 OCR")
        continue
    ...
if skipped == page_count and page_count > 0 and log:
    log(f"           ⚠️ {page_count} 页全部因尺寸过大被跳过，OCR 未产出文本")
```

周辉.pdf 实测：单页判定耗时 4.2 秒（仅 pymupdf 打开 + 量尺寸），不进入渲染 → 不触发 OpenCV 断言 → 优雅降级回失败桶。

---

#### D. PaddleOCR 2.x / 3.x 双兼容

PaddleOCR 在 3.x 重写了 API，参数名和返回结构都变了：

| 方面 | 2.x | 3.x |
|---|---|---|
| 方向检测开关 | `use_angle_cls=True` | `use_textline_orientation=True` |
| 日志开关 | `show_log=False` | 已移除（传入会 `ValueError`）|
| 调用方法 | `ocr.ocr(arr, cls=True)` | `ocr.predict(arr)` |
| 返回结构 | 嵌套 list `[[ [box, (text, conf)], ... ]]` | `OCRResult` 对象，`rec_texts` / `rec_scores` / `rec_polys` 键 |
| 长边限制 | 内部默认 4000 | `text_det_limit_side_len=8000` 可放宽 |

`_get_ocr()` 用 4 套 kwargs 逐级 try 兼容：

```python
for kwargs in (
    {"lang": "ch", "use_textline_orientation": True, "text_det_limit_side_len": 8000},  # 3.x 放宽长边
    {"lang": "ch", "use_textline_orientation": True},                                    # 3.x 标准
    {"lang": "ch", "use_angle_cls": True, "show_log": False},                            # 2.x
    {"lang": "ch"},                                                                       # minimal
):
    try:
        _ocr_instance = PaddleOCR(**kwargs)
        return _ocr_instance
    except (TypeError, ValueError):
        continue
```

`_extract_text_from_paddle_result()` 同样两路兼容 —— 优先尝试 `page["rec_texts"]` / `getattr(page, "rec_texts", None)`（3.x），否则 fallback 到 `isinstance(page, list)` 走嵌套 tuple 拆解（2.x）。

---

#### 同批次扩展端 v2.37.0 → v2.38.0（三联升版）

借这次发版顺手做了一轮扩展端日志降噪 + 死代码清理：

- `chrome_extension/content.js` 2.37.0 → 2.38.0：UI 重复 log demote 到 debug（`console.debug` 仅 stderr 不进采集面板）；删除 `isInvalidCandidateNameToken` stub（早期占位函数，从未被调用）。
- `chrome_extension/manifest.json` 同步 bump。
- `recruitment_assistant/services/extension_contract.py::EXPECTED_CONTENT_SCRIPT_VERSION` 同步 `"2.38.0"`，保证 bridge `_check_content_script_version` 启动时不打 mismatch warning。
- `recruitment_assistant/services/boss_ws_bridge.py` v2.03.0 → v2.04.0：UI 日志分级修正（info → UI、debug → stderr only），降低 BOSS 沟通中页面长时间运行时的重复回放噪音。

> 三联升版机制（content.js + manifest + extension_contract）是从早期"Chrome 不重载扩展导致版本不一致 + bridge 不输出 mismatch warning"的事故沉淀下来的硬规则 —— 任一文件改动都必须升对应版本号常量。

---

### 验证

- 任珮瑜.docx → 重跑 07 简历管理扫描，从"空白/损坏"桶 → "AI 解析成功"。
- 文国斌.pdf → 第一次 OCR 70 秒（含 PaddleOCR 模型加载 + 单页识别）→ 0.95 秒命中 `.ocr.txt` 缓存。
- 周辉.pdf → 单页 4.2 秒判定为超长跳过，输出 `⏭️ 第 1/1 页物理尺寸过大（最长边 13177pt > 8000pt），跳过 OCR` + `⚠️ 1 页全部因尺寸过大被跳过`，降级回失败桶不污染。
- 三平台 bridge 启动均无 content script version mismatch warning。

### 设计取舍

- **OCR 做成可选模块（`pip install ".[ocr]"`）而不是必装**：paddlepaddle 体积 600 MB+，模型再 200 MB+，纯文本简历用户不应被强制下载。`is_paddleocr_available()` 优雅降级 + UI 日志明确提示安装命令。
- **OCR 阈值定 200 字符（不是 50）**：50 字符是 docx 兜底阈值（区分"几乎空"和"有正文"）；OCR 触发阈值要更宽松一些，因为 pypdf 偶尔从图像 PDF 里抠出十几个字符的水印/页眉，仍然属于"识别失败"应进 OCR。200 字符约等于一份正常简历前两行（姓名+联系方式+教育起始）的长度。
- **OCR 缓存写到 PDF 同目录而不是 `data/cache/`**：用户手动管理简历文件时缓存跟随，删除 PDF 时缓存随之消失，不需要额外清理任务。
- **超长图阈值 8000pt（不是按像素）**：物理尺寸是 PaddleOCR 输入前就能拿到的稳定信号，不依赖 dpi；像素阈值会随 dpi 浮动需要复杂换算。8000pt ≈ 2.82 米，已经远超任何合理简历版面。
