"""面试管理页面：按面试进度管理邀约、评价与面试工具。"""

import os
from datetime import datetime
from pathlib import Path

import streamlit as st
from streamlit.components.v1 import html as st_html
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.config.settings import get_settings
import recruitment_assistant.services.resume_ai_service as resume_ai_service_module
from recruitment_assistant.services.resume_ai_service import ResumeAIService
from recruitment_assistant.services.resume_archive_service import ResumeArchiveService
from recruitment_assistant.storage.resume_db import create_resume_session, init_resume_database
from recruitment_assistant.storage.resume_models import Candidate, InterviewEvaluation, InterviewOutline
from recruitment_assistant.storage.models import JobPosition
from recruitment_assistant.storage.db import create_session as create_pg_session


@st.cache_resource
def ensure_resume_database_initialized() -> None:
    init_resume_database()


ensure_resume_database_initialized()
get_settings.cache_clear()
settings = get_settings()

st.set_page_config(page_title="面试管理", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("面试管理")
page_header("面试管理", "集中跟进待面试、面试轮次、评价记录与取消面试。")


@st.cache_resource
def get_ai_service(chain_key: tuple) -> ResumeAIService:
    endpoints = [
        {"name": n, "api_key": k, "base_url": u, "model": m}
        for (n, k, u, m) in chain_key
    ]
    if not endpoints:
        endpoints = [{"name": "默认接口", "api_key": settings.ai_api_key,
                      "base_url": settings.ai_base_url, "model": settings.ai_model}]
    return ResumeAIService(endpoints=endpoints)


def _current_ai_service() -> ResumeAIService:
    from recruitment_assistant.config.ai_model_manager import get_endpoint_chain
    chain = get_endpoint_chain()
    chain_key = tuple((e["name"], e["api_key"], e["base_url"], e["model"]) for e in chain)
    return get_ai_service(chain_key)


ai_service = _current_ai_service()
ROUND_LABELS = ["一面", "二面", "三面", "四面", "五面"]
FILTERS = ["待面试", "第一轮面试", "第二轮面试", "第三轮以上面试", "已取消"]


_CONCLUSION_COLORS = {
    "通过": "#16a34a",
    "待定": "#d97706",
    "淘汰": "#dc2626",
}


def _conclusion_html(value: str | None) -> str:
    text = value or "-"
    color = _CONCLUSION_COLORS.get(text)
    if not color:
        return text
    return f"<span style='color:{color}; font-weight:700'>{text}</span>"


def _fmt_date(d) -> str:
    if not d:
        return ""
    try:
        return d.strftime("%Y-%m-%d")
    except Exception:
        return str(d)


def _progress_label(eval_count: int) -> str:
    idx = max(0, min(eval_count, len(ROUND_LABELS) - 1))
    return ROUND_LABELS[idx]


def _display_filter(inv, eval_count: int) -> str:
    if inv.status == "cancelled":
        return "已取消"
    if eval_count == 0:
        return "待面试"
    if eval_count == 1:
        return "第一轮面试"
    if eval_count == 2:
        return "第二轮面试"
    return "第三轮以上面试"


def _candidate_summary(cand, pos) -> str:
    parts = [f"姓名：{cand.name}"]
    for label, value in (
        ("性别", cand.gender),
        ("年龄", f"{cand.age}岁" if cand.age else ""),
        ("最高学历", cand.education_level),
        ("现居城市", cand.current_city),
    ):
        if value:
            parts.append(f"{label}：{value}")
    if cand.educations:
        edu_lines = []
        for e in cand.educations:
            line = e.school_name
            if e.major:
                line += f" · {e.major}"
            if e.education_level:
                line += f"（{e.education_level}）"
            edu_lines.append(line)
        parts.append("教育经历：" + "；".join(edu_lines))
    if cand.skills:
        skills = "、".join(s.skill_name or "" for s in cand.skills if s.skill_name)
        if skills:
            parts.append(f"核心技能：{skills}")
    if cand.work_experiences:
        parts.append("工作经历：")
        for w in cand.work_experiences[:5]:
            date_range = ""
            if w.start_date:
                date_range = f"（{w.start_date} 至 {w.end_date or '至今'}）"
            parts.append(f"  - {w.company_name} · {w.position or '-'}{date_range}")
            if w.job_content:
                parts.append(f"    职责：{w.job_content}")
    if cand.project_experiences:
        parts.append("项目经验：")
        for p in cand.project_experiences[:5]:
            parts.append(f"  - {p.project_name}（{p.project_role or '-'}）")
            if p.project_desc:
                parts.append(f"    描述：{p.project_desc}")
            if p.project_duty:
                parts.append(f"    职责：{p.project_duty}")
            if p.project_result:
                parts.append(f"    成果：{p.project_result}")
    if cand.job_intention:
        ji = cand.job_intention
        intent_parts = []
        if ji.target_position:
            intent_parts.append(f"目标岗位：{ji.target_position}")
        if ji.target_city:
            intent_parts.append(f"期望城市：{ji.target_city}")
        if ji.expected_salary:
            intent_parts.append(f"期望薪资：{ji.expected_salary}")
        if ji.job_status:
            intent_parts.append(f"求职状态：{ji.job_status}")
        if intent_parts:
            parts.append("求职意向：" + "，".join(intent_parts))
    if cand.self_intro:
        parts.append(f"自我评价：{cand.self_intro}")
    return "\n".join(parts)



def _career_label(cand) -> str:
    if cand.work_experiences:
        work = cand.work_experiences[0]
        return work.position or work.company_name or "-"
    return "-"


def _format_evaluations(evals: list) -> str:
    if not evals:
        return ""
    parts = []
    for ev in evals:
        line = f"- {ev.interview_round or '面试'}：评分 {ev.score or '-'}"
        if ev.strengths:
            line += f"，优势：{ev.strengths}"
        if ev.weaknesses:
            line += f"，不足：{ev.weaknesses}"
        if ev.conclusion:
            line += f"，结论：{ev.conclusion}"
        parts.append(line)
    return "\n".join(parts)


import re as _re


def _markdown_to_print_html(content: str) -> str:
    lines = []
    for line in content.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s == "---":
            lines.append("<hr/>")
        elif s.startswith("### "):
            lines.append(f"<h3>{_inline(s[4:])}</h3>")
        elif s.startswith("## "):
            lines.append(f"<h2>{_inline(s[3:])}</h2>")
        elif s.startswith("# "):
            lines.append(f"<h1>{_inline(s[2:])}</h1>")
        elif s.startswith("- "):
            lines.append(f"<li>{_inline(s[2:])}</li>")
        else:
            lines.append(f"<p>{_inline(s)}</p>")
    return "\n".join(lines)


def _inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


@st.dialog("面试大纲", width="large")
def _open_outline_dialog():
    inv_id = st.session_state.get("interview_outline_inv_id")
    if not inv_id:
        st.error("未选中面试邀约")
        return
    session = create_resume_session()
    svc = ResumeArchiveService(session)
    pg_session = create_pg_session()
    try:
        inv = svc.get_invitation(inv_id)
        if not inv:
            st.error("面试邀约不存在")
            return
        cand = svc.get_candidate(inv.candidate_id)
        if not cand:
            st.error("候选人不存在")
            return
        pos = pg_session.get(JobPosition, inv.position_id) if inv.position_id else None
        evals = svc.list_interview_evals(cand.candidate_id)

        regenerate_key = f"outline_regen_{inv_id}"
        need_generate = st.session_state.pop(regenerate_key, False)

        outline_row = svc.get_outline(inv_id)
        if need_generate or not outline_row:
            with st.spinner("正在生成面试大纲…"):
                position_name = pos.title if pos else "未指定岗位"
                requirements = pos.job_requirements or "" if pos else ""
                eval_text = _format_evaluations(evals)
                content = ai_service.generate_interview_outline(
                    _candidate_summary(cand, pos),
                    position_name,
                    requirements=requirements,
                    evaluations=eval_text,
                )
                outline_row = svc.save_outline(inv_id, cand.candidate_id, inv.position_id, content)
                try:
                    from recruitment_assistant.services.monitoring import record_operation
                    record_operation("面试大纲生成", target=cand.name, status="成功")
                except Exception:
                    pass
                for _fmsg in resume_ai_service_module.pop_failover_notices():
                    st.warning(_fmsg)

        st.markdown(
            f"<div style='font-size:28px;font-weight:900;margin-bottom:8px;'>{cand.name}</div>"
            f"<div style='font-size:14px;color:var(--color-text-secondary);margin-bottom:16px;'>"
            f"{pos.title if pos else '未指定岗位'} · {cand.education_level or '-'} · {f'{cand.age}岁' if cand.age else '-'}</div>",
            unsafe_allow_html=True,
        )
        st.markdown("---")
        st.markdown(outline_row.content)
        st.markdown("---")

        btn_a, btn_b, _ = st.columns([1, 1, 3])
        if btn_a.button("🔄 重新生成", key="outline_regenerate", width="stretch"):
            st.session_state[regenerate_key] = True
            st.session_state["interview_outline_inv_id"] = inv_id
            st.rerun()
        if btn_b.button("🖨️ 打印大纲", key="outline_print", width="stretch"):
            body_html = _markdown_to_print_html(outline_row.content)
            st_html(
                f"""<script>
                var w = window.open('', '_blank');
                w.document.write(`<html><head><title>{cand.name} - 面试大纲</title>
                <style>
                @page {{ size: A4; margin: 2.54cm; }}
                body {{ font-family: SimSun, "宋体", serif; font-size: 12pt; line-height: 1.8; color: #000;
                       max-width: 680px; margin: 0 auto; padding: 40px 0; }}
                h1 {{ font-family: SimHei, "黑体", sans-serif; font-size: 16pt; font-weight: bold;
                     margin: 24pt 0 12pt; text-align: center; }}
                h2 {{ font-family: SimHei, "黑体", sans-serif; font-size: 14pt; font-weight: bold;
                     margin: 18pt 0 8pt; border-bottom: 1px solid #333; padding-bottom: 4pt; }}
                h3 {{ font-family: SimHei, "黑体", sans-serif; font-size: 12pt; font-weight: bold;
                     margin: 12pt 0 6pt; }}
                p {{ margin: 4pt 0; text-indent: 0; }}
                li {{ margin: 3pt 0 3pt 24pt; list-style-type: disc; }}
                strong {{ font-weight: bold; }}
                hr {{ border: none; border-top: 0.5pt solid #999; margin: 12pt 0; }}
                </style></head><body>{body_html}</body></html>`);
                w.document.close();
                w.focus();
                w.print();
                </script>""",
                height=0,
            )
    finally:
        session.close()
        pg_session.close()


@st.dialog("记录面试评价", width="large")
def _open_eval_dialog():
    inv_id = st.session_state.get("interview_eval_inv_id")
    if not inv_id:
        st.error("未选中面试邀约")
        return
    session = create_resume_session()
    svc = ResumeArchiveService(session)
    pg_session_eval = create_pg_session()
    try:
        inv = svc.get_invitation(inv_id)
        if not inv:
            st.error("面试邀约不存在")
            return
        cand = svc.get_candidate(inv.candidate_id)
        if not cand:
            st.error("候选人不存在")
            return
        pos = pg_session_eval.get(JobPosition, inv.position_id) if inv.position_id else None
        evals = svc.list_interview_evals(cand.candidate_id)
        eval_count = len(evals)
        default_round = _progress_label(eval_count)
        basic_bits = [
            f"{cand.age}岁" if cand.age else "年龄-",
            cand.education_level or "学历-",
            _career_label(cand),
            pos.title if pos else "未指定岗位",
        ]

        st.markdown(
            """
            <style>
            div[data-testid="stDialog"] div[role="dialog"] { max-width: 1040px; }
            .eval-page { padding: 2px 4px 0; }
            .eval-name { font-size: 32px; font-weight: 900; color: var(--color-text); margin-bottom: 24px; }
            .eval-basic { font-size: 15px; color: var(--color-text); margin-bottom: 30px; }
            .eval-current { display:flex; align-items:baseline; justify-content:center; gap:12px; margin-top:44px; }
            .eval-current span { font-size:14px; font-weight:700; }
            .eval-current b { font-size:42px; line-height:1; font-weight:500; color:var(--color-text); }
            .eval-divider { height:1px; background:var(--color-border); margin:0 0 22px; }
            .eval-history-title { text-align:center; font-size:22px; font-weight:800; margin:0 0 28px; }
            .eval-history-item { margin:0 0 58px 34px; color:var(--color-text); }
            .eval-history-round { font-size:24px; font-style:italic; font-weight:900; margin-bottom:14px; }
            .eval-history-line { font-size:15px; line-height:1.45; white-space:pre-wrap; }
            .eval-empty-history { color:var(--color-text-muted); text-align:center; margin-top:40px; }
            </style>
            """,
            unsafe_allow_html=True,
        )

        main_col, history_col = st.columns([3.2, 1.35], gap="large")
        with main_col:
            head_left, head_right = st.columns([2.2, 1])
            head_left.markdown(
                f"<div class='eval-page'><div class='eval-name'>{cand.name}</div>"
                f"<div class='eval-basic'>{' | '.join(basic_bits)}</div></div>",
                unsafe_allow_html=True,
            )
            head_right.markdown(
                f"<div class='eval-current'><span>当前环节：</span><b>{default_round}</b></div>",
                unsafe_allow_html=True,
            )
            st.markdown("<div class='eval-divider'></div>", unsafe_allow_html=True)

            form_cols = st.columns([1, 1, 1])
            interviewer = form_cols[0].text_input("面试官", label_visibility="visible", key=f"interview_evaler_{inv_id}")
            interview_date = form_cols[1].date_input("面试时间", value=datetime.now().date(), key=f"interview_date_{inv_id}")
            interview_mode = form_cols[2].selectbox("面试形式", ["线下", "线上"], key=f"interview_mode_{inv_id}")
            interview_record = st.text_area("面试评语", height=270, key=f"interview_record_{inv_id}")

            bottom_cols = st.columns([1.15, 0.15, 1])
            with bottom_cols[0]:
                st.markdown("**面试评价：**")
                score = st.feedback("stars", key=f"interview_stars_{inv_id}")
                score_value = int(score) + 1 if score is not None else None
            with bottom_cols[2]:
                conclusion = st.selectbox("面试结论", ["通过", "待定", "淘汰"], key=f"interview_conclusion_{inv_id}")

            btn_left, _, btn_right = st.columns([1, 1, 1])
            if btn_left.button("保  存", type="primary", width="stretch", key=f"interview_save_{inv_id}"):
                interview_dt = datetime.combine(interview_date, datetime.now().time())
                svc.create_interview_eval(
                    candidate_id=cand.candidate_id,
                    position_id=inv.position_id,
                    interviewer=interviewer,
                    interview_round=default_round,
                    score=score_value,
                    strengths=interview_record,
                    weaknesses="",
                    conclusion=conclusion,
                    notes=f"面试形式：{interview_mode}",
                    interview_time=interview_dt,
                )
                st.session_state.pop("interview_eval_inv_id", None)
                st.rerun()
            if btn_right.button("取  消", width="stretch", key=f"interview_close_{inv_id}"):
                st.session_state.pop("interview_eval_inv_id", None)
                st.rerun()

        with history_col:
            st.markdown("<div class='eval-history-title'>面试历史</div>", unsafe_allow_html=True)
            if not evals:
                st.markdown("<div class='eval-empty-history'>暂无面试历史</div>", unsafe_allow_html=True)
            for ev in reversed(evals):
                time_label = ev.interview_time.strftime("%Y-%m-%d") if ev.interview_time else "-"
                mode_label = "-"
                if ev.notes:
                    mode_label = ev.notes.replace("面试方式：", "").replace("面试形式：", "") or "-"
                st.markdown(
                    f"<div class='eval-history-item'>"
                    f"<div class='eval-history-round'>{ev.interview_round or '-'}</div>"
                    f"<div class='eval-history-line'>时  间：{time_label}<br>"
                    f"方  式：{mode_label}<br>"
                    f"面试官：{ev.interviewer or '-'}<br>"
                    f"结  论：{_conclusion_html(ev.conclusion)}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    finally:
        session.close()
        pg_session_eval.close()


@st.dialog("查看面试评价", width="large")
def _open_eval_history_dialog():
    inv_id = st.session_state.get("interview_history_inv_id")
    if not inv_id:
        st.error("未选中面试邀约")
        return
    session = create_resume_session()
    svc = ResumeArchiveService(session)
    try:
        inv = svc.get_invitation(inv_id)
        if not inv:
            st.error("面试邀约不存在")
            return
        cand = svc.get_candidate(inv.candidate_id)
        if not cand:
            st.error("候选人不存在")
            return
        evals = svc.list_interview_evals(cand.candidate_id)
        st.markdown(f"### {cand.name} 的面试评价")
        if not evals:
            st.info("暂无面试评价记录。")
            return
        for ev in evals:
            with st.container(border=True):
                st.markdown(
                    f"**{ev.interview_round or '-'}** | 面试官：{ev.interviewer or '-'} | "
                    f"评分：{ev.score or '-'}/5 | 结论：{_conclusion_html(ev.conclusion)}",
                    unsafe_allow_html=True,
                )
                if ev.notes:
                    st.caption(ev.notes)
                if ev.strengths:
                    st.markdown(ev.strengths)
                if ev.interview_time:
                    st.caption(f"面试时间：{ev.interview_time.strftime('%Y-%m-%d %H:%M')}")
    finally:
        session.close()


@st.dialog("放弃招聘确认")
def _open_abandon_dialog():
    inv_id = st.session_state.get("interview_abandon_inv_id")
    if not inv_id:
        st.error("未选中面试邀约")
        return
    session = create_resume_session()
    svc = ResumeArchiveService(session)
    try:
        inv = svc.get_invitation(inv_id)
        if not inv:
            st.error("面试邀约不存在")
            return
        cand = svc.get_candidate(inv.candidate_id)
        if not cand:
            st.error("候选人不存在")
            return
        eval_count = len(svc.list_interview_evals(cand.candidate_id))
        st.warning(f"是否要真的彻底放弃对候选人「{cand.name}」的招聘计划？确认后将删除他的所有面试评价记录（共 {eval_count} 条），并保留当前邀约为已取消状态。")
        cols = st.columns(2)
        if cols[0].button("确认放弃招聘", type="primary", width="stretch", key=f"abandon_confirm_{inv_id}"):
            svc.delete_interview_evals(cand.candidate_id)
            svc.update_invitation_status(inv.invitation_id, "cancelled")
            st.session_state.pop("interview_abandon_inv_id", None)
            st.rerun()
        if cols[1].button("再考虑一下", width="stretch", key=f"abandon_cancel_{inv_id}"):
            st.session_state.pop("interview_abandon_inv_id", None)
            st.rerun()
    finally:
        session.close()


st.markdown(
    """
    <style>
    .interview-filter-note { color:var(--color-text-secondary); font-size:13px; margin-top:-6px; margin-bottom:10px; }
    .interview-file-path { color:var(--color-text); font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; padding-top:7px; }
    </style>
    """,
    unsafe_allow_html=True,
)

session = create_resume_session()
svc = ResumeArchiveService(session)
pg_session_main = create_pg_session()
try:
    all_invitations = svc.list_invitations(status=None)
    positions_map = {
        p.id: p
        for p in pg_session_main.scalars(
            select(JobPosition).where(JobPosition.deleted_at.is_(None))
        ).all()
    }
    candidate_ids = sorted({inv.candidate_id for inv in all_invitations if inv.candidate_id})
    candidates_map = {}
    evals_map = {}
    if candidate_ids:
        candidates = session.scalars(
            select(Candidate)
            .where(Candidate.candidate_id.in_(candidate_ids))
            .options(
                selectinload(Candidate.educations),
                selectinload(Candidate.work_experiences),
                selectinload(Candidate.project_experiences),
                selectinload(Candidate.skills),
                selectinload(Candidate.job_intention),
                selectinload(Candidate.resume_source),
            )
        ).all()
        candidates_map = {cand.candidate_id: cand for cand in candidates}
        eval_rows = session.scalars(
            select(InterviewEvaluation)
            .where(InterviewEvaluation.candidate_id.in_(candidate_ids))
            .order_by(InterviewEvaluation.eval_id.desc())
        ).all()
        for ev in eval_rows:
            evals_map.setdefault(ev.candidate_id, []).append(ev)

    rows = []
    for inv in all_invitations:
        cand = candidates_map.get(inv.candidate_id)
        if not cand:
            continue
        evals = evals_map.get(cand.candidate_id, [])
        eval_count = len(evals)
        rows.append({
            "inv": inv,
            "cand": cand,
            "pos": positions_map.get(inv.position_id) if inv.position_id else None,
            "evals": evals,
            "eval_count": eval_count,
            "progress": _progress_label(eval_count),
            "display_filter": _display_filter(inv, eval_count),
        })

    counts = {label: sum(1 for r in rows if r["display_filter"] == label) for label in FILTERS}

    left_col, right_col = st.columns([1, 2])
    with left_col:
        st.markdown("**面试进度**")
        st.markdown("<div class='interview-filter-note'>面试轮次由候选人的面试评价次数自动产生。</div>", unsafe_allow_html=True)
        if "interview_filter" not in st.session_state or st.session_state.interview_filter not in FILTERS:
            st.session_state.interview_filter = "待面试"
        for label in FILTERS:
            button_label = f"{label}（{counts[label]}）"
            if st.button(
                button_label,
                key=f"interview_filter_{label}",
                width="stretch",
                type="primary" if st.session_state.interview_filter == label else "secondary",
            ):
                st.session_state.interview_filter = label
                st.rerun()

    with right_col:
        selected_filter = st.session_state.interview_filter
        display_rows = [r for r in rows if r["display_filter"] == selected_filter]

        st.markdown(f"**{selected_filter}** — 共 {len(display_rows)} 人")
        if not display_rows:
            st.info("当前分类暂无面试记录。")
        for row in display_rows:
            inv = row["inv"]
            cand = row["cand"]
            pos = row["pos"]
            evals = row["evals"]
            progress = row["progress"]
            with st.container(border=True):
                h_cols = st.columns([3.8, 1.4])
                info_parts = []
                for value in (
                    cand.gender,
                    f"{cand.age}岁" if cand.age else "",
                    cand.education_level,
                    cand.current_city,
                    cand.work_experiences[0].position if cand.work_experiences and cand.work_experiences[0].position else "",
                ):
                    if value:
                        info_parts.append(value)
                info_str = f"&nbsp;&nbsp;&nbsp;{'  |  '.join(info_parts)}" if info_parts else ""
                h_cols[0].markdown(
                    f"<div style='margin:0; line-height:1.4; padding-top:10px;'>"
                    f"<span style='font-size:22px; font-weight:700; color:var(--color-text);'>{cand.name}</span>"
                    f"<span style='font-size:14px; color:var(--color-text-secondary);'>{info_str}</span></div>",
                    unsafe_allow_html=True,
                )
                h_cols[1].markdown(
                    f"<div style='text-align:right;'>"
                    f"<span style='font-size:12px; color:var(--color-text-muted);'>面试进度 </span>"
                    f"<span style='font-size:32px; font-weight:700; color:var(--color-primary);'>{progress}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                if pos:
                    st.markdown(f"**拟招岗位：** {pos.title}" + (f"（{pos.department or '-'}）" if pos.department else ""))
                else:
                    st.markdown("**拟招岗位：** （未指定）")

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

                contacts = []
                if cand.phone:
                    contacts.append(f"📱 {cand.phone}")
                if cand.email:
                    contacts.append(f"📧 {cand.email}")
                if cand.wechat:
                    contacts.append(f"💬 {cand.wechat}")
                st.markdown(f"<div style='color:var(--color-text); font-size:14px; margin:4px 0;'>{' | '.join(contacts) if contacts else '无联系方式'}</div>", unsafe_allow_html=True)
                meta = [f"发起：{inv.create_time.strftime('%Y-%m-%d %H:%M') if inv.create_time else '-'}"]
                if inv.notes:
                    meta.append(f"备注：{inv.notes}")
                if evals:
                    latest = evals[0]
                    meta.append(f"最近评价：{latest.interview_round or '-'} · {latest.conclusion or '-'} · {latest.score or '-'}分")
                st.caption(" · ".join(meta))

                fp = Path(cand.resume_source.file_path) if cand.resume_source and cand.resume_source.file_path else None
                if fp:
                    st.markdown(f"<div class='interview-file-path'>📎 {fp}</div>", unsafe_allow_html=True)
                wp = Path(cand.resume_source.attachment_works_path) if cand.resume_source and getattr(cand.resume_source, "attachment_works_path", None) else None
                if wp:
                    works_row = st.columns([8, 1])
                    works_row[0].markdown(f"<div class='interview-file-path'>🎨 {wp}</div>", unsafe_allow_html=True)
                    if works_row[1].button("打开作品", key=f"interview_open_works_{inv.invitation_id}", width="stretch", disabled=not wp.exists()):
                        try:
                            os.startfile(str(wp))
                        except OSError as exc:
                            st.error(f"打开作品失败：{exc}")

                action_cols = st.columns(5)
                if action_cols[0].button("打开简历", key=f"interview_open_{inv.invitation_id}", width="stretch", disabled=not (fp and fp.exists())):
                    try:
                        os.startfile(str(fp))
                    except OSError as exc:
                        st.error(f"打开失败：{exc}")
                if action_cols[1].button("面试大纲", key=f"interview_outline_btn_{inv.invitation_id}", width="stretch", disabled=not ai_service.is_configured):
                    st.session_state["interview_outline_inv_id"] = inv.invitation_id
                    _open_outline_dialog()
                if action_cols[2].button("查看面试评价", key=f"interview_history_{inv.invitation_id}", width="stretch"):
                    st.session_state["interview_history_inv_id"] = inv.invitation_id
                    _open_eval_history_dialog()
                if inv.status == "cancelled":
                    if action_cols[3].button("恢复面试", key=f"interview_restore_{inv.invitation_id}", width="stretch"):
                        svc.update_invitation_status(inv.invitation_id, "pending")
                        st.rerun()
                    if action_cols[4].button("放弃招聘", key=f"interview_abandon_{inv.invitation_id}", width="stretch"):
                        st.session_state["interview_abandon_inv_id"] = inv.invitation_id
                        _open_abandon_dialog()
                else:
                    if action_cols[3].button("记录面试评价", key=f"interview_eval_{inv.invitation_id}", width="stretch"):
                        st.session_state["interview_eval_inv_id"] = inv.invitation_id
                        _open_eval_dialog()
                    if action_cols[4].button("取消面试", key=f"interview_cancel_{inv.invitation_id}", width="stretch"):
                        svc.update_invitation_status(inv.invitation_id, "cancelled")
                        st.rerun()

finally:
    session.close()
    pg_session_main.close()
