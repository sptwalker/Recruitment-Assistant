# 51 前程无忧「附件作品」采集 — 设计文档

- 起草日期：2026-05-25
- 平台：51 前程无忧（ehire.51job.com）
- 目标：在现有简历采集成功之后，**额外**捕获并归档候选人的「附件作品」PDF，并在简历管理 / 面试管理两个页面以独立链接展示。

## 1. 背景与现状

现有 51 采集管线已经稳定运行：候选人卡片 → 点击「附件简历」按钮 → 弹出 PDF 预览 → 点击下载 → 文件落到 `Downloads/51前程无忧/` → 扩展发 `resume_downloaded` 事件 → bridge 的 `_save_resume()` 完成 hash 去重、归档复制到 `data/attachments/51job/YYYYMMDD/`、写 `ResumeSource` 表、回 `persist_ack=saved`。详见 `recruitment_assistant/services/qiancheng_ws_bridge.py:1255` 和 `chrome_extension/content.js` 中 `QIANCHENG_SELECTORS` 块。

候选人聊天页右上区还可能出现一个「附件作品」按钮，与「附件简历」共用 `.file-type-text` class、文本不同：

```html
<span data-v-182e24c0="" class="file-type-text">附件作品</span>
```

经确认，点击它弹出的页面与附件简历完全一致——是 PDF 预览，下载链路可直接复用。

## 2. 设计目标

1. **加分项**：附件作品下载失败不阻断简历主流程，候选人整体仍算成功入库。
2. **零冗余 selector 学习**：直接复用「附件简历」的 `.file-type-text` 强选择器 + 文本匹配（`attachment_works_btn_text="附件作品"`），不走 localStorage learning。遵循 user memory `feedback_codify_extension_learning`。
3. **可见性**：简历管理与面试管理两个 UI 页面都要能看到作品文件链接并能一键打开。
4. **审计可追**：作品下载成功在采集页实时日志里输出绿色「附件作品已归档」一行。

## 3. 流程

```
现有流程  →  点附件简历  →  PDF 预览  →  下载  →  persist_ack=saved
                                                      │
                                                      ▼
新增分支            探测 .file-type-text 文本="附件作品"
                                                      │
                                               ┌──不存在──→ 静默跳过（仅 debug 日志）
                                               │
                                               └──存在──→ 点击 → 等 PDF 预览 → 复用现有下载逻辑
                                                                  │
                                                                  ├─任一步失败 → warning 日志，作品跳过
                                                                  │
                                                                  └─成功 → emit resume_attachment_works_downloaded
                                                                                │
                                                                                ▼
                                                                _save_attachment_works(data)
                                                                  → 复用文件名 builder（多塞一段「-（附件作品）」）
                                                                  → 写 ResumeSource.attachment_works_path
                                                                  → emit persist_ack status=works_saved
                                                                  → 日志（success 绿色）：「附件作品已归档：…」
```

## 4. 三处分叉点

| 维度 | 简历 | 作品 |
|---|---|---|
| 扩展事件类型 | `resume_downloaded` | `resume_attachment_works_downloaded`（新增） |
| Bridge 处理函数 | `_save_resume` | `_save_attachment_works`（新增；与简历共享 helper） |
| 文件名 | `张三-28-本科-产品经理-51前程无忧-20260525-143022-001.pdf` | `张三-28-本科-产品经理-51前程无忧-（附件作品）-20260525-143022-001.pdf` |
| DB 字段 | `ResumeSource.file_path` | `ResumeSource.attachment_works_path`（新增 nullable） |
| 候选人级失败行为 | 阻断 | 不阻断 |
| selector | `.file-type-text` 文本=`附件简历` | `.file-type-text` 文本=`附件作品` |
| Persist ACK status | `saved` / `duplicate_skipped` / 等 | `works_saved` / `works_duplicate_skipped` / `works_skipped` |
| Dedup | candidate_key + sha256 | candidate_key + `::works` 后缀 + 同一 hash 池 |
| 实时日志色 | 绿色（success） | 绿色（success），关键词「附件作品已归档」 |

## 5. 实现切入点

### 5.1 Chrome 扩展（chrome_extension/content.js）

- **新增常量**：`QIANCHENG_SELECTORS.attachment_works_btn_text = "附件作品"`
- **新增函数**：`findQianchengAttachmentWorksBtn()` — 镜像 `findQianchengAttachmentBtn()`，scope 同 `.chat-user-operate`，文本判定换为 `attachment_works_btn_text`。
- **新增主流程**：`tryDownloadAttachmentWorks(candidate)`
  - 探测按钮存在性；不存在直接 return（debug 日志）
  - 点击 → 等 PDF iframe/preview ready
  - 复用 `findQianchengDownloadBtn()` 链路触发下载
  - 监听 chrome downloads → 拿到 download_path
  - emit `resume_attachment_works_downloaded` 事件，data 复用当前候选人的 `candidate_signature` / `candidate_info`，附 `download_path` / `filename`
  - 等 persist_ack（status `works_saved` / 失败均不阻断）
  - 关闭作品预览（沿用现有 close_preview selector）
- **注入位置**：在现有候选人循环里，简历 `persist_ack=saved` 之后、`close_preview` **之后**调用 `tryDownloadAttachmentWorks`。即：简历预览先关闭，再独立点击作品按钮、单独打开作品预览、单独关闭。这样两个 PDF 预览的状态机互不耦合，作品分支失败也不会卡住简历的关闭。
- **失败信号**：emit `qiancheng_attachment_works_skipped` 带 reason，bridge 转 warning 日志，**不抛错**。
- **版本号 bump**（按 user memory `feedback_bump_version_on_module_change`）：
  - `CONTENT_SCRIPT_VERSION` +1
  - `manifest.json` version +1
  - bridge 端 `EXPECTED_*` 同步

