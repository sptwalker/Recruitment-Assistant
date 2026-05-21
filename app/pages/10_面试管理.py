"""面试管理页面：按面试进度管理邀约、评价与面试工具。"""

import os
from datetime import datetime
from pathlib import Path

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.services.resume_ai_service import ResumeAIService
from recruitment_assistant.services.resume_archive_service import ResumeArchiveService
from recruitment_assistant.storage.resume_db import create_resume_session, init_resume_database
from recruitment_assistant.storage.resume_models import Candidate, InterviewEvaluation, JobPosition


@st.cache_resource
def ensure_resume_database_initialized() -> None:
    init_resume_database()


ensure_resume_database_initialized()
settings = get_settings()

st.set_page_config(page_title="面试管理", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("面试管理")
page_header("面试管理", "集中跟进待面试、面试轮次、评价记录与取消面试。")


@st.cache_resource
def get_ai_service(api_key: str, base_url: str, model: str) -> ResumeAIService:
    return ResumeAIService(
        api_key=api_key,
        base_url=base_url,
        model=model,
    )


ai_service = get_ai_service(settings.ai_api_key, settings.ai_base_url, settings.ai_model)
ROUND_LABELS = ["一面", "二面", "三面", "四面", "五面"]
FILTERS = ["待面试", "第一轮面试", "第二轮面试", "第三轮以上面试", "已取消"]


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
        ("学历", cand.education_level),
        ("城市", cand.current_city),
        ("自我评价", cand.self_intro),
    ):
        if value:
            parts.append(f"{label}：{value}")
    if cand.skills:
        skills = "、".join(s.skill_name or "" for s in cand.skills if s.skill_name)
        if skills:
            parts.append(f"技能：{skills}")
    if cand.work_experiences:
        works = []
        for w in cand.work_experiences[:3]:
            works.append(f"{w.company_name}·{w.position or '-'}：{w.job_content or ''}")
        parts.append("工作经历：" + "；".join(works))
    if pos:
        parts.append(f"应聘岗位：{pos.title}")
    return "\n".join(parts)



def _career_label(cand) -> str:
    if cand.work_experiences:
        work = cand.work_experiences[0]
        return work.position or work.company_name or "-"
    return "-"


