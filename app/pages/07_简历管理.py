"""简历分析管理模块 — 4 Tab 页面。

Tab 1: 简历自动解析入库（扫描 → 提取文本 → AI 结构化 → 去重 → 入库）
Tab 2: 简历库浏览（搜索 / 详情 / 删除 / 屏蔽）
Tab 3: 招聘岗位录入/匹配（录入岗位 → AI 匹配候选人）
Tab 4: 面试评价（填写评分 / 查看历史）
"""

import time
from datetime import datetime
from pathlib import Path

import streamlit as st

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.parsers.pdf_resume_parser import (
    extract_text_from_docx,
    extract_text_from_pdf,
    is_empty_or_corrupted,
)
from recruitment_assistant.schemas.resume_archive import CandidateCreate, ResumeSourceCreate
from recruitment_assistant.services.resume_ai_service import ResumeAIService, normalize_platform
from recruitment_assistant.services.resume_archive_service import ResumeArchiveService
from recruitment_assistant.storage.resume_db import create_resume_session, init_resume_database

init_resume_database()
settings = get_settings()

st.set_page_config(page_title="简历管理", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("简历管理")
page_header("简历分析管理", "自动解析入库、浏览管理、岗位匹配、面试评价。")


@st.cache_resource
def get_ai_service() -> ResumeAIService:
    return ResumeAIService(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        model=settings.ai_model,
    )


ai_service = get_ai_service()

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
    elif suffix in (".docx", ".doc"):
        return extract_text_from_docx(path)
    return ""


# ==================== 4 Tab 页面 ====================
tabs = st.tabs(["📥 简历自动解析入库", "📋 简历库浏览", "🎯 招聘岗位录入/匹配", "💬 面试评价"])

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
            if ai_service.is_configured:
                lines.append("  👉 点击下方「开始自动解析入库」按钮启动任务")
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
        # column-reverse + 倒序数组 → 最新行渲染在底部，scrollbar 默认贴底
        escaped_rows = "".join(
            f"<div class='log-row'>{_html.escape(line) or '&nbsp;'}</div>"
            for line in reversed(recent)
        )
        html = f"""
        <div class='resume-log-window'>
          {escaped_rows}
        </div>
        <style>
          .resume-log-window {{
            height: 460px;
            overflow-y: auto;
            background: #0e1117;
            color: #d4d4d8;
            font-family: 'Consolas','Monaco','Microsoft YaHei Mono','Microsoft YaHei',monospace;
            font-size: 13px;
            line-height: 1.55;
            padding: 12px 14px;
            border-radius: 6px;
            border: 1px solid #334155;
            white-space: pre-wrap;
            word-break: break-all;
            user-select: text;            /* 允许选中文字 */
            display: flex;
            flex-direction: column-reverse; /* 最新行贴底显示 */
          }}
          .resume-log-window .log-row {{
            color: #e4e4e7;
          }}
        </style>
        """
        st.markdown(html, unsafe_allow_html=True)

    render_log_window()

    # ---------- 操作按钮区（窗口外不显示任何任务信息）----------
    task_state = st.session_state.parse_task_state
    is_idle = task_state == "idle"
    is_running = task_state == "running"
    is_stopping = task_state == "stopping"

    btn_cols = st.columns([1.4, 1.2, 1, 1, 3.4])

    if is_idle:
        start_btn = btn_cols[0].button(
            "🚀 开始自动解析入库",
            type="primary",
            disabled=(not ai_service.is_configured) or (not all_files),
            key="parse_start_btn",
        )
        stop_btn = False
    else:
        start_btn = False
        stop_btn = btn_cols[0].button(
            "⏹ 停止解析任务",
            type="secondary",
            disabled=is_stopping,
            key="parse_stop_btn",
        )

    refresh_btn = btn_cols[1].button("🔄 重新扫描", disabled=not is_idle, key="parse_refresh_btn")
    clear_btn = btn_cols[2].button("🧹 清空窗口", disabled=not is_idle, key="parse_clear_btn")

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
        # 任务开始：快照文件列表 + 初始化统计
        st.session_state.parse_task_state = "running"
        st.session_state.parse_queue = list(all_files)
        st.session_state.parse_index = 0
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
        ts = datetime.now().strftime("%H:%M:%S")
        st.session_state.parse_log_lines.append("─" * 60)
        st.session_state.parse_log_lines.append(f"[{ts}] 🚀 解析任务开始")
        st.session_state.parse_log_lines.append(f"  待处理简历：{len(all_files)} 份")
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
        if is_empty_or_corrupted(path):
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
                log(f"           📄 文本提取成功（{len(raw_text)} 字符），调用 AI 结构化…")
                # 3) AI 结构化
                candidate_data = None
                ai_failed = False
                try:
                    candidate_data = ai_service.parse_resume_text(raw_text)
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
with tabs[1]:
    st.markdown("### 简历库浏览")

    # 搜索栏
    search_cols = st.columns([1.5, 1, 1, 1, 0.8])
    s_name = search_cols[0].text_input("姓名", key="browse_name")
    s_city = search_cols[1].text_input("城市", key="browse_city")
    s_edu = search_cols[2].selectbox("学历", ["全部", "博士", "硕士", "本科", "大专", "高中", "中专"], key="browse_edu")
    s_platform = search_cols[3].selectbox("来源", ["全部", "BOSS直聘", "智联招聘", "51前程无忧"], key="browse_platform")
    page_num = search_cols[4].number_input("页码", min_value=1, value=1, step=1, key="browse_page")

    session = create_resume_session()
    svc = ResumeArchiveService(session)
    candidates, total = svc.list_candidates(
        page=page_num,
        page_size=20,
        name=s_name or None,
        city=s_city or None,
        education_level=s_edu if s_edu != "全部" else None,
        platform=s_platform if s_platform != "全部" else None,
    )
    st.caption(f"共 {total} 条记录，当前第 {page_num} 页")

    if candidates:
        for c in candidates:
            with st.expander(f"**{c.name}** | {c.gender or ''} | {c.age or ''}岁 | {c.education_level or ''} | {c.current_city or ''} | {c.phone or ''}"):
                detail_cols = st.columns([3, 1])
                with detail_cols[0]:
                    if c.educations:
                        st.markdown("**教育经历**")
                        for edu in c.educations:
                            st.text(f"  {edu.school_name} | {edu.education_level or ''} | {edu.major or ''}")
                    if c.work_experiences:
                        st.markdown("**工作经历**")
                        for w in c.work_experiences:
                            st.text(f"  {w.company_name} | {w.position or ''} | {w.industry or ''}")
                    if c.skills:
                        st.markdown("**技能**")
                        st.text("  " + "、".join(s.skill_name or "" for s in c.skills if s.skill_name))
                    if c.job_intention:
                        st.markdown("**求职意向**")
                        ji = c.job_intention
                        st.text(f"  {ji.target_position or ''} | {ji.target_city or ''} | {ji.expected_salary or ''}")
                    if c.self_intro:
                        st.markdown("**自我评价**")
                        st.text(f"  {c.self_intro[:200]}")
                with detail_cols[1]:
                    if st.button("🗑️ 删除", key=f"del_{c.candidate_id}"):
                        svc.delete_candidate(c.candidate_id)
                        session.close()
                        st.rerun()
    else:
        st.info("暂无候选人数据。请先在「简历自动解析入库」中导入简历。")
    session.close()

# ==================== Tab 3: 招聘岗位录入/匹配 ====================
with tabs[2]:
    st.markdown("### 招聘岗位录入/匹配")

    # 岗位录入
    with st.expander("➕ 录入新岗位", expanded=False):
        pos_cols = st.columns([2, 1, 2, 1, 1])
        pos_title = pos_cols[0].text_input("岗位名称", key="pos_title")
        pos_dept = pos_cols[1].text_input("部门", key="pos_dept")
        pos_req = pos_cols[2].text_area("岗位要求", key="pos_req", height=100)
        pos_salary = pos_cols[3].text_input("薪资范围", key="pos_salary")
        pos_city = pos_cols[4].text_input("工作城市", key="pos_city")
        if st.button("保存岗位", type="primary"):
            if pos_title:
                session = create_resume_session()
                svc = ResumeArchiveService(session)
                svc.create_position(
                    title=pos_title, department=pos_dept,
                    requirements=pos_req, salary_range=pos_salary, work_city=pos_city,
                )
                session.close()
                st.success(f"岗位「{pos_title}」已保存")
                st.rerun()
            else:
                st.warning("请填写岗位名称")

    # 已有岗位列表 + AI 匹配
    session = create_resume_session()
    svc = ResumeArchiveService(session)
    positions = svc.list_positions()

    if positions:
        st.markdown("**已有岗位**")
        for pos in positions:
            pos_exp = st.expander(f"📌 {pos.title} | {pos.department or ''} | {pos.work_city or ''} | {pos.salary_range or ''}")
            with pos_exp:
                st.text(f"要求：{pos.requirements or '无'}")
                match_cols = st.columns([1, 1, 2])
                if match_cols[0].button("🎯 AI 匹配", key=f"match_{pos.position_id}"):
                    if not ai_service.is_configured:
                        st.error("AI API Key 未配置")
                    else:
                        all_candidates, _ = svc.list_candidates(page=1, page_size=100)
                        candidate_dicts = [
                            {"candidate_id": c.candidate_id, "name": c.name,
                             "education_level": c.education_level, "current_city": c.current_city,
                             "position": c.work_experiences[0].position if c.work_experiences else ""}
                            for c in all_candidates
                        ]
                        with st.spinner("AI 正在匹配..."):
                            results = ai_service.match_candidates(pos.requirements or pos.title, candidate_dicts)
                        if results:
                            st.markdown("**匹配结果**")
                            for r in results[:10]:
                                st.text(f"  ID={r.get('candidate_id')} | 匹配度={r.get('match_score')}% | {r.get('reason', '')}")
                        else:
                            st.info("未找到匹配候选人")
                if match_cols[1].button("📝 生成面试大纲", key=f"outline_{pos.position_id}"):
                    if not ai_service.is_configured:
                        st.error("AI API Key 未配置")
                    else:
                        with st.spinner("AI 正在生成面试大纲..."):
                            outline = ai_service.generate_interview_outline("（通用候选人）", pos.title)
                        st.markdown(outline)
                if match_cols[2].button("🗑️ 删除岗位", key=f"delpos_{pos.position_id}"):
                    svc.delete_position(pos.position_id)
                    session.close()
                    st.rerun()
    else:
        st.info("暂无岗位。请先录入招聘岗位。")
    session.close()

# ==================== Tab 4: 面试评价 ====================
with tabs[3]:
    st.markdown("### 面试评价")

    session = create_resume_session()
    svc = ResumeArchiveService(session)

    # 填写评价
    with st.expander("➕ 新增面试评价", expanded=False):
        eval_cols = st.columns([1.5, 1.5, 1, 1])
        # 候选人选择
        all_candidates, _ = svc.list_candidates(page=1, page_size=200)
        candidate_options = {f"{c.name} (ID:{c.candidate_id})": c.candidate_id for c in all_candidates}
        selected_candidate = eval_cols[0].selectbox("候选人", list(candidate_options.keys()) or ["暂无候选人"], key="eval_candidate")
        # 岗位选择
        positions = svc.list_positions()
        position_options = {f"{p.title} (ID:{p.position_id})": p.position_id for p in positions}
        selected_position = eval_cols[1].selectbox("应聘岗位", ["无"] + list(position_options.keys()), key="eval_position")
        interviewer = eval_cols[2].text_input("面试官", key="eval_interviewer")
        interview_round = eval_cols[3].selectbox("轮次", ["初试", "复试", "终面"], key="eval_round")

        eval_cols2 = st.columns([1, 2, 2])
        score = eval_cols2[0].slider("评分", 1, 10, 5, key="eval_score")
        strengths = eval_cols2[1].text_area("优势", key="eval_strengths", height=80)
        weaknesses = eval_cols2[2].text_area("不足", key="eval_weaknesses", height=80)

        eval_cols3 = st.columns([1, 2])
        conclusion = eval_cols3[0].selectbox("结论", ["通过", "待定", "淘汰"], key="eval_conclusion")
        notes = eval_cols3[1].text_input("备注", key="eval_notes")

        if st.button("💾 保存评价", type="primary"):
            if candidate_options and selected_candidate in candidate_options:
                cid = candidate_options[selected_candidate]
                pid = position_options.get(selected_position) if selected_position != "无" else None
                svc.create_interview_eval(
                    candidate_id=cid,
                    position_id=pid,
                    interviewer=interviewer,
                    interview_round=interview_round,
                    score=score,
                    strengths=strengths,
                    weaknesses=weaknesses,
                    conclusion=conclusion,
                    notes=notes,
                    interview_time=datetime.now(),
                )
                st.success("评价已保存")
                st.rerun()
            else:
                st.warning("请先选择候选人")

    # 历史评价列表
    st.markdown("**历史面试评价**")
    evals = svc.list_interview_evals()
    if evals:
        for ev in evals[:50]:
            candidate = svc.get_candidate(ev.candidate_id)
            c_name = candidate.name if candidate else f"ID:{ev.candidate_id}"
            st.markdown(
                f"- **{c_name}** | 面试官：{ev.interviewer or '未填'} | "
                f"轮次：{ev.interview_round or ''} | 评分：{ev.score or '-'}/10 | "
                f"结论：**{ev.conclusion or '未填'}** | {ev.strengths or ''}"
            )
    else:
        st.info("暂无面试评价记录。")
    session.close()