### 5.2 Bridge（recruitment_assistant/services/qiancheng_ws_bridge.py）

- **抽公共 helper**（重构现有 `_save_resume`，避免新函数复制 100+ 行）：
  - `_build_qiancheng_resume_filename(candidate_info, ext, *, variant: str | None = None)` — `variant=None` 走原格式，`variant="附件作品"` 在 `-51前程无忧-` 之后插入 `-（附件作品）`。
  - `_archive_download_file(src_download_path, target_filename)` — 把现有的 copy + delete + 错误吞掉抽出。
  - `_save_resume()` 改为调用上面两个 helper，**外部行为零变化**。
- **新增** `_save_attachment_works(data)`：
  - dedup key：`candidate_key + "::works"`
  - hash dedup：复用全局 `_saved_resume_hash_signatures`（防作品/简历/跨候选人串档）
  - 文件名 variant=`"附件作品"`
  - 持久化：`ResumeSource.attachment_works_path = abs_path`（同候选人记录 update，而不是 insert 新行）
  - 事件回包：`resume_persist_ack { status: "works_saved" | "works_duplicate_skipped" | "works_archive_missing" | "works_hash_mismatch_blocked" }`
  - 日志：success 级别，模板 `附件作品已归档：{filename}`（含「成功/已归档」关键词，前端自动绿色）
  - runtime_state：可选追加 `attachment_works_count` 字段，前端汇总展示（首版可不做）
- **事件路由**：`_handle_event("resume_attachment_works_downloaded")` → `_save_attachment_works(data)`；`_handle_event("qiancheng_attachment_works_skipped")` → warning 日志。

### 5.3 Schema（recruitment_assistant/storage/resume_models.py + storage/resume_db.py）

- **模型层**：`ResumeSource` 加 `attachment_works_path: Mapped[str | None] = mapped_column(String, nullable=True)`。
- **迁移**：项目用 `Base.metadata.create_all()` 动态建表，新加 nullable 列对老表**不会自动 ALTER**。在 `init_resume_database()` 末尾追加幂等迁移：
  ```python
  with engine.begin() as conn:
      cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(resume_source)").fetchall()}
      if "attachment_works_path" not in cols:
          conn.exec_driver_sql("ALTER TABLE resume_source ADD COLUMN attachment_works_path TEXT")
  ```
  实现时先 read `storage/resume_db.py` 确认确切 init 方式，必要时调整。

### 5.4 UI

#### 07_简历管理.py（候选人详情卡 ~lines 836-860 区段）

紧跟现有简历文件块之后追加作品块：

```
📎 简历文件本地地址：  C:\...\张三-28-本科-...-20260525-001.pdf
                      [📄 打开] [📁 访问目录]   ← 现状不变

🎨 附件作品文件地址：  C:\...\张三-28-本科-...-（附件作品）-20260525-001.pdf
                      [📄 打开]               ← 新增，仅一个按钮
```

仅当 `rs.attachment_works_path` 非空且文件存在时显示。文件不存在时按钮 disabled，但路径仍展示。

#### 10_面试管理.py（简历摘要面板 ~lines 460-462 末尾）

镜像同样的"图标 + 路径 + 打开按钮"行——作品块跟在简历块下方，只一个「打开」按钮。

## 6. 文件落盘

`data/attachments/51job/YYYYMMDD/`，与简历同目录，靠文件名里 `（附件作品）` 区分。这样浏览归档目录时能直观看到一个候选人的两个文件并排。

## 7. 测试要点

| 场景 | 预期 |
|---|---|
| 候选人无附件作品按钮 | 简历正常归档，无作品记录，无错误日志 |
| 候选人有作品按钮且下载成功 | 简历 + 作品两个文件归档；DB 同行的 `file_path` 与 `attachment_works_path` 都填；UI 两个块都展示；实时日志里两条绿色「成功/已归档」 |
| 作品按钮存在但点击后预览超时 | 简历仍归档；作品 warning 日志「附件作品下载失败：reason=...」；DB `attachment_works_path` 留 NULL |
| 作品 hash 与已归档简历碰撞 | 作品被拒，记 `works_hash_mismatch_blocked`；简历记录不受影响 |
| 同候选人重复采集 | 简历走现有 dedup；作品按 `candidate_key::works` 二级 dedup，已有作品则 skip |
| Schema 迁移 | 旧库启动后自动加列；二次启动不重复加 |

## 8. 不做（YAGNI）

- 不为多个作品文件预留 1:N 表结构（业务上每候选人最多 1 件作品）
- 不实现作品 OCR / 内容解析
- 不做候选人侧的"重新下载作品"按钮（首版）
- 不在 runtime_state 加单独的作品计数面板（首版口头日志即可）

## 9. 受影响的文件清单

- `chrome_extension/content.js` — 新增 selector + 函数 + 候选人循环注入；CONTENT_SCRIPT_VERSION bump
- `chrome_extension/manifest.json` — version bump
- `recruitment_assistant/services/qiancheng_ws_bridge.py` — helper 抽取 + `_save_attachment_works` + 路由 + 版本期望同步
- `recruitment_assistant/storage/resume_models.py` — `ResumeSource.attachment_works_path` 字段
- `recruitment_assistant/storage/resume_db.py` — 幂等列迁移
- `app/pages/07_简历管理.py` — 作品块渲染（路径 + 单按钮）
- `app/pages/10_面试管理.py` — 作品块渲染（路径 + 单按钮）
- `app/pages/09_51前程无忧采集.py` — 实时日志着色规则**不需要改**（关键词已命中绿色）
