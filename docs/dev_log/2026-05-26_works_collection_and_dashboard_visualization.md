# 51 附件作品采集闭环 + OCR 误判修复 + 首页百分比可视化

- 日期：2026-05-26
- 范围：51 前程无忧附件作品下载链 + PDF 损坏判定 + 简历管理日志窗口 + 首页仪表盘 + 三 bridge / 扩展 / 主题预览
- 上游 spec：[docs/superpowers/specs/2026-05-25-qiancheng-attachment-works-design.md](../superpowers/specs/2026-05-25-qiancheng-attachment-works-design.md)（提交 cb26430）

## 一、51 前程无忧"附件作品"采集闭环

### 业务目标

候选人简历下载成功后，进一步识别并下载"附件作品"（设计稿/作品集），与简历同目录、文件名复用既有规则但插入 `（附件作品）` 标识；UI 在简历管理 / 面试管理两处展示作品文件路径与"打开作品"按钮；作品成功 / 跳过都进实时日志（绿色成功 / 黄色警告）；作品流任何失败都不阻断简历主流。

### 扩展（Chrome Extension v2.39.0 → 2.39.3）

- `chrome_extension/content.js`
  - QIANCHENG_SELECTORS 新增：
    - `attachment_works_btn_text: "附件作品"` — `.file-type-text` 文本匹配
    - `attachment_works_download_anchor: 'a.download_a[href^="blob:"]'` — 作品 modal 独立 Vue scope（`data-v-0cc45215`），不复用简历的 `.annex-resume`
  - 新增函数三件套：
    - `findQianchengAttachmentWorksButton()` 在 `.chat-user-operate` 内扫文本"附件作品"
    - `waitForQianchengWorksDownloadAnchor(timeoutMs=6000)` 轮询可见 blob 锚点
    - `tryDownloadQianchengAttachmentWorks(candidateId, signature, info)` 主流：点击按钮 → 等 modal → `.click()`（不能 dispatchEvent，部分 Chromium 对合成 click 不下载 blob）→ `download_intent` 携带 `variant: "attachment_works"`
  - `qianchengCollectLoop` 候选人迭代里在 `resumeSaved` 后注入作品流
  - `resume_persist_ack` handler 接受 `status === "saved" || status === "works_saved"`，避免作品成功后误报"持久化未计入"（v2.39.2 修复）
  - 沟通职位简化 `slice(0, 8) → slice(0, 12)`（v2.39.3，三家统一上限到 12 字符）
- `chrome_extension/manifest.json` version 2.39.0 → 2.39.3
- `recruitment_assistant/services/extension_contract.py` EXPECTED_* 同步到 2.39.3

### Bridge（QIANCHENG_BRIDGE_VERSION 1.20.0 → 1.22.0）

- `recruitment_assistant/services/qiancheng_ws_bridge.py`
  - `_save_resume` 顶部加 variant 路由：`if data["variant"] == "attachment_works": self._save_attachment_works(data); return`
  - 新增 `_save_attachment_works(data)`（约 120 行）：
    - 复用候选人元信息 + `_saved_resume_hash_signatures` 进程级 SHA256 池防交叉污染
    - 文件名：在简历名 `51前程无忧` 与日期之间插 `-（附件作品）-`
    - 落盘：`data/attachments/51job/YYYYMMDD/<新文件名>.pdf`
    - 不写 `BossCandidateRecord`（去重表只追踪简历主流）
    - 成功 emit `_log("success", "附件作品已归档: ...")` + `resume_persist_ack(status="works_saved")`
  - 新增 `qiancheng_attachment_works_skipped` 警告日志路由
- `boss_ws_bridge.py` / `zhilian_ws_bridge.py` 仅 bump 版本号（沟通职位简化跟着改）

### 数据层

- `recruitment_assistant/storage/resume_models.py` ResumeSource 加 `attachment_works_path: Mapped[str | None]`
- `recruitment_assistant/storage/resume_db.py` 新增 `_migrate_add_attachment_works_path()` — 用 `PRAGMA table_info` 检查后 idempotent ALTER，`init_resume_database()` 末尾调用一次
- `recruitment_assistant/schemas/resume_archive.py` ResumeSourceCreate 加 `attachment_works_path: str | None = None`

### UI

