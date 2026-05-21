from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

import streamlit as st
from sqlalchemy import case, func, select

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.storage.db import create_session
from recruitment_assistant.storage.models import CrawlTask
from recruitment_assistant.storage.resume_db import create_resume_session, init_resume_database
from recruitment_assistant.storage.resume_models import (
    Candidate,
    InterviewEvaluation,
    InterviewInvitation,
    JobPosition,
    ResumeSource,
)

settings = get_settings()
st.set_page_config(page_title="简历智采助手", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("首页")
page_header("首页", "用仪表盘统一查看三大招聘平台采集入口、简历沉淀与面试推进。")


if "show_new_task_dialog" not in st.session_state:
    st.session_state.show_new_task_dialog = False
if st.query_params.get("new_task") == "1":
    st.session_state.show_new_task_dialog = True
    st.query_params.clear()


@st.dialog("新建采集任务", width="small")
def new_task_dialog() -> None:
    st.markdown('<div class="vibe-card"><h3>采集参数</h3>', unsafe_allow_html=True)
    target_site = st.selectbox("目标网站", ["智联招聘"])
    collect_target = st.radio("采集目标", ["指定数量简历", "所有新简历"], horizontal=True)
    max_resumes = None
    search_minutes = None
    if collect_target == "指定数量简历":
        max_resumes = st.number_input("简历数量", min_value=1, max_value=500, value=20, step=1)
    else:
        search_minutes = st.number_input("搜索时间（分钟）", min_value=10, max_value=900, value=60, step=10)
    speed_mode = st.radio("采集速度", ["快速采集（5-15s间隔）", "慢速采集（10-45s间隔）"], horizontal=True)
    account_name = st.text_input("账号标识", value="default")
    st.markdown('</div>', unsafe_allow_html=True)

    task_config = {
        "目标网站": target_site,
        "采集目标": collect_target,
        "简历数量": int(max_resumes) if max_resumes else None,
        "搜索时间分钟": int(search_minutes) if search_minutes else None,
        "采集速度": speed_mode,
        "账号标识": account_name or "default",
        "间隔秒": "5-15" if speed_mode.startswith("快速") else "10-45",
    }
    from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter

    adapter = ZhilianAdapter(account_name=task_config["账号标识"])
    login_artifact_saved = adapter.state_path.exists() or getattr(adapter, "user_data_dir", adapter.state_path.with_name(f"{adapter.state_path.stem}_profile")).exists()
    has_login_state = bool(login_artifact_saved)

    button_label = "已登录开始任务" if has_login_state else "请登录开始任务"

    if st.button(button_label, type="primary"):
        st.session_state.pending_collect_task = {
            **task_config,
            "任务状态": "等待启动",
        }
        st.session_state.auto_start_collect_task = True
        st.session_state.collect_task_logs = []
        st.session_state.collect_candidates = []
        st.session_state.collect_runtime_state = {
            "running": False,
            "paused": False,
            "stopped": False,
            "logs": [],
            "candidates": [],
            "skipped_count": 0,
            "task_config": st.session_state.pending_collect_task,
            "thread": None,
        }
        st.session_state.show_new_task_dialog = False
        st.switch_page("pages/06_智联采集.py")


@st.cache_resource
def ensure_home_resume_database_initialized() -> None:
    init_resume_database()


def fmt_count(value: int | float | None) -> str:
    if value is None:
        return "-"
    return f"{int(value):,}"


def platform_count(platform_counts: dict[str, int], *aliases: str) -> int:
    return sum(int(platform_counts.get(alias, 0) or 0) for alias in aliases)


@st.cache_data(ttl=30)
def load_home_dashboard_data() -> dict:
    data = {
        "resume_total": 0,
        "platform_resume_counts": {"zhilian": 0, "boss": 0, "qiancheng": 0},
        "today_resumes": 0,
        "collect_totals": {"zhilian": 0, "boss": 0, "qiancheng": 0},
        "running_tasks": {"zhilian": 0, "boss": 0, "qiancheng": 0},
        "interviews": {"total": 0, "positions": 0, "pending": 0, "first": 0, "second": 0, "third": 0},
        "errors": [],
    }

    try:
        ensure_home_resume_database_initialized()
        with create_resume_session() as session:
            platform_counts = dict(
                session.execute(
                    select(ResumeSource.source_platform, func.count(ResumeSource.source_id)).group_by(ResumeSource.source_platform)
                ).all()
            )
            today_start = datetime.combine(datetime.now().date(), time.min)
            data["resume_total"] = int(session.scalar(select(func.count(Candidate.candidate_id))) or 0)
            data["platform_resume_counts"] = {
                "zhilian": platform_count(platform_counts, "智联招聘", "智联", "zhilian"),
                "boss": platform_count(platform_counts, "BOSS直聘", "BOSS", "boss"),
                "qiancheng": platform_count(platform_counts, "51前程无忧", "前程无忧", "51", "51job", "qiancheng"),
            }
            data["today_resumes"] = int(
                session.scalar(
                    select(func.count(ResumeSource.source_id)).where(ResumeSource.crawl_time >= today_start)
                )
                or 0
            )
            data["interviews"] = {
                "total": int(session.scalar(select(func.count(InterviewInvitation.invitation_id))) or 0),
                "positions": int(session.scalar(select(func.count(JobPosition.position_id))) or 0),
                "pending": int(
                    session.scalar(
                        select(func.count(InterviewInvitation.invitation_id)).where(InterviewInvitation.status == "pending")
                    )
                    or 0
                ),
                "first": int(
                    session.scalar(
                        select(func.count(InterviewEvaluation.eval_id)).where(
                            InterviewEvaluation.interview_round.in_(["一面", "初面", "第一轮"])
                        )
                    )
                    or 0
                ),
                "second": int(
                    session.scalar(
                        select(func.count(InterviewEvaluation.eval_id)).where(
                            InterviewEvaluation.interview_round.in_(["二面", "复面", "第二轮"])
                        )
                    )
                    or 0
                ),
                "third": int(
                    session.scalar(
                        select(func.count(InterviewEvaluation.eval_id)).where(
                            InterviewEvaluation.interview_round.in_(["三面", "终面", "第三轮"])
                        )
                    )
                    or 0
                ),
            }
    except Exception as exc:
        data["errors"].append(f"简历/面试统计读取失败：{exc}")

    try:
        with create_session() as session:
            success_rows = session.execute(
                select(CrawlTask.platform_code, func.coalesce(func.sum(CrawlTask.success_count), 0))
                .where(CrawlTask.status == "success")
                .group_by(CrawlTask.platform_code)
            ).all()
            running_rows = session.execute(
                select(CrawlTask.platform_code, func.count(CrawlTask.id))
                .where(CrawlTask.status == "running")
                .group_by(CrawlTask.platform_code)
            ).all()
            for code, resume_count in success_rows:
                if code in data["collect_totals"]:
                    data["collect_totals"][code] = int(resume_count or 0)
            for code, task_count in running_rows:
                if code in data["running_tasks"]:
                    data["running_tasks"][code] = int(task_count or 0)
    except Exception as exc:
        data["errors"].append(f"采集统计读取失败：{exc}")

    return data


@st.cache_data(ttl=120)
def zhilian_login_status() -> str:
    state_path = Path("data/browser_state/zhilian_default.json")
    profile_dir = Path("data/browser_state/zhilian_default_profile")
    if state_path.exists() or profile_dir.exists():
        return "已保存登录态"
    return st.session_state.get("zhilian_login_status_default", "待登录")


def session_login_status(platform_code: str) -> str:
    return st.session_state.get(f"{platform_code}_login_status_default", "进入页面检测")


def status_class(text: str) -> str:
    if "已" in text or "连接" in text or "运行" in text:
        return "ok"
    if "失败" in text or "失效" in text:
        return "err"
    return "warn"


def platform_card_html(platform: dict, data: dict) -> str:
    code = platform["code"]
    running = data["running_tasks"].get(code, 0)
    collect_total = data["collect_totals"].get(code, 0)
    resume_total = data["platform_resume_counts"].get(code, 0)
    run_text = "运行中" if running else "待启动"
    return f"""
<div class="home-platform-card">
  <div class="home-platform-head">
    <div>
      <div class="home-platform-kicker">{platform['kicker']}</div>
      <h3>{platform['name']}</h3>
    </div>
    <div class="home-platform-icon">{platform['icon']}</div>
  </div>
  <div class="home-status-grid">
    <div><span>登录状态</span><b class="home-status-{status_class(platform['login_status'])}">{platform['login_status']}</b></div>
    <div><span>运行状态</span><b class="home-status-{status_class(run_text)}">{run_text}</b></div>
  </div>
  <div class="home-card-data">
    <div><span>历史获取</span><strong>{fmt_count(collect_total)}</strong></div>
    <div><span>已入库</span><strong>{fmt_count(resume_total)}</strong></div>
    <div><span>运行批次</span><strong>{fmt_count(running)}</strong></div>
  </div>
  <div class="home-card-actions">
    <a class="vibe-outline-btn" href="/平台登录" target="_self">打开目录</a>
    <a class="vibe-primary-btn" href="{platform['href']}" target="_self">进入采集</a>
  </div>
</div>
"""


def stat_row_html(title: str, subtitle: str, items: list[tuple[str, str, str]]) -> str:
    cells = "".join(
        f'<div class="home-stat-cell"><span>{label}</span><strong>{value}</strong><em>{note}</em></div>'
        for label, value, note in items
    )
    return f"""
<div class="home-stat-panel">
<div class="home-stat-title">
<h3>{title}</h3>
<p>{subtitle}</p>
</div>
<div class="home-stat-grid">{cells}</div>
</div>
"""


home_data = load_home_dashboard_data()
platforms = [
    {
        "code": "zhilian",
        "name": "智联招聘",
        "kicker": "主动搜索 / 已投递",
        "icon": "Z",
        "href": "/智联采集",
        "login_status": zhilian_login_status(),
    },
    {
        "code": "boss",
        "name": "BOSS直聘",
        "kicker": "Chrome 扩展采集",
        "icon": "B",
        "href": "/BOSS采集",
        "login_status": session_login_status("boss"),
    },
    {
        "code": "qiancheng",
        "name": "前程无忧",
        "kicker": "51job 附件简历",
        "icon": "51",
        "href": "/51前程无忧采集",
        "login_status": session_login_status("qiancheng"),
    },
]

dashboard_css = """
<style>
.vibe-page-title { margin-bottom:14px !important; }
.home-dashboard-shell { max-width:1180px; margin:0 auto 24px; padding:26px; background:rgba(255,255,255,.86); border:1px solid #E5EAF2; border-radius:28px; box-shadow:0 24px 80px rgba(15,23,42,.10); }
.home-dashboard-title { display:flex; justify-content:space-between; align-items:flex-end; gap:16px; margin-bottom:18px; }
.home-dashboard-title h2 { margin:0; color:#172033; font-size:24px; font-weight:900; letter-spacing:-.4px; }
.home-dashboard-title p { margin:5px 0 0; color:#64748B; font-size:13px; }
.home-refresh-note { color:#64748B; font-size:12px; white-space:nowrap; }
.home-platform-grid { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:12px; }
.home-platform-card { min-height:292px; padding:20px; background:#FFFFFF; border:1px solid #E5EAF2; border-radius:22px; box-shadow:0 12px 34px rgba(15,23,42,.07); box-sizing:border-box; }
.home-platform-head { display:flex; justify-content:space-between; gap:14px; align-items:flex-start; padding-bottom:14px; border-bottom:1px solid #EEF2F7; }
.home-platform-kicker { color:#64748B; font-size:12px; font-weight:700; }
.home-platform-head h3 { margin:5px 0 0; color:#172033; font-size:22px; font-weight:900; }
.home-platform-icon { display:grid; place-items:center; width:46px; height:46px; color:#2563EB; font-size:18px; font-weight:900; background:#EFF6FF; border-radius:16px; }
.home-status-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:16px 0; }
.home-status-grid div { padding:10px 11px; background:#F8FAFC; border:1px solid #EEF2F7; border-radius:14px; }
.home-status-grid span, .home-card-data span, .home-stat-cell span { display:block; color:#64748B; font-size:12px; line-height:1.2; }
.home-status-grid b { display:block; margin-top:5px; font-size:14px; }
.home-status-ok { color:#168A45; }
.home-status-warn { color:#B7791F; }
.home-status-err { color:#C73552; }
.home-card-data { display:grid; grid-template-columns:repeat(3, 1fr); gap:10px; margin-bottom:16px; }
.home-card-data div { padding:11px; background:#FFFFFF; border:1px solid #EEF2F7; border-radius:14px; text-align:center; }
.home-card-data strong { display:block; margin-top:5px; color:#172033; font-size:20px; font-weight:900; }
.home-card-actions { display:flex; gap:10px; }
.home-card-actions a, .home-card-actions a:link, .home-card-actions a:visited, .home-card-actions a:hover, .home-card-actions a:active { flex:1; justify-content:center; text-decoration:none !important; }
.home-stat-panel { display:grid; grid-template-columns:180px 1fr; gap:18px; margin-top:18px; padding:18px; background:#FFFFFF; border:1px solid #E5EAF2; border-radius:22px; box-shadow:0 10px 28px rgba(15,23,42,.055); }
.home-stat-title { display:flex; flex-direction:column; justify-content:center; }
.home-stat-title h3 { margin:0; color:#172033; font-size:19px; font-weight:900; }
.home-stat-title p { margin:6px 0 0; color:#64748B; font-size:12px; line-height:1.45; }
.home-stat-grid { display:grid; grid-template-columns:repeat(6, minmax(0, 1fr)); gap:10px; }
.home-stat-cell { padding:13px 10px; text-align:center; background:#F8FAFC; border:1px solid #EEF2F7; border-radius:16px; box-sizing:border-box; }
.home-stat-cell strong { display:block; margin-top:6px; color:#172033; font-size:24px; line-height:1.05; font-weight:900; }
.home-stat-cell em { display:block; margin-top:5px; color:#94A3B8; font-size:11px; font-style:normal; }
@media (max-width: 980px) { .home-dashboard-shell { padding:18px; } .home-platform-grid, .home-stat-panel { grid-template-columns:1fr; } .home-stat-grid { grid-template-columns:repeat(2, 1fr); } .home-dashboard-title { align-items:flex-start; flex-direction:column; } }
</style>
"""
st.markdown(dashboard_css, unsafe_allow_html=True)

platform_resume_counts = home_data["platform_resume_counts"]
collect_totals = home_data["collect_totals"]
resume_total = int(home_data["resume_total"] or 0)
collect_total = sum(int(value or 0) for value in collect_totals.values())
archive_rate = round(resume_total / collect_total * 100) if collect_total else 0
interviews = home_data["interviews"]

platform_cards = "".join(platform_card_html(platform, home_data) for platform in platforms)
resume_stats = stat_row_html(
    "简历库总数",
    "按已入库简历来源平台汇总，辅助判断采集转化效果。",
    [
        ("简历库总数", fmt_count(resume_total), "已归档"),
        ("今日采集", fmt_count(home_data["today_resumes"]), "今日入库"),
        ("智联", fmt_count(platform_resume_counts["zhilian"]), "智联招聘"),
        ("BOSS", fmt_count(platform_resume_counts["boss"]), "BOSS直聘"),
        ("前程", fmt_count(platform_resume_counts["qiancheng"]), "51前程无忧"),
        ("入库率", f"{archive_rate}%", "入库/采集"),
    ],
)
interview_stats = stat_row_html(
    "面试总数",
    "跟进邀约池与各轮面试评价数量，快速判断招聘推进节奏。",
    [
        ("面试总数", fmt_count(interviews["total"]), "全部邀约"),
        ("招聘岗位数", fmt_count(interviews["positions"]), "岗位库"),
        ("待邀", fmt_count(interviews["pending"]), "待处理"),
        ("一面", fmt_count(interviews["first"]), "第一轮"),
        ("二面", fmt_count(interviews["second"]), "第二轮"),
        ("三面", fmt_count(interviews["third"]), "第三轮"),
    ],
)

st.markdown(
    f"""
<div class="home-dashboard-shell">
  <div class="home-dashboard-title">
    <div>
      <h2>招聘采集工作台</h2>
      <p>参考三平台入口 + 简历库 + 面试进度的总览结构，关键操作集中在同一屏完成。</p>
    </div>
    <div class="home-refresh-note">数据缓存 30 秒 · 单次采集上限 {settings.crawler_max_resumes_per_task}</div>
  </div>
  <div class="home-platform-grid">{platform_cards}</div>
  {resume_stats}
  {interview_stats}
</div>
""",
    unsafe_allow_html=True,
)

if home_data["errors"]:
    for error in home_data["errors"]:
        st.warning(error)

if st.session_state.show_new_task_dialog:
    new_task_dialog()
