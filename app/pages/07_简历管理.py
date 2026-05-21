"""简历分析管理模块 — 3 Tab 页面。

Tab 1: 简历自动解析入库（扫描 → 提取文本 → AI 结构化 → 去重 → 入库）
Tab 2: 简历库浏览（搜索 / 详情 / 删除 / 屏蔽 / 面试邀约）
Tab 3: 招聘岗位录入/匹配（录入岗位 → AI 匹配候选人）
"""

import importlib
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.config.settings import get_settings
import recruitment_assistant.parsers.pdf_resume_parser as resume_parser_module
from recruitment_assistant.schemas.resume_archive import ResumeSourceCreate
import recruitment_assistant.services.resume_ai_service as resume_ai_service_module
from recruitment_assistant.services.resume_archive_service import ResumeArchiveService
from recruitment_assistant.storage.resume_db import create_resume_session, init_resume_database
from recruitment_assistant.storage.resume_models import JobPosition

resume_parser_module = importlib.reload(resume_parser_module)
extract_doc_text = resume_parser_module.extract_doc_text
extract_text_from_docx = resume_parser_module.extract_text_from_docx
extract_text_from_pdf = resume_parser_module.extract_text_from_pdf
is_empty_or_corrupted = resume_parser_module.is_empty_or_corrupted
has_garbled_text = resume_parser_module.has_garbled_text

resume_ai_service_module = importlib.reload(resume_ai_service_module)
ResumeAIService = resume_ai_service_module.ResumeAIService
normalize_platform = resume_ai_service_module.normalize_platform

init_resume_database()
get_settings.cache_clear()
settings = get_settings()