- `app/pages/07_简历管理.py`
  - `scan_resume_files()` 跳过文件名含"（附件作品）"的 PDF，避免 AI 解析器把作品当独立简历
  - 新增 `find_attachment_works_path_for(resume_path)`：剥简历文件名末 3 段（日期/时间/序号）后在同目录找前缀+`（附件作品）` 的 sibling PDF
  - AI 解析时 `ResumeSourceCreate(attachment_works_path=find_attachment_works_path_for(path))`
  - 简历管理详情：作品行只一个 📄 打开按钮（无访问目录按钮，与简历区分）
- `app/pages/10_面试管理.py` 候选人摘要面板末尾加作品行（🎨 路径 + 打开作品按钮）

### 关键决策记忆

- 作品 modal 是独立 Vue scope（`data-v-0cc45215`），不复用 `.annex-resume`；首轮测试就栽在直接复用简历 selector
- 原生 `<a download>` 必须用 `.click()` 而非 `dispatchEvent(MouseEvent)`，否则 blob URL 不触发下载
- variant 字段经 `download_intent → background.js → resume_downloaded` 全链路 `{...data}` 透传，不引入新事件类型

## 二、OCR 回退误判修复

### 现象

候选人 PDF "陈丽娟-26岁-硕士-UI设计师-51前程无忧-20260526-001433-004.pdf"（953×1348 pt 单页扫描件，1 张图，0 字符文本）在简历处理流被标"⚠️ 跳过 — 文件空白或损坏"，未进入 OCR 回退分支。

### 根因

`recruitment_assistant/parsers/pdf_resume_parser.py::is_empty_or_corrupted` 旧实现：

```python
if suffix == ".pdf":
    from pypdf import PdfReader
    reader = PdfReader(str(path))   # 此 PDF xref 段不规范，pypdf 抛 PdfReadError
    return len(reader.pages) == 0
...
except Exception:
    return True   # ← 任何异常都被吞成"损坏"
```

pypdf 解析 xref 表时 `Could not read Boolean object`，外层 except 把异常吞为"损坏" → 简历管理:524 输出跳过 → 永远走不到 :531 的 `<200 字符 → OCR 回退`。但 pymupdf 能正常打开同一文件。

### 修复

`is_empty_or_corrupted` 改为 pymupdf 优先 + pypdf 兜底：

```python
try:
    import fitz
    doc = fitz.open(str(path))
    try: return doc.page_count == 0
    finally: doc.close()
except Exception:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return len(reader.pages) == 0
```

实测同一份 PDF 修复后 `is_empty_or_corrupted == False` → `extract_text_from_pdf` 返回 0 字符 → 触发 `<200` 字符 OCR 回退分支 → 单页 953×1348 pt 远低于 OCR 服务的 8000 pt 跳过阈值，可正常 PaddleOCR。

### 关于 `extract_text_from_pdf`

它早就走 `_extract_pdf_pages_best_effort`（pymupdf 优先 + pypdf 兜底）所以扫描件文本提取本就为空，问题只在 corrupt 探测的入口判定。

## 三、简历管理日志窗口改造（浅色 + 始终可见滚动条 + 自动贴底）

### 问题

旧实现用 `st.markdown(unsafe_allow_html=True)` 注入 `<script>` 控制 `scrollTop`，但 markdown 安全过滤会剥掉 `<script>` 标签，自动贴底脚本根本没在跑，只是"看着像"。`overflow-y: auto` 导致内容达阈值才出滚动条，宽度抖动。

### 修复

`app/pages/07_简历管理.py::render_log_window`：

- `st.markdown` → `st.components.v1.html`：脚本在真实 iframe 内执行
- `overflow-y: auto → scroll` + `scrollbar-gutter: stable`：始终保留滚动条占位
- `setTimeout` → `MutationObserver`：监听 DOM 变化，每次 Streamlit 增量重渲染追加新行立即贴底
- 配色：iframe 与父页面 CSS 变量不互通；之前 fallback 用了 dark 色导致黑底，改回浅色十六进制（`#ffffff` 背景 / `#262730` 文字 / `#e6eaf1` 边框 / `#c0392b` 错误行 / `#f0f2f6` 滚动条轨道 / `#c8ccd4` 滑块）与 Streamlit 浅色主题一致

## 四、首页仪表盘百分比可视化

### 改动

- 简历库行：`简历库总数 → 简历库信息`，副标题改为"自动采集结果，按已入库简历来源平台汇总统计。"
  - 智联 / BOSS / 前程 三格改成环形图（占简历库总数百分比），em 注释带原始数量
  - 入库率改成水波球（水位高度跟着百分比走，水波两条相位错开）