@st.dialog("记录面试评价", width="large")
def _open_eval_dialog():
    inv_id = st.session_state.get("interview_eval_inv_id")
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
        pos = session.get(JobPosition, inv.position_id) if inv.position_id else None
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
            .eval-name { font-size: 32px; font-weight: 900; color: #111; margin-bottom: 24px; }
            .eval-basic { font-size: 15px; color: #111; margin-bottom: 30px; }
            .eval-current { display:flex; align-items:baseline; justify-content:center; gap:12px; margin-top:44px; }
            .eval-current span { font-size:14px; font-weight:700; }
            .eval-current b { font-size:42px; line-height:1; font-weight:500; color:#111; }
            .eval-divider { height:1px; background:#d9d9d9; margin:0 0 22px; }
            .eval-history-title { text-align:center; font-size:22px; font-weight:800; margin:0 0 28px; }
            .eval-history-item { margin:0 0 58px 34px; color:#111; }
            .eval-history-round { font-size:24px; font-style:italic; font-weight:900; margin-bottom:14px; }
            .eval-history-line { font-size:15px; line-height:1.45; white-space:pre-wrap; }
            .eval-empty-history { color:#94a3b8; text-align:center; margin-top:40px; }
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
            if btn_left.button("保  存", type="primary", use_container_width=True, key=f"interview_save_{inv_id}"):
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
            if btn_right.button("取  消", use_container_width=True, key=f"interview_close_{inv_id}"):
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
                    f"结  论：{ev.conclusion or '-'}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    finally:
        session.close()


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
                    f"评分：{ev.score or '-'}/5 | 结论：**{ev.conclusion or '-'}**"
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
        if cols[0].button("确认放弃招聘", type="primary", use_container_width=True, key=f"abandon_confirm_{inv_id}"):
            svc.delete_interview_evals(cand.candidate_id)
            svc.update_invitation_status(inv.invitation_id, "cancelled")
            st.session_state.pop("interview_abandon_inv_id", None)
            st.rerun()
        if cols[1].button("再考虑一下", use_container_width=True, key=f"abandon_cancel_{inv_id}"):
            st.session_state.pop("interview_abandon_inv_id", None)
            st.rerun()
    finally:
        session.close()


st.markdown(
    """
    <style>
    .interview-filter-note { color:#64748b; font-size:13px; margin-top:-6px; margin-bottom:10px; }
    .interview-file-path { color:#111827; font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; padding-top:7px; }
    </style>
    """,
    unsafe_allow_html=True,
)

session = create_resume_session()
svc = ResumeArchiveService(session)
try:
    all_invitations = svc.list_invitations(status=None)
    positions_map = {p.position_id: p for p in session.scalars(select(JobPosition)).all()}
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
                selectinload(Candidate.skills),
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
                use_container_width=True,
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
                    f"<span style='font-size:22px; font-weight:700; color:#000;'>{cand.name}</span>"
                    f"<span style='font-size:14px; color:#333;'>{info_str}</span></div>",
                    unsafe_allow_html=True,
                )
                h_cols[1].markdown(
                    f"<div style='text-align:right;'>"
                    f"<span style='font-size:12px; color:#666;'>面试进度 </span>"
                    f"<span style='font-size:32px; font-weight:700; color:#2563eb;'>{progress}</span>"
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
                st.markdown(f"<div style='color:#000; font-size:14px; margin:4px 0;'>{' | '.join(contacts) if contacts else '无联系方式'}</div>", unsafe_allow_html=True)
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

                action_cols = st.columns(5)
                if action_cols[0].button("打开简历", key=f"interview_open_{inv.invitation_id}", use_container_width=True, disabled=not (fp and fp.exists())):
                    try:
                        os.startfile(str(fp))
                    except OSError as exc:
                        st.error(f"打开失败：{exc}")
                if action_cols[1].button("生成面试大纲", key=f"interview_outline_btn_{inv.invitation_id}", use_container_width=True, disabled=not ai_service.is_configured):
                    position_name = pos.title if pos else "未指定岗位"
                    st.session_state[f"interview_outline_{inv.invitation_id}"] = ai_service.generate_interview_outline(
                        _candidate_summary(cand, pos),
                        position_name,
                    )
                    st.rerun()
                if action_cols[2].button("查看面试评价", key=f"interview_history_{inv.invitation_id}", use_container_width=True):
                    st.session_state["interview_history_inv_id"] = inv.invitation_id
                    _open_eval_history_dialog()
                if inv.status == "cancelled":
                    if action_cols[3].button("恢复面试", key=f"interview_restore_{inv.invitation_id}", use_container_width=True):
                        svc.update_invitation_status(inv.invitation_id, "pending")
                        st.rerun()
                    if action_cols[4].button("放弃招聘", key=f"interview_abandon_{inv.invitation_id}", use_container_width=True):
                        st.session_state["interview_abandon_inv_id"] = inv.invitation_id
                        _open_abandon_dialog()
                else:
                    if action_cols[3].button("记录面试评价", key=f"interview_eval_{inv.invitation_id}", use_container_width=True):
                        st.session_state["interview_eval_inv_id"] = inv.invitation_id
                        _open_eval_dialog()
                    if action_cols[4].button("取消面试", key=f"interview_cancel_{inv.invitation_id}", use_container_width=True):
                        svc.update_invitation_status(inv.invitation_id, "cancelled")
                        st.rerun()

                outline = st.session_state.get(f"interview_outline_{inv.invitation_id}")
                if outline:
                    with st.expander("面试大纲", expanded=True):
                        st.markdown(outline)
finally:
    session.close()