st.set_page_config(page_title="简历管理", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("简历管理")
page_header("简历分析管理", "自动解析入库、浏览管理、岗位匹配。面试跟进请到「面试管理」。")


@st.cache_resource
def get_ai_service(api_key: str, base_url: str, model: str) -> ResumeAIService:
    return ResumeAIService(
        api_key=api_key,
        base_url=base_url,
        model=model,
    )


ai_service = get_ai_service(settings.ai_api_key, settings.ai_base_url, settings.ai_model)

RESUME_DIRS = {
    "BOSS直聘": settings.attachment_dir / "boss",
    "智联招聘": settings.attachment_dir / "zhilian",
    "51前程无忧": settings.attachment_dir / "51job",
}


def scan_resume_files() -> list[dict]:
    """扫描三个平台目录下所有 PDF/DOCX 简历文件。"""
    files = []
    for platform, base_dir in RESUME_DIRS.items():
        if not base_dir.exists():
            continue
        for f in sorted(base_dir.rglob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.suffix.lower() in (".pdf", ".docx", ".doc") and f.is_file():
                files.append({
                    "path": f,
                    "platform": platform,
                    "name": f.name,
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                })
    return files


def extract_text(path: Path) -> str:
    """根据文件类型提取纯文本。"""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_text_from_pdf(path)
    elif suffix == ".docx":
        return extract_text_from_docx(path)
    elif suffix == ".doc":
        return extract_doc_text(path)
    return ""


def should_skip_empty_or_corrupted(path: Path) -> bool:
    """页面侧最终判定：老 .doc 优先用当前进程内重载后的提取器检测，避免旧缓存误判。"""
    if path.suffix.lower() == ".doc":
        try:
            return len(extract_doc_text(path).strip()) < 50
        except Exception:
            return True
    return is_empty_or_corrupted(path)


def infer_candidate_from_filename(file_name: str) -> tuple[str | None, int | None, str | None]:
    """从标准附件名中提取姓名/年龄/学历，用于 AI 调用前预去重。"""
    stem = Path(file_name).stem
    parts = [part.strip() for part in re.split(r"[-_｜|]", stem) if part.strip()]
    name = parts[0] if parts else None
    age = None
    education_level = None
    for part in parts[1:5]:
        age_match = re.search(r"(\d{1,2})\s*岁", part)
        if age_match:
            age = int(age_match.group(1))
        if part in {"高中", "中专", "大专", "专科", "本科", "硕士", "研究生", "博士"}:
            education_level = "硕士" if part == "研究生" else ("大专" if part == "专科" else part)
    if name and not re.search(r"[\u4e00-\u9fffA-Za-z]", name):
        name = None
    return name, age, education_level


def filter_duplicate_files_before_ai(files: list[dict]) -> tuple[list[dict], list[str]]:
    """在调用 AI 前用文件名里的姓名/年龄/学历先查库去重，减少 AI 流量消耗。"""
    if not files:
        return [], []
    kept: list[dict] = []
    skipped: list[str] = []
    session = create_resume_session()
    try:
        svc = ResumeArchiveService(session)
        for file_info in files:
            name, age, education_level = infer_candidate_from_filename(file_info["name"])
            if name and svc.is_duplicate(name=name, age=age, education_level=education_level):
                skipped.append(f"{name}（{file_info['name']}）")
            else:
                kept.append(file_info)
    finally:
        session.close()
    return kept, skipped


# ==================== 3 Tab 页面 ====================
tabs = st.tabs([
    "📥 简历自动解析入库",
    "📋 简历库浏览",
    "🎯 招聘岗位录入/匹配",
])

# ==================== Tab 1: 简历自动解析入库 ====================
with tabs[0]:
    st.markdown("### 简历自动解析入库")

    if not ai_service.is_configured:
        st.warning("⚠️ AI API Key 未配置。请到「平台登录 → AI模型」标签配置 `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL`，保存后重启 Streamlit。")

    # 数据库当前状态（非任务信息，保留在窗口外）
    session = create_resume_session()
    svc = ResumeArchiveService(session)
    stats = svc.get_stats()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("简历库总数", stats["total"])
    col2.metric("BOSS直聘", stats["platform_counts"].get("BOSS直聘", 0))
    col3.metric("智联招聘", stats["platform_counts"].get("智联招聘", 0))
    col4.metric("51前程无忧", stats["platform_counts"].get("51前程无忧", 0))
    session.close()

    # ---------- 任务状态：用 session_state 跨 rerun 维护 ----------
    # parse_task_state: "idle" / "running" / "stopping"
    # parse_queue:      待处理文件列表（任务开始时快照，运行中不变）
    # parse_index:      当前处理到第几个（每份 rerun 一次后 +1）
    # parse_results:    汇总统计与明细
    if "parse_task_state" not in st.session_state:
        st.session_state.parse_task_state = "idle"
    if "parse_log_lines" not in st.session_state:
        st.session_state.parse_log_lines = []
    if "parse_queue" not in st.session_state:
        st.session_state.parse_queue = []
    if "parse_index" not in st.session_state:
        st.session_state.parse_index = 0
    if "parse_since_date" not in st.session_state:
        st.session_state.parse_since_date = date.today() - timedelta(days=7)
    if "parse_results" not in st.session_state:
        st.session_state.parse_results = {
            "success_count": 0,
            "skip_count": 0,
            "fail_count": 0,
            "success_files": [],
            "skip_files": [],
            "failed_files": {
                "空白/损坏": [],
                "文本过短": [],
                "AI 解析异常": [],
                "AI 返回空结果": [],
                "入库失败": [],
            },
        }

    # 每次 rerun 都重新扫描简历目录（只在 idle 时使用，运行中用 parse_queue 快照）
    all_files = scan_resume_files()

    def append_scan_summary(lines: list[str], files: list[dict]) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        by_platform: dict[str, int] = {}
        for f in files:
            by_platform[f["platform"]] = by_platform.get(f["platform"], 0) + 1
        lines.append(f"[{ts}] 📂 扫描简历目录")
        lines.append(f"  共扫描到 {len(files)} 个简历文件")
        for p in ("BOSS直聘", "智联招聘", "51前程无忧"):
            lines.append(f"    - {p}: {by_platform.get(p, 0)} 份")
        if not files:
            lines.append("  ℹ️ 三个平台目录下暂无简历文件")
        else:
            since_repr = st.session_state.get(
                "parse_since_date", date.today() - timedelta(days=7)
            ).isoformat()
            lines.append(f"  ⏰ 当前日期过滤：≥ {since_repr}（可在按钮行调整）")
            if ai_service.is_configured:
                lines.append("  👉 点击下方「自动解析入库」按钮启动任务")
            else:
                lines.append("  ⚠️ AI 未配置，「开始」按钮已禁用")
        lines.append("")

    # 第一次进入时自动追加扫描统计
    if not st.session_state.parse_log_lines:
        append_scan_summary(st.session_state.parse_log_lines, all_files)

    # ---------- 渲染日志窗口（HTML，文字可选中，无灰化）----------
    import html as _html

    def render_log_window() -> None:
        # 只保留最后 500 行展示，避免 DOM 过大
        recent = st.session_state.parse_log_lines[-500:]
        # 按追加顺序渲染：旧信息在上，新信息继续向下输出
        def _log_row_class(line: str) -> str:
            if any(token in line for token in ("❌", "⚠️", "失败", "跳过", "异常", "损坏", "过短")):
                return "log-row log-row-error"
            return "log-row"

        escaped_rows = "".join(
            f"<div class='{_log_row_class(line)}'>{_html.escape(line) or '&nbsp;'}</div>"
            for line in recent
        )
        html = f"""
        <div id='resume-log-window' class='resume-log-window'>
          {escaped_rows}
        </div>
        <script>
          setTimeout(() => {{
            const logWindow = document.getElementById('resume-log-window');
            if (logWindow) {{
              logWindow.scrollTop = logWindow.scrollHeight;
            }}
          }}, 0);
        </script>
        <style>
          .resume-log-window {{
            height: 460px;
            overflow-y: auto;
            background: var(--color-surface);
            color: var(--color-text);
            font-family: 'Consolas','Monaco','Microsoft YaHei Mono','Microsoft YaHei',monospace;
            font-size: 13px;
            line-height: 1.55;
            padding: 12px 14px;
            border-radius: 6px;
            border: 1px solid var(--color-border);
            white-space: pre-wrap;
            word-break: break-all;
            user-select: text;            /* 允许选中文字 */
          }}
          .resume-log-window .log-row {{
            color: var(--color-text);
          }}
          .resume-log-window .log-row-error {{
            color: var(--color-danger);
            font-weight: 700;
          }}
          .resume-log-window::-webkit-scrollbar {{ width: 10px; }}
          .resume-log-window::-webkit-scrollbar-track {{ background: var(--color-bg-soft); }}
          .resume-log-window::-webkit-scrollbar-thumb {{
            background: var(--color-border-strong, var(--color-border));
            border-radius: 5px;
          }}
          .resume-log-window::-webkit-scrollbar-thumb:hover {{ background: var(--color-text-muted); }}
        </style>
        """
        st.markdown(html, unsafe_allow_html=True)

    render_log_window()

    # 日志窗口与按钮区之间的视觉气口
    st.markdown("<div style='height: 18px'></div>", unsafe_allow_html=True)

    # ---------- 操作按钮区（窗口外不显示任何任务信息）----------
    task_state = st.session_state.parse_task_state
    is_idle = task_state == "idle"
    is_running = task_state == "running"
    is_stopping = task_state == "stopping"

    btn_cols = st.columns([1.4, 1.4, 1.4, 1.4, 3.0])
    # 索引 0=日期过滤 1=开始/停止 2=重新扫描 3=清空 4=进度

    # 在按钮列上方放同高度的占位行，让按钮顶部和 picker 的输入框顶部对齐
    _label_style = "font-size:18px; font-weight:bold; margin-bottom:6px;"
    for _i in (1, 2, 3, 4):
        btn_cols[_i].markdown(
            f"<div style='{_label_style}'>&nbsp;</div>", unsafe_allow_html=True
        )

    with btn_cols[0]:
        st.markdown(
            f"<div style='{_label_style}'>请选择最远整理日期</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            """
            <style>
            div[data-testid="stDateInput"] input[aria-label="请选择最远整理日期"] {
                min-width: 190px !important;
                width: 190px !important;
                font-size: 17px !important;
                font-weight: 700 !important;
                padding: 10px 12px !important;
            }
            div[data-testid="stDateInput"] div[data-baseweb="input"] {
                min-width: 190px !important;
                width: 190px !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        # 放宽 date_input 子列，让日期控件显示更宽
        _picker_sub_cols = st.columns([1.35, 0.65])
        with _picker_sub_cols[0]:
            since = st.date_input(
                "请选择最远整理日期",
                value=st.session_state.parse_since_date,
                max_value=date.today(),
                label_visibility="collapsed",
                key="parse_since_date_picker",
                help="只整理修改时间晚于此日期的简历（含当天）",
                disabled=not is_idle,
            )
        st.session_state.parse_since_date = since

    if is_idle:
        start_btn = btn_cols[1].button(
            "🚀 自动解析入库",
            type="primary",
            disabled=(not ai_service.is_configured) or (not all_files),
            key="parse_start_btn",
        )
        stop_btn = False
    else:
        start_btn = False
        stop_btn = btn_cols[1].button(
            "⏹ 停止解析任务",
            type="secondary",
            disabled=is_stopping,
            key="parse_stop_btn",
        )

    refresh_btn = btn_cols[2].button("🔄 重新扫描", disabled=not is_idle, key="parse_refresh_btn")
    clear_btn = btn_cols[3].button("🧹 清空窗口", disabled=not is_idle, key="parse_clear_btn")

    # 运行中显示进度副文本（也只在窗口外的"状态条"，不输出任务信息）
    if is_running or is_stopping:
        done = st.session_state.parse_index
        total_q = len(st.session_state.parse_queue)
        btn_cols[4].progress(
            done / max(total_q, 1),
            text=f"进度 {done}/{total_q}" + ("（正在停止…）" if is_stopping else ""),
        )

    # ---------- 按钮事件处理 ----------
    if clear_btn:
        st.session_state.parse_log_lines = []
        append_scan_summary(st.session_state.parse_log_lines, all_files)
        st.rerun()

    if refresh_btn:
        append_scan_summary(st.session_state.parse_log_lines, all_files)
        st.rerun()

    if start_btn:
        # 任务开始：按日期过滤文件列表 + 初始化统计
        st.session_state.parse_task_state = "running"
        since_dt = datetime.combine(st.session_state.parse_since_date, datetime.min.time())
        filtered_files = [
            f for f in all_files
            if datetime.fromtimestamp(f["path"].stat().st_mtime) >= since_dt
        ]
        filtered_files, pre_skip_files = filter_duplicate_files_before_ai(filtered_files)
        st.session_state.parse_queue = filtered_files
        st.session_state.parse_index = 0
        st.session_state.parse_results = {
            "success_count": 0,
            "skip_count": len(pre_skip_files),
            "fail_count": 0,
            "success_files": [],
            "skip_files": pre_skip_files,
            "failed_files": {
                "空白/损坏": [],
                "文本过短": [],
                "AI 解析异常": [],
                "AI 返回空结果": [],
                "入库失败": [],
            },
        }
        ts = datetime.now().strftime("%H:%M:%S")
        st.session_state.parse_log_lines.append("─" * 60)
        st.session_state.parse_log_lines.append(f"[{ts}] 🚀 解析任务开始")
        st.session_state.parse_log_lines.append(f"  日期过滤：≥ {st.session_state.parse_since_date.isoformat()}")
        st.session_state.parse_log_lines.append(f"  AI 调用前预去重跳过：{len(pre_skip_files)} 份")
        st.session_state.parse_log_lines.append(f"  待 AI 处理简历：{len(filtered_files)} 份（原始扫描 {len(all_files)} 份）")
        st.session_state.parse_log_lines.append("─" * 60)
        st.rerun()

    if stop_btn:
        st.session_state.parse_task_state = "stopping"
        ts = datetime.now().strftime("%H:%M:%S")
        st.session_state.parse_log_lines.append(f"[{ts}] ⏹ 收到停止指令，正在收尾…")
        st.rerun()

    # ---------- 任务执行主循环（每次 rerun 处理 1 份，然后再 rerun）----------
    if is_running or is_stopping:
        queue = st.session_state.parse_queue
        idx = st.session_state.parse_index
        results = st.session_state.parse_results

        def log(line: str) -> None:
            st.session_state.parse_log_lines.append(line)

        # 停止 or 队列处理完 → 写收尾统计并归位
        if is_stopping or idx >= len(queue):
            end_ts = datetime.now().strftime("%H:%M:%S")
            log("─" * 60)
            log(f"[{end_ts}] 🏁 解析任务{'已停止' if is_stopping else '结束'}")
            log("")
            log("📊 任务统计")
            log(f"  • 计划处理：{len(queue)} 份")
            log(f"  • 实际处理：{idx} 份")
            log(f"  • ✅ 成功入库：{results['success_count']} 份")
            log(f"  • 🔄 重复跳过：{results['skip_count']} 份")
            log(f"  • ❌ 解析失败：{results['fail_count']} 份")
            if is_stopping:
                log(f"  • ⏹ 未处理（被停止）：{len(queue) - idx} 份")

            if results["fail_count"] > 0:
                log("")
                log("❌ 失败明细（按原因分组）")
                for reason, items in results["failed_files"].items():
                    if not items:
                        continue
                    log(f"  【{reason}】{len(items)} 份")
                    for item in items:
                        log(f"    - {item}")

            if results["skip_count"] > 0:
                log("")
                log("🔄 重复跳过明细")
                for item in results["skip_files"]:
                    log(f"  - {item}")

            if results["success_count"] > 0:
                log("")
                log("✅ 成功入库明细")
                for item in results["success_files"]:
                    log(f"  - {item}")

            log("")
            log("💡 顶部 4 个 metric 数字将在下次刷新页面（F5 或切 tab）后更新。")

            # 归位
            st.session_state.parse_task_state = "idle"
            st.session_state.parse_queue = []
            st.session_state.parse_index = 0
            st.rerun()

        # 正常处理 1 份
        file_info = queue[idx]
        path = file_info["path"]
        platform = file_info["platform"]
        fname = file_info["name"]

        log(f"[{idx+1}/{len(queue)}] 🔍 处理：{fname}（{platform}）")

        # 1) 空白/损坏检测
        if should_skip_empty_or_corrupted(path):
            log("           ⚠️ 跳过 — 文件空白或损坏")
            results["failed_files"]["空白/损坏"].append(fname)
            results["fail_count"] += 1
        else:
            # 2) 文本提取
            raw_text = extract_text(path)
            if len(raw_text.strip()) < 50:
                log(f"           ⚠️ 跳过 — 提取文本过短（{len(raw_text)} 字符）")
                results["failed_files"]["文本过短"].append(fname)
                results["fail_count"] += 1
            else:
                if has_garbled_text(raw_text):
                    log("           ⚠️ PDF 含解析乱码（嵌入子集字体缺 ToUnicode 映射），邮箱/手机/年龄等数字字段可能识别失败，请人工补录。")
                log(f"           📄 文本提取成功（{len(raw_text)} 字符），调用 AI 结构化…")
                # 3) AI 结构化
                candidate_data = None
                ai_failed = False
                try:
                    candidate_data = ai_service.parse_resume_text(raw_text, source_name=fname)
                except Exception as exc:
                    # pydantic ValidationError 是多行的，首行只是计数（"1 validation error for ..."）,
                    # 后续行才有 字段路径 / 实际值 / 错误类型，必须一并打到日志里才能定位 bug
                    err_lines = str(exc).splitlines() or [repr(exc)]
                    log(f"           ❌ AI 解析异常：{err_lines[0]}")
                    for sub in err_lines[1:8]:
                        log(f"              {sub}")
                    results["failed_files"]["AI 解析异常"].append(f"{fname}（{err_lines[0]}）")
                    results["fail_count"] += 1
                    ai_failed = True

                if not ai_failed and not candidate_data:
                    log("           ⚠️ AI 返回空结果")
                    results["failed_files"]["AI 返回空结果"].append(fname)
                    results["fail_count"] += 1
                elif candidate_data:
                    fname_name, fname_age, fname_edu = infer_candidate_from_filename(fname)
                    filled = []
                    if not candidate_data.age and fname_age:
                        candidate_data.age = fname_age
                        filled.append(f"年龄={fname_age}")
                    if not candidate_data.education_level and fname_edu:
                        candidate_data.education_level = fname_edu
                        filled.append(f"学历={fname_edu}")
                    if (not candidate_data.name or candidate_data.name in ("未知", "未识别")) and fname_name:
                        candidate_data.name = fname_name
                        filled.append(f"姓名={fname_name}")
                    if filled:
                        log(f"           🪪 从文件名补全字段：{' / '.join(filled)}")
                    log(
                        f"           🤖 AI 识别 — 姓名:{candidate_data.name} "
                        f"电话:{candidate_data.phone or '-'} "
                        f"学历:{candidate_data.education_level or '-'} "
                        f"城市:{candidate_data.current_city or '-'}"
                    )
                    # 4)+5)+6) 去重 → 补充来源 → 入库
                    session = create_resume_session()
                    try:
                        svc = ResumeArchiveService(session)
                        if svc.is_duplicate(
                            phone=candidate_data.phone,
                            name=candidate_data.name,
                            age=candidate_data.age,
                            education_level=candidate_data.education_level,
                        ):
                            log("           🔄 去重跳过 — 候选人已存在于数据库")
                            results["skip_files"].append(f"{candidate_data.name}（{fname}）")
                            results["skip_count"] += 1
                        else:
                            candidate_data.resume_source = ResumeSourceCreate(
                                source_platform=normalize_platform(platform),
                                file_name=fname,
                                file_type=path.suffix.lstrip(".").upper(),
                                file_path=str(path),
                                crawl_time=datetime.now(),
                            )
                            try:
                                created = svc.create_candidate(candidate_data)
                                log(f"           ✅ 入库成功 — candidate_id={created.candidate_id}")
                                results["success_files"].append(
                                    f"{candidate_data.name}（{platform} - {fname}）"
                                )
                                results["success_count"] += 1
                            except Exception as exc:
                                log(f"           ❌ 入库失败：{exc}")
                                results["failed_files"]["入库失败"].append(f"{fname}（{exc}）")
                                results["fail_count"] += 1
                    finally:
                        session.close()

        # 处理完一份，索引 +1，触发下一轮 rerun
        st.session_state.parse_index = idx + 1
        st.rerun()


# ==================== Tab 2: 简历库浏览 ====================
def _fmt_date(d) -> str:
    """date 或 datetime 对象 → YYYY-MM-DD；None → ''。"""
    if not d:
        return ""
    try:
        return d.strftime("%Y-%m-%d")
    except Exception:
        return str(d)


@st.dialog("发起面试邀约")
def _open_invite_dialog():
    """弹窗：选岗位（可不选）→ 检查去重 → 写库。

    用 session_state['invite_dialog_cid'] 在浏览页 → dialog 之间传候选人 ID。
    """
    cid = st.session_state.get("invite_dialog_cid")
    if not cid:
        st.error("未选中候选人")
        return
    session = create_resume_session()
    svc = ResumeArchiveService(session)
    try:
        cand = svc.get_candidate(cid)
        if not cand:
            st.error("候选人不存在")
            return

        st.markdown(
            f"**候选人：** {cand.name}（"
            f"{cand.age or '?'}岁 · {cand.education_level or '-'}"
            f"）"
        )

        # 去重检测：同一候选人同时只能有 1 条 pending
        already_pending = svc.has_pending_invitation(cid)
        if already_pending:
            st.warning("⚠️ 该候选人已有进行中的邀约，请先到「面试管理」页面取消或完成。")

        # 岗位下拉（可不选）
        positions = svc.list_positions(status="open")
        pos_options = ["（不指定岗位）"] + [
            f"{p.position_id} | {p.title}（{p.department or '-'} · {p.work_city or '-'}）"
            for p in positions
        ]
        pick = st.selectbox(
            "拟招聘岗位（可选）",
            pos_options,
            key=f"invite_pos_pick_{cid}",
        )
        notes = st.text_area(
            "备注（可选）",
            key=f"invite_notes_{cid}",
            height=80,
        )

        confirm_col, cancel_col = st.columns(2)
        if confirm_col.button(
            "✅ 确认邀约",
            type="primary",
            disabled=already_pending,
            use_container_width=True,
            key=f"invite_confirm_{cid}",
        ):
            position_id = None
            if pick != "（不指定岗位）":
                position_id = int(pick.split(" | ", 1)[0])
            svc.create_invitation(candidate_id=cid, position_id=position_id, notes=notes)
            st.session_state.pop("invite_dialog_cid", None)
            st.rerun()
        if cancel_col.button(
            "取消",
            use_container_width=True,
            key=f"invite_cancel_{cid}",
        ):
            st.session_state.pop("invite_dialog_cid", None)
            st.rerun()
    finally:
        session.close()


def _render_candidate_detail(c, svc, session) -> None:
    """右侧详情区：把候选人的所有字段按段落渲染。"""
    head_cols = st.columns([4, 1, 1, 1])
    head_cols[0].markdown(f"## {c.name}")
    # ⭐ 关注复选框（实时落库）
    new_fav = head_cols[1].checkbox(
        "⭐ 关注",
        value=bool(c.is_favorite),
        key=f"browse_fav_{c.candidate_id}",
    )
    if int(new_fav) != int(c.is_favorite or 0):
        svc.update_candidate_field(c.candidate_id, is_favorite=int(new_fav))
        st.rerun()
    if head_cols[2].button("📧 面试邀约", key=f"browse_invite_{c.candidate_id}"):
        st.session_state["invite_dialog_cid"] = c.candidate_id
        _open_invite_dialog()
    if head_cols[3].button("🗑️ 删除", key=f"browse_del_{c.candidate_id}"):
        svc.delete_candidate(c.candidate_id)
        st.session_state.pop("browse_selected_cid", None)
        session.close()
        st.rerun()

    # 基本信息
    st.markdown("##### 基本信息")
    info_items = [
        ("性别", c.gender),
        ("年龄", f"{c.age}岁" if c.age else None),
        ("生日", _fmt_date(c.birth_date) or None),
        ("学历", c.education_level),
        ("现居城市", c.current_city),
        ("手机", c.phone),
        ("邮箱", c.email),
        ("微信", c.wechat),
    ]
    info_lines = [f"- **{k}**：{v}" for k, v in info_items if v]
    st.markdown("\n".join(info_lines) if info_lines else "_（无）_")

    # 教育经历
    st.markdown("##### 教育经历")
    if c.educations:
        for edu in c.educations:
            head = f"- **{edu.school_name}**"
            if edu.start_date or edu.end_date:
                head += f"（{_fmt_date(edu.start_date) or '?'} — {_fmt_date(edu.end_date) or '至今'}）"
            st.markdown(head)
            subs = [edu.education_level, edu.degree, edu.major,
                    "全日制" if edu.is_full_time else "非全日制"]
            subs = [s for s in subs if s]
            if subs:
                st.markdown(f"  - {' · '.join(subs)}")
    else:
        st.markdown("_（无）_")

    # 工作经历
    st.markdown("##### 工作经历")
    if c.work_experiences:
        for w in c.work_experiences:
            head = f"- **{w.company_name}**"
            if w.start_date or w.end_date:
                head += f"（{_fmt_date(w.start_date) or '?'} — {_fmt_date(w.end_date) or '至今'}）"
            st.markdown(head)
            subs = [s for s in (w.position, w.industry) if s]
            if subs:
                st.markdown(f"  - {' · '.join(subs)}")
            if w.job_content:
                st.markdown(f"  - 工作内容：{w.job_content}")
    else:
        st.markdown("_（无）_")

    # 项目经历
    st.markdown("##### 项目经历")
    if c.project_experiences:
        for p in c.project_experiences:
            head = f"- **{p.project_name}**"
            if p.project_date:
                head += f"（{p.project_date}）"
            st.markdown(head)
            if p.project_role:
                st.markdown(f"  - 角色：{p.project_role}")
            if p.project_desc:
                st.markdown(f"  - 项目描述：{p.project_desc}")
            if p.project_duty:
                st.markdown(f"  - 我的职责：{p.project_duty}")
            if p.project_result:
                st.markdown(f"  - 项目成果：{p.project_result}")
    else:
        st.markdown("_（无）_")

    # 技能 / 证书（按 skill_type 分组）
    st.markdown("##### 技能 / 证书")
    if c.skills:
        by_type: dict[str, list] = defaultdict(list)
        for s in c.skills:
            by_type[s.skill_type or "其他"].append(s)
        for stype, items in by_type.items():
            parts = []
            for s in items:
                name = s.skill_name or ""
                if s.proficiency:
                    name += f"（{s.proficiency}）"
                parts.append(name)
            st.markdown(f"- **{stype}**：{'、'.join(parts)}")
    else:
        st.markdown("_（无）_")

    # 求职意向
    st.markdown("##### 求职意向")
    if c.job_intention:
        ji = c.job_intention
        ji_items = [
            ("目标岗位", ji.target_position),
            ("期望城市", ji.target_city),
            ("期望薪资", ji.expected_salary),
            ("求职状态", ji.job_status),
        ]
        ji_lines = [f"- **{k}**：{v}" for k, v in ji_items if v]
        st.markdown("\n".join(ji_lines) if ji_lines else "_（无）_")
    else:
        st.markdown("_（无）_")

    # 荣誉
    st.markdown("##### 荣誉")
    if c.honors:
        for h in c.honors:
            line = f"- **{h.honor_name}**"
            extras = [x for x in (h.honor_level, _fmt_date(h.honor_date)) if x]
            if extras:
                line += f"（{' · '.join(extras)}）"
            st.markdown(line)
    else:
        st.markdown("_（无）_")

    # 自我评价（完整展示，不截断）
    st.markdown("##### 自我评价")
    st.markdown(c.self_intro if c.self_intro else "_（无）_")

    # 简历来源（含本地文件地址）
    st.markdown("##### 简历来源")
    if c.resume_source:
        rs = c.resume_source
        src_lines = []
        if rs.source_platform:
            src_lines.append(f"- **来源平台**：{rs.source_platform}")
        if rs.file_name:
            src_lines.append(f"- **原始文件名**：{rs.file_name}")
        if rs.file_type:
            src_lines.append(f"- **文件类型**：{rs.file_type}")
        if rs.crawl_time:
            src_lines.append(f"- **入库时间**：{rs.crawl_time.strftime('%Y-%m-%d %H:%M:%S')}")
        st.markdown("\n".join(src_lines) if src_lines else "_（无）_")
        if rs.file_path:
            st.markdown("**📎 简历文件本地地址：**")
            st.code(rs.file_path, language=None)
            fp = Path(rs.file_path)
            file_exists = fp.exists()
            dir_exists = fp.parent.exists()
            op_cols = st.columns([1, 1, 6])
            if op_cols[0].button("📄 打开", key=f"browse_open_file_{c.candidate_id}",
                                  disabled=not file_exists,
                                  help="用默认应用打开简历文件"):
                try:
                    os.startfile(str(fp))
                except OSError as exc:
                    st.error(f"打开失败：{exc}")
            if op_cols[1].button("📁 访问目录", key=f"browse_open_dir_{c.candidate_id}",
                                  disabled=not dir_exists,
                                  help="在资源管理器中打开文件所在目录"):
                try:
                    os.startfile(str(fp.parent))
                except OSError as exc:
                    st.error(f"打开目录失败：{exc}")
            if not file_exists:
                st.warning("⚠️ 文件不存在，可能已被移动或删除")
    else:
        st.markdown("_（无）_")


with tabs[1]:
    st.markdown("### 简历库浏览")

    # 过滤条
    filt_cols = st.columns([1.4, 1.0, 1.0, 1.0, 1.0])
    f_name = filt_cols[0].text_input("姓名", key="browse_name")
    f_city = filt_cols[1].text_input("城市", key="browse_city")
    f_edu = filt_cols[2].selectbox(
        "学历", ["全部", "博士", "硕士", "本科", "大专", "高中", "中专"], key="browse_edu"
    )
    f_platform = filt_cols[3].selectbox(
        "来源", ["全部", "BOSS直聘", "智联招聘", "51前程无忧"], key="browse_platform"
    )
    f_mark = filt_cols[4].selectbox("标记", ["全部", "关注"], key="browse_mark")

    session = create_resume_session()
    svc = ResumeArchiveService(session)
    # 一次拉全部（不分页，左侧列表滚动浏览）。page_size 设大可保证全量返回。
    candidates, total = svc.list_candidates(
        page=1,
        page_size=10000,
        name=f_name or None,
        city=f_city or None,
        education_level=f_edu if f_edu != "全部" else None,
        platform=f_platform if f_platform != "全部" else None,
        favorite_only=(f_mark == "关注"),
    )
    st.caption(f"共 {total} 条候选人")

    if not candidates:
        st.info("暂无候选人数据。请先在「简历自动解析入库」中导入简历。")
        session.close()
    else:
        # session_state 记忆当前选中候选人。过滤后若原选中不在结果集内，回退第一条
        valid_cids = {c.candidate_id for c in candidates}
        if st.session_state.get("browse_selected_cid") not in valid_cids:
            st.session_state.browse_selected_cid = candidates[0].candidate_id

        list_col, detail_col = st.columns([1, 3])

        with list_col:
            st.markdown("**候选人列表**")
            # 列表内按钮基础样式：白底黑字 + 左对齐 + primary 态高亮
            # 注：关注高亮改用 label "⭐ " 前缀（见 for 循环），不再依赖 nth-child CSS
            # —— 早期版本用 nth-child 选择器在 Streamlit 1.56 的 DOM 嵌套层数下命中失败，
            #    且条件性注入 fav CSS 会让整个容器位置漂移
            st.markdown(
                """
                <style>
                div[data-testid="stVerticalBlockBorderWrapper"] button {
                    text-align: left !important;
                    justify-content: flex-start !important;
                    background: var(--color-surface) !important;
                    color: var(--color-text) !important;
                    border: 1px solid var(--color-border) !important;
                    font-weight: normal !important;
                }
                div[data-testid="stVerticalBlockBorderWrapper"] button p {
                    text-align: left !important;
                    color: var(--color-text) !important;
                }
                div[data-testid="stVerticalBlockBorderWrapper"] button:hover {
                    background: var(--color-hover) !important;
                    border-color: var(--color-border-strong, var(--color-border)) !important;
                }
                div[data-testid="stVerticalBlockBorderWrapper"] button[kind="primary"] {
                    background: var(--color-primary-soft) !important;
                    border-color: var(--color-primary) !important;
                    font-weight: 600 !important;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )
            with st.container(height=1400, border=True):
                for c in candidates:
                    is_selected = c.candidate_id == st.session_state.browse_selected_cid
                    prefix = "⭐ " if c.is_favorite else ""
                    label = f"{prefix}{c.name} | {c.age or '?'}岁 | {c.education_level or '-'}"
                    if st.button(
                        label,
                        key=f"browse_pick_{c.candidate_id}",
                        use_container_width=True,
                        type="primary" if is_selected else "secondary",
                    ):
                        st.session_state.browse_selected_cid = c.candidate_id
                        st.rerun()

        with detail_col:
            selected = next(
                (c for c in candidates if c.candidate_id == st.session_state.browse_selected_cid),
                None,
            )
            if selected:
                _render_candidate_detail(selected, svc, session)

        session.close()

# ==================== Tab 3: 招聘岗位录入/匹配 ====================
SALARY_LOW_OPTIONS = ["不限", "3K", "5K", "8K", "10K", "12K", "15K", "20K", "25K", "30K", "40K", "50K"]
SALARY_HIGH_OPTIONS = ["不限", "5K", "8K", "10K", "12K", "15K", "20K", "25K", "30K", "40K", "50K", "80K", "100K"]
EDU_OPTIONS = ["不限", "大专", "本科", "硕士以上"]
EXP_OPTIONS = ["不限", "1-3年", "3-5年", "5-10年", "10年以上"]


def _render_position_form(prefill=None, key_suffix: str = "new"):
    """录入/编辑岗位表单。prefill 为 JobPosition 实例则做编辑预填，否则空表单。
    返回 (clicked, dict) — clicked=True 表示用户点了保存按钮。"""
    title = st.text_input("岗位名称", value=getattr(prefill, "title", "") or "",
                          key=f"posf_title_{key_suffix}")
    dept = st.text_input("部门", value=getattr(prefill, "department", "") or "",
                         key=f"posf_dept_{key_suffix}")
    req = st.text_area("岗位要求", value=getattr(prefill, "requirements", "") or "",
                       height=300, key=f"posf_req_{key_suffix}")

    sal_cols = st.columns([1, 0.2, 1])

    def _safe_index(opts, value, default=0):
        try:
            return opts.index(value)
        except ValueError:
            return default

    cur_low, cur_high = "不限", "不限"
    if prefill and prefill.salary_range and "-" in prefill.salary_range:
        parts = prefill.salary_range.split("-", 1)
        cur_low, cur_high = parts[0].strip(), parts[1].strip()
    sal_low = sal_cols[0].selectbox("薪资下限", SALARY_LOW_OPTIONS,
                                     index=_safe_index(SALARY_LOW_OPTIONS, cur_low),
                                     key=f"posf_sallow_{key_suffix}")
    sal_cols[1].markdown("<div style='text-align:center; padding-top:32px;'>—</div>",
                          unsafe_allow_html=True)
    sal_high = sal_cols[2].selectbox("薪资上限", SALARY_HIGH_OPTIONS,
                                      index=_safe_index(SALARY_HIGH_OPTIONS, cur_high),
                                      key=f"posf_salhigh_{key_suffix}")

    extra_cols = st.columns(2)
    edu = extra_cols[0].selectbox("学历要求", EDU_OPTIONS,
                                   index=_safe_index(EDU_OPTIONS, getattr(prefill, "min_education", None) or "不限"),
                                   key=f"posf_edu_{key_suffix}")
    exp = extra_cols[1].selectbox("工作年限", EXP_OPTIONS,
                                   index=_safe_index(EXP_OPTIONS, getattr(prefill, "min_experience", None) or "不限"),
                                   key=f"posf_exp_{key_suffix}")

    btn_label = "💾 保存修改" if prefill else "保存岗位"
    clicked = st.button(btn_label, type="primary", key=f"posf_btn_{key_suffix}")

    if sal_low == "不限" and sal_high == "不限":
        salary_range = ""
    elif sal_low == "不限":
        salary_range = f"≤{sal_high}"
    elif sal_high == "不限":
        salary_range = f"≥{sal_low}"
    else:
        salary_range = f"{sal_low}-{sal_high}"

    return clicked, {
        "title": title, "department": dept, "requirements": req,
        "salary_range": salary_range,
        "min_education": None if edu == "不限" else edu,
        "min_experience": None if exp == "不限" else exp,
    }


@st.dialog("编辑岗位", width="large")
def _open_edit_position_dialog():
    pos_id = st.session_state.get("edit_pos_id")
    if not pos_id:
        st.error("缺少岗位 ID")
        return
    session = create_resume_session()
    svc = ResumeArchiveService(session)
    pos = session.get(JobPosition, pos_id)
    if not pos:
        session.close()
        st.error("岗位不存在")
        return
    clicked, data = _render_position_form(prefill=pos, key_suffix=f"edit_{pos_id}")
    if clicked:
        if not data["title"]:
            st.warning("请填写岗位名称")
        else:
            svc.update_position(pos_id, **data)
            session.close()
            st.session_state.pop("edit_pos_id", None)
            st.success("修改已保存")
            st.rerun()
    else:
        session.close()


with tabs[2]:
    st.markdown("### 招聘岗位录入/匹配")

    with st.expander("➕ 录入新岗位", expanded=False):
        clicked, data = _render_position_form(prefill=None, key_suffix="new")
        if clicked:
            if not data["title"]:
                st.warning("请填写岗位名称")
            else:
                session = create_resume_session()
                svc = ResumeArchiveService(session)
                svc.create_position(**data)
                session.close()
                st.success(f"岗位「{data['title']}」已保存")
                st.rerun()

    # ---- 两栏布局：左 1/3 岗位列表，右 2/3 匹配结果 ----
    session = create_resume_session()
    svc = ResumeArchiveService(session)
    positions = svc.list_positions()

    if not positions:
        st.info("暂无岗位。请先录入招聘岗位。")
        session.close()
    else:
        if "match_selected_pos" not in st.session_state or \
           st.session_state.match_selected_pos not in {p.position_id for p in positions}:
            st.session_state.match_selected_pos = positions[0].position_id

        pos_col, match_col = st.columns([1, 2])

        # ---- 左栏：岗位 expander 列表 ----
        with pos_col:
            st.markdown("**招聘岗位**")
            # 缩小左栏岗位 expander 内的 markdown / caption 字体（保留 streamlit 原生排版）
            st.markdown(
                """<style>
                div[data-testid="stExpander"] .stMarkdown p,
                div[data-testid="stExpander"] .stMarkdown li,
                div[data-testid="stExpander"] .stMarkdown h1,
                div[data-testid="stExpander"] .stMarkdown h2,
                div[data-testid="stExpander"] .stMarkdown h3,
                div[data-testid="stExpander"] [data-testid="stCaptionContainer"],
                div[data-testid="stExpander"] [data-testid="stCaptionContainer"] p {
                    font-size: 13px !important;
                    line-height: 1.5 !important;
                }
                </style>""",
                unsafe_allow_html=True,
            )
            for pos in positions:
                is_sel = pos.position_id == st.session_state.match_selected_pos
                exp_label = ("▶ " if is_sel else "") + pos.title
                if pos.salary_range:
                    exp_label += f"（{pos.salary_range}）"
                with st.expander(exp_label, expanded=is_sel):
                    if not is_sel:
                        # 用专属 container（带唯一 key）包按钮，再用 has-selector 锚定容器内的按钮
                        view_btn_container = st.container(key=f"viewbtn_wrap_{pos.position_id}")
                        st.markdown(
                            f"""<style>
                            div.st-key-viewbtn_wrap_{pos.position_id} button,
                            div.st-key-viewbtn_wrap_{pos.position_id} button:focus,
                            div.st-key-viewbtn_wrap_{pos.position_id} button:active {{
                                background-color: var(--color-success-soft) !important;
                                color: var(--color-success) !important;
                                border-color: var(--color-success) !important;
                            }}
                            div.st-key-viewbtn_wrap_{pos.position_id} button p {{
                                color: var(--color-success) !important;
                            }}
                            div.st-key-viewbtn_wrap_{pos.position_id} button:hover {{
                                background-color: var(--color-success) !important;
                                border-color: var(--color-success) !important;
                                color: var(--color-surface) !important;
                            }}
                            div.st-key-viewbtn_wrap_{pos.position_id} button:hover p {{
                                color: var(--color-surface) !important;
                            }}
                            </style>""",
                            unsafe_allow_html=True,
                        )
                        with view_btn_container:
                            if st.button("查看匹配结果 >>>", key=f"pos_view_{pos.position_id}",
                                         use_container_width=True):
                                st.session_state.match_selected_pos = pos.position_id
                                st.rerun()
                    meta_bits = []
                    if pos.department:
                        meta_bits.append(f"部门：{pos.department}")
                    if pos.min_education:
                        meta_bits.append(f"学历：{pos.min_education}")
                    if pos.min_experience:
                        meta_bits.append(f"年限：{pos.min_experience}")
                    if meta_bits:
                        st.caption(" | ".join(meta_bits))
                    if pos.requirements:
                        st.markdown(pos.requirements)
                    else:
                        st.caption("（无岗位要求说明）")

                    pos_btn_cols = st.columns(2)
                    if pos_btn_cols[0].button("🎯 智能匹配", key=f"pos_match_{pos.position_id}",
                                               type="primary", use_container_width=True,
                                               disabled=not ai_service.is_configured):
                        st.session_state.match_selected_pos = pos.position_id
                        st.session_state["trigger_match"] = pos.position_id
                        st.rerun()
                    if pos_btn_cols[1].button("🧹 清除匹配", key=f"pos_clear_{pos.position_id}",
                                               use_container_width=True):
                        st.session_state.match_selected_pos = pos.position_id
                        session_tmp = create_resume_session()
                        ResumeArchiveService(session_tmp).clear_position_matches(pos.position_id)
                        session_tmp.close()
                        st.rerun()
                    pos_btn_cols2 = st.columns(2)
                    if pos_btn_cols2[0].button("✏️ 编辑岗位", key=f"pos_edit_{pos.position_id}",
                                               use_container_width=True):
                        st.session_state["edit_pos_id"] = pos.position_id
                        _open_edit_position_dialog()
                    if pos_btn_cols2[1].button("🗑️ 删除", key=f"pos_del_{pos.position_id}",
                                               use_container_width=True):
                        svc.delete_position(pos.position_id)
                        st.session_state.pop("match_selected_pos", None)
                        session.close()
                        st.rerun()

        # ---- 右栏：只放匹配的候选人简历 ----
        def _score_color(score: int) -> str:
            """50% → 红棕, 95%+ → 亮绿, HSL 连续渐变。"""
            clamped = max(50, min(score, 95))
            hue = 15 + (clamped - 50) * 105 / 45
            return f"hsl({hue:.0f}, 70%, 38%)"

        with match_col:
            sel_pos = next((p for p in positions if p.position_id == st.session_state.match_selected_pos), None)
            if not sel_pos:
                session.close()
            else:
                # 如果左栏触发了匹配，执行 AI 评估
                if st.session_state.pop("trigger_match", None) == sel_pos.position_id:
                    all_candidates, _ = svc.list_candidates(page=1, page_size=10000)
                    candidate_dicts = []
                    for c in all_candidates:
                        skills_str = "、".join(s.skill_name or "" for s in c.skills if s.skill_name)[:100]
                        work_str = "; ".join(
                            f"{w.company_name}({w.position or '-'})"
                            for w in (c.work_experiences or [])[:3]
                        )
                        candidate_dicts.append({
                            "candidate_id": c.candidate_id,
                            "name": c.name,
                            "education_level": c.education_level or "-",
                            "current_city": c.current_city or "-",
                            "position": c.work_experiences[0].position if c.work_experiences else "-",
                            "skills": skills_str or "-",
                            "work_summary": work_str or "-",
                        })
                    svc.clear_position_matches(sel_pos.position_id)
                    total = len(candidate_dicts)
                    chunk_size = max(3, min(20, total // 5))
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    all_results = []
                    for i in range(0, total, chunk_size):
                        chunk = candidate_dicts[i:i + chunk_size]
                        done = min(i + chunk_size, total)
                        status_text.markdown(f"AI 正在评估候选人匹配度… **{i}/{total}**")
                        progress_bar.progress(i / total if total > 0 else 0)
                        results = ai_service.match_candidates(
                            sel_pos.requirements or sel_pos.title, chunk
                        )
                        all_results.extend(results)
                    progress_bar.progress(1.0)
                    status_text.markdown(f"AI 评估完成 **{total}/{total}**，正在保存…")
                    for r in all_results:
                        cid = r.get("candidate_id")
                        score = r.get("match_score", 0)
                        reason = r.get("reason", "")
                        if cid and isinstance(score, (int, float)):
                            svc.save_position_match(sel_pos.position_id, int(cid), int(score), reason)
                    progress_bar.empty()
                    status_text.empty()
                    st.rerun()

                # 展示匹配结果 Banner
                matches = svc.list_position_matches(sel_pos.position_id, min_score=50)
                if matches:
                    st.markdown(f"**{sel_pos.title}** — 匹配结果（≥50%，共 {len(matches)} 人）")
                    for match_row, cand in matches:
                        score = match_row.score
                        reason = match_row.reason or ""
                        with st.container(border=True):
                            # 第一行：姓名+主信息 左顶头 | 匹配度右侧 | 邀约按钮
                            h_cols = st.columns([3.5, 1.5, 0.3, 1.2])
                            info_parts = []
                            if cand.gender:
                                info_parts.append(cand.gender)
                            if cand.age:
                                info_parts.append(f"{cand.age}岁")
                            if cand.education_level:
                                info_parts.append(cand.education_level)
                            if cand.current_city:
                                info_parts.append(cand.current_city)
                            if cand.work_experiences and cand.work_experiences[0].position:
                                info_parts.append(cand.work_experiences[0].position)
                            info_str = f"&nbsp;&nbsp;&nbsp;{'  |  '.join(info_parts)}" if info_parts else ""
                            h_cols[0].markdown(
                                f"<div style='margin:0; line-height:1.4; padding-top:12px;'>"
                                f"<span style='font-size:22px; font-weight:700; color:var(--color-text);'>{cand.name}</span>"
                                f"<span style='font-size:14px; color:var(--color-text-secondary);'>{info_str}</span></div>",
                                unsafe_allow_html=True,
                            )
                            color = _score_color(score)
                            h_cols[1].markdown(
                                f"<div style='text-align:right;'>"
                                f"<span style='font-size:12px; color:var(--color-text-muted);'>匹配度 </span>"
                                f"<span style='font-size:32px; font-weight:700; color:{color};'>{score}%</span>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                            # 第 3 列空白做气口
                            with h_cols[3]:
                                st.markdown("<div style='padding-top:10px;'></div>",
                                            unsafe_allow_html=True)
                                if st.button("📧 邀约面试", key=f"match_invite_{sel_pos.position_id}_{cand.candidate_id}"):
                                    st.session_state["invite_dialog_cid"] = cand.candidate_id
                                    _open_invite_dialog()

                            # AI 评语：主题主色（紧贴上方）
                            if reason:
                                st.markdown(
                                    f"<div style='color:var(--color-primary); margin:0;'>"
                                    f"💡 <b>AI 评语：</b>{reason}</div>",
                                    unsafe_allow_html=True,
                                )

                            # 简要履历
                            edu_str = " / ".join(
                                f"{e.school_name}({e.education_level or ''}·{e.major or ''})"
                                for e in (cand.educations or [])[:2]
                            )
                            work_str = " → ".join(
                                f"{w.company_name}·{w.position or '-'}（{_fmt_date(w.start_date) or '?'} 至 {_fmt_date(w.end_date) or '至今'}）"
                                for w in (cand.work_experiences or [])[:3]
                            )
                            if edu_str:
                                st.markdown(f"🎓 {edu_str}")
                            if work_str:
                                st.markdown(f"💼 {work_str}")

                            # 联系方式（黑色正常字号）
                            contacts = []
                            if cand.phone:
                                contacts.append(f"📱 {cand.phone}")
                            if cand.email:
                                contacts.append(f"📧 {cand.email}")
                            if cand.wechat:
                                contacts.append(f"💬 {cand.wechat}")
                            contact_text = (" | ".join(contacts) if contacts else "无联系方式")
                            st.markdown(
                                f"<div style='color:var(--color-text); font-size:14px; margin:4px 0;'>{contact_text}</div>",
                                unsafe_allow_html=True,
                            )

                            # 简历文件 + 右下角来源信息
                            if cand.resume_source and cand.resume_source.file_path:
                                fp = Path(cand.resume_source.file_path)
                                file_btn_key = f"filebtns_{sel_pos.position_id}_{cand.candidate_id}"
                                st.markdown(
                                    f"""<style>
                                    .st-key-{file_btn_key} {{
                                        margin-top: -8px !important;
                                        margin-bottom: 0 !important;
                                    }}
                                    .st-key-{file_btn_key} [data-testid="stHorizontalBlock"] {{
                                        gap: 4px !important;
                                        align-items: center !important;
                                    }}
                                    .st-key-{file_btn_key} button {{
                                        min-width: 32px !important;
                                        width: 32px !important;
                                        height: 32px !important;
                                        padding: 0 !important;
                                        display: inline-flex !important;
                                        align-items: center !important;
                                        justify-content: center !important;
                                        border-radius: 6px !important;
                                        font-size: 14px !important;
                                        line-height: 1 !important;
                                    }}
                                    .st-key-{file_btn_key} button p {{
                                        margin: 0 !important;
                                        padding: 0 !important;
                                        font-size: 14px !important;
                                        line-height: 1 !important;
                                    }}
                                    </style>""",
                                    unsafe_allow_html=True,
                                )
                                src_info = ""
                                if cand.resume_source:
                                    rs = cand.resume_source
                                    src_info = f"来源：{rs.source_platform or '-'} | 入库：{rs.crawl_time.strftime('%Y-%m-%d') if rs.crawl_time else '-'}"
                                with st.container(key=file_btn_key):
                                    file_cols = st.columns([7, 0.6, 0.6, 5])
                                    file_cols[0].markdown(
                                        f"<div style='color:var(--color-text); font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; padding-top:6px;'>📎 {fp}</div>",
                                        unsafe_allow_html=True,
                                    )
                                    if file_cols[1].button("📄", key=f"mopen_{sel_pos.position_id}_{cand.candidate_id}", help="打开文件"):
                                        try:
                                            os.startfile(str(fp))
                                        except OSError:
                                            pass
                                    if file_cols[2].button("📁", key=f"mdir_{sel_pos.position_id}_{cand.candidate_id}", help="访问目录"):
                                        try:
                                            os.startfile(str(fp.parent))
                                        except OSError:
                                            pass
                                    file_cols[3].markdown(
                                        f"<div style='text-align:right; color:var(--color-text-muted); font-size:12px; padding-top:8px;'>{src_info}</div>",
                                        unsafe_allow_html=True,
                                    )
                else:
                    st.info("暂无匹配结果。在左侧岗位中点击「🎯 智能匹配」开始 AI 评估。")

                session.close()