- 面试行：`面试总数 → 面试/待邀信息`，副标题改为"面试邀约池与各轮面试数量统计，数字化管理面试全流程。"
  - 待邀 / 一面 / 二面 / 三面四格改成环形图（占面试/待邀总数百分比）
  - 分母不再用 `InterviewInvitation` 邀约表行数，改为 `pending + first + second + third` 求和（与各轮 cell 实际数据来源一致，避免显示百分比之和不等于 100% 的违和感）
- 数字"今日采集" / "招聘岗位数"用 `--color-text-secondary`（深灰）做次要数字 variant
- 6 格 cell 全改成 `display:flex; flex-direction:column; align-items:center; justify-content:center` + `min-height:140px`，所有 cell 内容上下左右居中
- 主指标数字 `font-size: 24px → 34px`

### 配色规则（全部使用主题 CSS 变量，可随主题切换）

平台环形图（三家差异化）：

| 平台 | variant | 主色 |
|---|---|---|
| 智联 | `home-ring-primary` | `--color-primary` |
| BOSS | `home-ring-secondary` | `--color-secondary` |
| 前程 | `home-ring-accent` | `--color-accent` |

面试阶段环形图（递进加深，靠 `color-mix` 在 `--color-success` 与 `--color-primary` 间插值，最深一档再混 `--color-text`）：

| 阶段 | variant | 公式 |
|---|---|---|
| 待邀 | `home-ring-step-0` | 78% success + 22% primary |
| 一面 | `home-ring-step-1` | 38% success + 62% primary |
| 二面 | `home-ring-step-2` | 100% primary |
| 三面 | `home-ring-step-3` | 70% primary + 30% text |

环形图轨道色：`color-mix(in srgb, var(--ring-color) 18%, var(--color-bg-soft))` 实时按主色生成低饱和底，不依赖 `*-soft` 变体。

注：`color-mix` 需要 Chrome 111+ / Edge 111+ / Safari 16.2+。

### 数学

- `_RING_PERIMETER = 150.796`（2π × 24）+ `stroke-dasharray/dashoffset` 控制弧长，fg circle `transform: rotate(-90deg)` 让起点在顶部
- 水波 svg `clipPath` 切圆球（圆心 30,30 / r=24 / 顶 y=6 / 底 y=54），水位 `water_y = 6 + (1 − pct/100) × 48`

### 主题预览页（平台登录页内）

`app/components/theme_preview.py` 在"任务进度"卡下新增"百分比可视化控件"区，并排展示 4 种 widget 示例：半圆仪表盘 / 环形进度 / 子弹图 / 水波球。全部用 `--color-primary*` 系列变量，切主题时颜色跟随。

## 五、版本号

| 模块 | 旧 | 新 |
|---|---|---|
| 页面 APP_VERSION | V3.01 | V3.02 |
| chrome_extension/content.js | 2.38.0 | 2.39.3 |
| chrome_extension/manifest.json | 2.38.0 | 2.39.3 |
| extension_contract.EXPECTED_* | 2.38.0 | 2.39.3 |
| BOSS_BRIDGE_VERSION | 2.04.0 | 2.05.0 |
| QIANCHENG_BRIDGE_VERSION | 1.20.0 | 1.22.0 |
| ZHILIAN_BRIDGE_VERSION | 1.30.0 | 1.31.0 |

## 六、文件改动统计

```
14 files changed, ~550 insertions, ~62 deletions
```

主要文件：
- chrome_extension/content.js（+123 / -？）
- recruitment_assistant/services/qiancheng_ws_bridge.py（+130 / -？）
- app/main.py（+129 / -？）
- app/pages/07_简历管理.py（+95 / -？）
- app/components/theme_preview.py（+70 / -？）
- recruitment_assistant/parsers/pdf_resume_parser.py（+23 / -？）

## 七、记忆系统更新（this session 涉及）

- `qiancheng_chat_dom_selectors.md` 追加：
  - 附件作品下载锚点 `a.download_a[href^="blob:"]`（独立 Vue scope `data-v-0cc45215`）
  - 触发要求：原生 `<a download>` 用 `.click()`，不能用 dispatchEvent
  - 关闭：先试 `.annex-resume .container-close`，失败发 Escape keydown 兜底

## 八、待办 / 已知遗留

- 旧"持久化未计入"日志在 v2.39.2 已修，但用户需手动 reload Chrome 扩展才生效（manifest 改了 chrome 才会重载）
- OCR 修复后陈丽娟那份 PDF 应能进入 PaddleOCR 流；首次模型下载约 200MB+，预热慢属正常（参考记忆 `paddleocr_windows_setup`）
