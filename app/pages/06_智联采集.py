import importlib
import inspect
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import delete, func, select

from components.layout import APP_VERSION, inject_vibe_style, page_header
from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.parsers.pdf_resume_parser import clean_candidate_signature
import recruitment_assistant.platforms.zhilian.adapter as zhilian_adapter_module
from recruitment_assistant.schemas.raw_resume import RawResumeCreate
from recruitment_assistant.services.crawl_task_service import CrawlTaskService, platform_candidate_record
from recruitment_assistant.services.raw_resume_service import RawResumeService
from recruitment_assistant.storage.db import Base, create_session, engine
from recruitment_assistant.utils.hash_utils import text_hash

Base.metadata.create_all(bind=engine)

settings = get_settings()
st.set_page_config(page_title="采集任务", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("采集任务")
page_header("采集任务", "创建、编辑并追踪招聘平台简历采集任务。")

st.markdown(
    """
<style>
.collect-panel { background:#fff; border:1px solid #E5EAF2; border-radius:22px; padding:20px; box-shadow:0 12px 32px rgba(31,41,55,.05); margin-bottom:18px; }
.collect-panel h3 { margin:0 0 14px; font-size:18px; line-height:1.3; color:#1F2937; }
.plain-section-title { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:18px 0 10px; }
.plain-section-title h3 { margin:0; font-size:18px; line-height:1.3; color:#1F2937; }
.collect-panel-stat { color:#4A90E2; font-size:14px; font-weight:700; white-space:nowrap; }
.collect-info-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }
.collect-info-item { background:#F7F9FC; border:1px solid #E5EAF2; border-radius:14px; padding:12px 14px; }
.collect-info-label { color:#6B7280; font-size:13px; line-height:1.4; margin-bottom:5px; }
.collect-info-value { color:#1F2937; font-size:14px; line-height:1.4; font-weight:700; word-break:break-all; }
.collect-log-box { height:240px; overflow-y:auto; background:#FFFFFF; color:#1F2937; border:1px solid #E5EAF2; border-radius:16px; padding:14px; font-family:Consolas, Monaco, monospace; font-size:13px; line-height:1.55; white-space:pre-wrap; }
[data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stVerticalBlockBorderWrapper"] > div,
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlock"] {
    background:#FFFFFF !important;
    background-color:#FFFFFF !important;
    border-color:#E5EAF2 !important;
    border-radius:22px !important;
    box-shadow:0 12px 32px rgba(31,41,55,.05);
}
.collect-info-item { background:#FFFFFF; border:1px solid #E5EAF2; border-radius:14px; padding:12px 14px; }
.collect-running-text { color:#F59E0B; font-size:14px; font-weight:700; white-space:nowrap; }
.collect-task-progress { color:#4A90E2; font-size:14px; font-weight:700; white-space:nowrap; }
.collect-empty { color:#94A3B8; font-size:13px; }
</style>
""",
    unsafe_allow_html=True,
)


def default_task_config() -> dict:
    return {
        "目标网站": "智联招聘",
        "采集目标": "指定数量简历",
        "简历数量": 5,
        "搜索时间分钟": None,
        "采集速度": "快速采集（5-15s间隔）",
        "每候选人等待秒数": 20,
        "账号标识": "default",
        "间隔秒": "5-15",
        "任务状态": "等待启动",
    }


def get_runtime_state() -> dict:
    if "collect_runtime_state" not in st.session_state:
        st.session_state.collect_runtime_state = {
            "running": False,
            "paused": False,
            "stopped": False,
            "logs": [],
            "candidates": [],
            "scanned_count": 0,
            "skipped_count": 0,
            "task_config": default_task_config(),
            "thread": None,
        }
    return st.session_state.collect_runtime_state


def init_state() -> None:
    runtime = get_runtime_state()
    if "collect_task_logs" not in st.session_state:
        st.session_state.collect_task_logs = list(runtime.get("logs", []))
    if "collect_candidates" not in st.session_state:
        st.session_state.collect_candidates = list(runtime.get("candidates", []))
    if "collect_paused" not in st.session_state:
        st.session_state.collect_paused = runtime["paused"]
    if "collect_stopped" not in st.session_state:
        st.session_state.collect_stopped = runtime["stopped"]
    if "collect_running" not in st.session_state:
        st.session_state.collect_running = runtime["running"]


def sync_runtime_to_session() -> dict:
    runtime = get_runtime_state()
    pending_task = st.session_state.get("pending_collect_task")
    if pending_task and not runtime.get("running") and runtime.get("task_config", {}).get("任务状态") in {None, "等待启动"}:
        runtime["task_config"] = pending_task
    thread = runtime.get("thread")
    if thread is not None and not thread.is_alive() and runtime.get("running"):
        runtime["running"] = False
        if runtime.get("task_config", {}).get("任务状态") == "运行中":
            runtime["task_config"] = {**runtime.get("task_config", default_task_config()), "任务状态": "已完成"}
    st.session_state.collect_task_logs = list(runtime.get("logs", []))
    st.session_state.collect_candidates = list(runtime.get("candidates", []))
    st.session_state.collect_paused = runtime["paused"]
    st.session_state.collect_stopped = runtime["stopped"]
    st.session_state.collect_running = runtime["running"]
    st.session_state.pending_collect_task = runtime.get("task_config") or pending_task or default_task_config()
    if "scanned_count" not in runtime:
        runtime["scanned_count"] = 0
    if "skipped_count" not in runtime:
        runtime["skipped_count"] = 0
    return runtime


def append_collect_log(message: str) -> None:
    runtime = get_runtime_state()
    timestamp = datetime.now().strftime("%H:%M:%S")
    runtime["logs"].append(f"[{timestamp}] {message}")
    runtime["logs"] = runtime["logs"][-5000:]
    st.session_state.collect_task_logs = list(runtime.get("logs", []))


def render_log_html(container=None) -> None:
    logs = st.session_state.get("collect_task_logs", [])[-300:]
    html = "<br/>".join(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") for line in logs)
    body = html or "等待任务启动..."
    target = container or st
    target.markdown(f'<div id="collect-log-box" class="collect-log-box" data-collect-log-box="1">{body}<span id="collect-log-bottom"></span></div>', unsafe_allow_html=True)
    scroll_token = str(len(logs))
    components.html(
        f"""
        <script>
        (() => {{
            const token = {scroll_token!r};
            const scrollCollectLog = () => {{
                const doc = window.parent.document;
                const boxes = Array.from(doc.querySelectorAll('#collect-log-box, [data-collect-log-box="1"], .collect-log-box'));
                boxes.forEach((box) => {{
                    box.scrollTop = box.scrollHeight;
                    box.dataset.autoScrollToken = token;
                    const bottom = box.querySelector('#collect-log-bottom');
                    if (bottom) bottom.scrollIntoView({{ block: 'end' }});
                }});
            }};
            scrollCollectLog();
            requestAnimationFrame(scrollCollectLog);
            [50, 150, 350, 800, 1200].forEach((delay) => setTimeout(scrollCollectLog, delay));
        }})();
        </script>
        """,
        height=1,
    )


def render_candidate_table(container=None) -> None:
    target = container or st
    candidates = st.session_state.get("collect_candidates", [])
    target.dataframe(candidates, use_container_width=True, hide_index=True)


def render_history_task_table() -> None:
    try:
        with create_session() as session:
            task_service = CrawlTaskService(session)
            task_rows = task_service.list_tasks(limit=50)
            success_task_count, success_resume_count = task_service.success_summary()
    except Exception as exc:
        st.warning(f"历史批次任务读取失败：{exc}")
        task_rows = []
        success_task_count = 0
        success_resume_count = 0

    st.markdown(
        '<div class="plain-section-title"><h3>历史批次任务列表</h3><div class="collect-panel-stat">已成功执行{}次任务，共获取了{}份简历。</div></div>'.format(
            success_task_count,
            success_resume_count,
        ),
        unsafe_allow_html=True,
    )
    st.dataframe(
        [
            {
                "批次ID": row.id,
                "时间": row.started_at,
                "目标网站": "智联招聘" if row.platform_code == "zhilian" else row.platform_code,
                "目标数量": row.planned_count,
                "获取数量": row.success_count,
                "耗时": f"{int((row.finished_at - row.started_at).total_seconds())}秒" if row.started_at and row.finished_at else "运行中",
                "状态": row.status,
            }
            for row in task_rows
        ],
        use_container_width=True,
        hide_index=True,
    )


def save_raw_resume_rows(rows: list[dict], task_id: int | None = None) -> list[int]:
    if not rows:
        return []
    saved_ids = []
    with create_session() as session:
        raw_service = RawResumeService(session)
        for row in rows:
            raw_resume = raw_service.create_raw_resume(RawResumeCreate(**row))
            saved_ids.append(raw_resume.id)
            profile_key = build_profile_dedup_key(
                row.get("platform_code", "zhilian"),
                (row.get("raw_json", {}) or {}).get("pre_download_candidate_info", {}) or {},
            )
            if profile_key:
                upsert_profile_dedup_record(session, row, profile_key, raw_resume.id, task_id)
    return saved_ids


def normalize_duplicate_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_profile_value(value: str) -> str:
    text = normalize_duplicate_text(value)
    return "" if not text or text == "待识别" else text


def normalize_profile_age(value: str) -> str:
    text = normalize_profile_value(value)
    match = re.search(r"(?<!\d)(\d{2})(?!\d)", text)
    if not match:
        return ""
    age = int(match.group(1))
    return f"{age}岁" if 16 <= age <= 70 else ""


def normalize_profile_education(value: str) -> str:
    text = normalize_profile_value(value)
    if not text:
        return ""
    degree_map = {
        "博士": "博士",
        "硕士": "硕士",
        "研究生": "硕士",
        "本科": "本科",
        "大专": "大专",
        "专科": "大专",
        "高中": "高中",
        "中专": "中专",
    }
    for keyword, degree in degree_map.items():
        if keyword in text:
            return degree
    return text


def build_profile_dedup_key(platform_code: str, info: dict) -> str:
    name = normalize_profile_value(info.get("name") or info.get("姓名") or "")
    age = normalize_profile_age(info.get("age") or info.get("年龄") or "")
    education = normalize_profile_education(info.get("education") or info.get("highest_degree") or info.get("学历") or "")
    if not name or not age or not education:
        return ""
    return text_hash("|".join([platform_code, "profile_name_age_education", name, age, education])) or ""


def build_profile_dedup_label(info: dict) -> str:
    name = normalize_profile_value(info.get("name") or info.get("姓名") or "") or "待识别"
    age = normalize_profile_age(info.get("age") or info.get("年龄") or "") or "待识别"
    education = normalize_profile_education(info.get("education") or info.get("highest_degree") or info.get("学历") or "") or "待识别"
    return f"{name}/{age}/{education}"


def build_profile_lookup_keys(platform_code: str = "zhilian") -> set[str]:
    with create_session() as session:
        return {
            str(key)
            for key in session.execute(
                platform_candidate_record.select()
                .with_only_columns(platform_candidate_record.c.candidate_key)
                .where(platform_candidate_record.c.platform_code == platform_code)
            ).scalars()
            if key
        }


def get_profile_dedup_count(platform_code: str = "zhilian") -> int:
    return len(build_profile_lookup_keys(platform_code))


def upsert_profile_dedup_record(session, row: dict, profile_key: str, raw_resume_id: int, task_id: int | None = None) -> None:
    raw_json = row.get("raw_json", {}) or {}
    info = raw_json.get("pre_download_candidate_info", {}) or {}
    existing = session.execute(
        platform_candidate_record.select().where(
            platform_candidate_record.c.platform_code == row.get("platform_code", "zhilian"),
            platform_candidate_record.c.candidate_key == profile_key,
        )
    ).mappings().first()
    if existing:
        session.execute(
            platform_candidate_record.update()
            .where(platform_candidate_record.c.id == existing["id"])
            .values(
                hit_count=int(existing.get("hit_count") or 0) + 1,
                raw_resume_id=existing.get("raw_resume_id") or raw_resume_id,
                task_id=existing.get("task_id") or task_id,
            )
        )
        session.commit()
        return
    session.execute(
        platform_candidate_record.insert().values(
            platform_code=row.get("platform_code", "zhilian"),
            target_site="智联招聘",
            candidate_key=profile_key,
            candidate_signature=None,
            name=normalize_profile_value(info.get("name") or info.get("姓名") or "") or None,
            gender=None,
            job_title=None,
            phone=None,
            resume_file_name=None,
            source_url=row.get("source_url"),
            content_hash=None,
            raw_resume_id=raw_resume_id,
            task_id=task_id,
        )
    )
    session.commit()


def build_profile_debug_index(platform_code: str = "zhilian") -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    with create_session() as session:
        rows = session.execute(
            select(platform_candidate_record.c.name, platform_candidate_record.c.candidate_key)
            .where(platform_candidate_record.c.platform_code == platform_code)
        ).mappings()
        for row in rows:
            name = normalize_profile_value(row.get("name") or "")
            if not name:
                continue
            values = index.setdefault(name, [])
            key_text = str(row.get("candidate_key") or "")[:12]
            item = f"pre_key:{key_text}"
            if item not in values:
                values.append(item)
    return index


def clean_candidate_name(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or text == "待识别":
        return "待识别"
    stop_tokens = [
        "沟通", "聊天", "附件", "简历", "查看", "下载", "电话", "手机号", "求职", "职位", "岗位",
        "未读", "已读", "在线", "打招呼", "要附件", "本科", "专科", "硕士", "博士", "经验",
        "设置备注", "不合适", "已向对方要附件简历", "待识别",
    ]
    candidates = []
    for part in re.split(r"[｜|/\\,，;；:：\n\r\t ]+", text):
        part = part.strip(" ·-—_()（）[]【】")
        if not part or any(token in part for token in stop_tokens):
            continue
        if re.search(r"\d|岁|年|男|女", part):
            continue
        if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", part) or re.fullmatch(r"[A-Za-z][A-Za-z .·-]{1,30}", part):
            candidates.append(part)
    return candidates[0] if candidates else "待识别"


def parse_candidate_signature(signature: str) -> tuple[str, str]:
    name, job_title = clean_candidate_signature(signature or "")
    return clean_candidate_name(name or ""), clean_job_title(job_title or "", name or "")


def is_unknown_or_noise(value: str) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return not text or text == "待识别" or any(
        token in text
        for token in ["设置备注", "不合适", "已向对方要附件简历", "要附件简历", "查看附件简历"]
    )


def is_probably_person_name(value: str) -> bool:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    return bool(re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", text) or re.fullmatch(r"[A-Za-z][A-Za-z .·-]{1,30}", text))


JOB_TITLE_HINTS = [
    "工程师", "经理", "主管", "专员", "顾问", "运营", "销售", "开发", "产品", "设计", "会计", "人事",
    "行政", "客服", "教师", "司机", "助理", "总监", "招聘", "采购", "算法", "测试", "前端", "后端",
    "架构", "实施", "运维", "财务", "出纳", "法务", "分析师", "需求分析",
]
COMPANY_NOISE_TOKENS = ["有限公司", "分公司", "集团", "科技", "公司", "企业", "中心", "事业部", "工作室", "系统集成"]
JOB_SECTION_NOISE_TOKENS = ["工作经历", "项目经历", "教育经历", "实习经历", "培训经历", "校园经历"]


def extract_core_job_title(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" -—｜|:：")
    if not text:
        return ""
    text = re.sub(r"^(求职岗位|求职职位|应聘岗位|应聘职位|期望职位|期望岗位|目标职位|目标岗位|职位|岗位)[:： ]*", "", text).strip(" -—｜|")
    text = re.sub(r"^(" + "|".join(JOB_SECTION_NOISE_TOKENS) + r")\s*[（(]?\s*\d+(?:\.\d+)?\s*年\s*[）)]?\s*", "", text).strip(" -—｜|")
    text = re.split(r"电话|手机|性别|姓名|男|女|\d{2,}|岁|经验|本科|专科|硕士|博士|学历|在线|沟通|附件|简历", text)[0].strip(" -—｜|")
    parts = [part.strip(" -—｜|/\\,，;；:：()（）[]【】") for part in re.split(r"[·•|｜/\\,，;；\n\r\t]+", text)]
    candidates = [part for part in parts if part]
    candidates.append(text)
    for part in reversed(candidates):
        if not (2 <= len(part) <= 40):
            continue
        if any(token in part for token in COMPANY_NOISE_TOKENS + JOB_SECTION_NOISE_TOKENS):
            continue
        if any(hint.lower() in part.lower() for hint in JOB_TITLE_HINTS):
            return part
    return text if 2 <= len(text) <= 40 and not any(token in text for token in COMPANY_NOISE_TOKENS + JOB_SECTION_NOISE_TOKENS) else ""


def clean_job_title(value: str, candidate_name: str = "") -> str:
    text = extract_core_job_title(value)
    if not text or text == "待识别":
        return "待识别"
    if text == candidate_name or is_probably_person_name(text):
        return "待识别"
    if any(token in text for token in ["电话", "手机", "附件", "简历", "聊天", "沟通", "未读", "已读", "设置备注", "不合适"]):
        return "待识别"
    return text if 2 <= len(text) <= 40 else "待识别"


def build_candidate_record(row: dict) -> dict:
    raw_json = row.get("raw_json", {})
    info = raw_json.get("candidate_info", {}) or {}
    attachment = raw_json.get("attachment", {}) or {}
    file_path = attachment.get("file_path") or ""
    signature_name, signature_job_title = parse_candidate_signature(raw_json.get("candidate_signature") or "")
    info_name = clean_candidate_name(info.get("name") or "")
    candidate_name = signature_name if is_unknown_or_noise(info_name) else info_name
    job_from_info = clean_job_title(info.get("job_title") or "", candidate_name)
    job_title = signature_job_title if is_unknown_or_noise(job_from_info) else job_from_info
    age = info.get("age") or info.get("年龄") or "待识别"
    education = info.get("education") or info.get("highest_degree") or info.get("学历") or "待识别"
    return {
        "姓名": candidate_name,
        "年龄": age,
        "学历": education,
        "求职岗位": job_title,
        "电话": info.get("phone") or "待识别",
        "简历文件名": info.get("resume_file_name") or attachment.get("file_name") or Path(file_path).name or "待识别",
    }


def legacy_render_log_html() -> None:
    render_log_html()


def render_task_editor(task_config: dict, has_login_state: bool, disabled: bool = False) -> dict:
    st.markdown('<div class="plain-section-title"><h3>当前采集任务</h3></div>', unsafe_allow_html=True)
    task_banner = st.container(border=True)
    with task_banner:
        col1, col2, col3 = st.columns(3)
        target_options = ["智联招聘"]
        target_site = col1.selectbox(
            "目标网站",
            target_options,
            index=target_options.index(task_config.get("目标网站", "智联招聘")) if task_config.get("目标网站", "智联招聘") in target_options else 0,
            disabled=disabled,
        )
        target_mode = col2.radio(
            "采集目标",
            ["指定数量简历", "按时间采集"],
            index=0 if task_config.get("采集目标", "指定数量简历") == "指定数量简历" else 1,
            horizontal=True,
            disabled=disabled,
        )
        speed_mode = col3.radio(
            "采集速度",
            ["快速采集（5-15s间隔）", "慢速采集（10-45s间隔）"],
            index=0 if str(task_config.get("采集速度", "快速采集（5-15s间隔）")).startswith("快速") else 1,
            horizontal=True,
            disabled=disabled,
        )

        col4, col5, col6 = st.columns(3)
        resume_count = int(task_config.get("简历数量") or 5)
        search_minutes = int(task_config.get("搜索时间分钟") or 60)
        per_candidate_wait_seconds = int(task_config.get("每候选人等待秒数") or 20)
        if target_mode == "指定数量简历":
            resume_count = int(col4.number_input("简历数量", min_value=1, max_value=500, value=resume_count, step=1, disabled=disabled))
            search_minutes_value = None
        else:
            search_minutes = int(col4.number_input("搜索时间（分钟）", min_value=10, max_value=900, value=search_minutes, step=10, disabled=disabled))
            search_minutes_value = search_minutes
            resume_count = settings.crawler_max_resumes_per_task
        per_candidate_wait_seconds = int(col5.number_input("每候选人等待秒数", min_value=5, max_value=180, value=per_candidate_wait_seconds, step=5, disabled=disabled))
        task_status = task_config.get("任务状态", "等待启动")
        login_state_text = "已保存" if has_login_state else "未保存"
        login_state_color = "#16A34A" if has_login_state else "#DC2626"
        col6.markdown(
            f'<div class="collect-info-item"><div class="collect-info-label">登录态 / 任务状态</div>'
            f'<div class="collect-info-value" style="font-size:13px;color:{login_state_color};">{login_state_text}</div>'
            f'<div class="collect-info-value" style="font-size:13px;">{task_status}</div></div>',
            unsafe_allow_html=True,
        )

    updated_task = {
        **task_config,
        "目标网站": target_site,
        "采集目标": target_mode,
        "简历数量": resume_count if target_mode == "指定数量简历" else None,
        "搜索时间分钟": search_minutes_value,
        "采集速度": speed_mode,
        "每候选人等待秒数": per_candidate_wait_seconds,
        "账号标识": task_config.get("账号标识") or "default",
        "间隔秒": "5-15" if speed_mode.startswith("快速") else "10-45",
    }
    if not disabled:
        st.session_state.pending_collect_task = updated_task
        runtime = get_runtime_state()
        if not runtime.get("running"):
            runtime["task_config"] = updated_task
    return updated_task


def get_task_target_count(task_config: dict) -> int:
    max_resumes = task_config.get("简历数量")
    return int(max_resumes) if max_resumes else settings.crawler_max_resumes_per_task


def run_collect_task(task_config: dict, runtime: dict | None = None) -> None:
    runtime = runtime or get_runtime_state()
    account_name = task_config.get("账号标识") or "default"
    adapter_module = importlib.reload(zhilian_adapter_module)
    adapter = adapter_module.ZhilianAdapter(account_name=account_name)
    has_login_state = adapter.state_path.exists()
    started_monotonic = time.monotonic()
    task_batch = None

    def log(message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        runtime["logs"].append(f"[{timestamp}] {message}")
        runtime["logs"] = runtime["logs"][-5000:]

    def should_pause_or_stop() -> bool:
        if runtime.get("stopped"):
            log("检测到停止请求，正在结束当前采集任务。")
            return False
        while runtime.get("paused") and not runtime.get("stopped"):
            time.sleep(0.5)
        return not runtime.get("stopped")

    try:
        runtime["running"] = True
        runtime["stopped"] = False
        runtime["paused"] = False
        runtime["task_config"] = {**task_config, "任务状态": "运行中"}
        target_count = get_task_target_count(task_config)
        with create_session() as session:
            task_batch = CrawlTaskService(session).create_task(
                platform_code="zhilian",
                task_name=f"智联采集-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                task_type="chat_attachment_resume",
                query_params=task_config,
                planned_count=target_count,
            )
            task_batch_id = task_batch.id
        log(f"当前系统版本：{APP_VERSION}。")
        log(f"采集任务启动，批次ID：{task_batch_id}。")
        log(f"任务配置：目标网站=智联招聘，使用账号={account_name}，目标数量={target_count}，采集速度={task_config.get('采集速度', '快速采集（5-15s间隔）')}。")
        if not has_login_state:
            log("未检测到登录态，正在打开智联登录窗口。")
            adapter.login_manually(wait_seconds=900, keep_open=False)
            log("登录流程完成，已保存登录态。")
        else:
            log("检测到已保存登录态，跳过登录。")

        search_minutes = task_config.get("搜索时间分钟")
        speed_mode = task_config.get("采集速度") or "快速采集（5-15s间隔）"
        min_download_interval = 5 if speed_mode.startswith("快速") else 10
        run_seconds = int(search_minutes) * 60 if search_minutes else 900
        per_candidate_wait = int(task_config.get("每候选人等待秒数") or 20)
        log(f"打开智联页面并进入聊天采集流程，目标数量：{target_count}，最长运行：{run_seconds} 秒，每候选人最多等待：{per_candidate_wait} 秒，下载间隔：{min_download_interval} 秒。")
        profile_lookup_keys = build_profile_lookup_keys("zhilian")
        start_profile_key_count = len(profile_lookup_keys)
        log(f"已加载下载前个人信息去重库：当前共有 {start_profile_key_count} 条个人信息键。")
        scanned_candidate_keys: set[str] = set()
        profile_debug_index = build_profile_debug_index("zhilian")

        def on_resume_saved(row: dict) -> None:
            if not should_pause_or_stop():
                return
            record = build_candidate_record(row)
            runtime["candidates"].append(record)
            runtime["candidates"] = runtime["candidates"][-200:]
            platform_code = row.get("platform_code", "zhilian")
            raw_json = row.get("raw_json", {}) or {}
            pre_info = raw_json.get("pre_download_candidate_info", {}) or {}
            profile_key = build_profile_dedup_key(platform_code, pre_info)
            if profile_key:
                profile_lookup_keys.add(profile_key)
                profile_name = normalize_profile_value(pre_info.get("name") or pre_info.get("姓名") or "")
                if profile_name:
                    values = profile_debug_index.setdefault(profile_name, [])
                    item = build_profile_dedup_label(pre_info)
                    if item not in values:
                        values.append(item)
            log(f"正在读取候选人信息：{record['姓名']}，年龄：{record['年龄']}，学历：{record['学历']}，求职岗位：{record['求职岗位']}，电话：{record['电话']}。")
            log(f"简历附件下载成功：{record['简历文件名']}。")

        def on_resume_skipped(row: dict) -> None:
            if not should_pause_or_stop():
                return
            raw_json = row.get("raw_json", {}) or {}
            record = build_candidate_record(row)
            skip_stage = raw_json.get("skip_stage") or "before_download_profile"
            stage_label = {
                "before_download_profile": "下载前个人信息键",
            }.get(skip_stage, skip_stage)
            runtime["skipped_count"] = int(runtime.get("skipped_count") or 0) + 1
            skipped_count = runtime["skipped_count"]
            log(f"已跳过重复候选人（{stage_label}）：{record['姓名']} / {record['学历']}，累计跳过 {skipped_count} 位。")

        def on_diagnostic(message: str) -> None:
            log(message)

        def on_download_failed(payload: dict) -> None:
            runtime["download_failed_count"] = int(runtime.get("download_failed_count") or 0) + 1
            candidate = normalize_duplicate_text(payload.get("candidate_signature") or "")[:60] or "未知候选人"
            log(
                f"附件下载失败：候选人={candidate}，"
                f"url_hash={payload.get('url_hash') or '空'}，原因={payload.get('error') or '未知错误'}。"
            )

        def should_skip_candidate_profile_with_log(info: dict, signature: str = "") -> bool:
            profile_key = build_profile_dedup_key("zhilian", info)
            profile_label = build_profile_dedup_label(info)
            scan_key = normalize_duplicate_text(signature) or profile_label
            if scan_key and scan_key not in scanned_candidate_keys:
                scanned_candidate_keys.add(scan_key)
                runtime["scanned_count"] = int(runtime.get("scanned_count") or 0) + 1
            should_skip = bool(profile_key and profile_key in profile_lookup_keys)
            profile_name = normalize_profile_value(info.get("name") or info.get("姓名") or "")
            same_name_history = profile_debug_index.get(profile_name, []) if profile_name else []
            same_name_text = "；".join(same_name_history[:5]) if same_name_history else "无"
            key_hash_text = profile_key[:12] if profile_key else "缺失"
            log(
                f"下载前个人信息重复判断：hit={should_skip}，action_skip={should_skip}，"
                f"profile={profile_label}，key={'有' if profile_key else '缺失'}，key_hash={key_hash_text}，"
                f"历史同名={same_name_text}。"
            )
            return should_skip

        collect_kwargs = {
            "target_url": "https://rd5.zhaopin.com/",
            "max_resumes": target_count,
            "wait_seconds": run_seconds,
            "per_candidate_wait_seconds": per_candidate_wait,
            "min_download_interval_seconds": min_download_interval,
            "on_resume_saved": on_resume_saved,
            "on_resume_skipped": on_resume_skipped,
            "should_continue": should_pause_or_stop,
            "on_diagnostic": on_diagnostic,
        }
        collect_signature = inspect.signature(adapter.auto_click_chat_attachment_resumes)
        log(
            "智联适配器能力检测："
            f"profile去重={'支持' if 'should_skip_candidate_profile' in collect_signature.parameters else '不支持'}。"
        )
        if "should_skip_candidate_profile" in collect_signature.parameters:
            collect_kwargs["should_skip_candidate_profile"] = should_skip_candidate_profile_with_log
        else:
            log("当前加载的智联适配器不支持下载前个人信息去重，请更新适配器后再测试去重。")
        if "on_resume_skipped" not in collect_signature.parameters:
            collect_kwargs.pop("on_resume_skipped", None)
        if "min_download_interval_seconds" not in collect_signature.parameters:
            collect_kwargs.pop("min_download_interval_seconds", None)
        if "should_continue" not in collect_signature.parameters:
            collect_kwargs.pop("should_continue", None)
        if "on_diagnostic" not in collect_signature.parameters:
            collect_kwargs.pop("on_diagnostic", None)
        if "on_download_failed" in collect_signature.parameters:
            collect_kwargs["on_download_failed"] = on_download_failed
        rows = adapter.auto_click_chat_attachment_resumes(**collect_kwargs)
        saved_ids = save_raw_resume_rows(rows, task_id=task_batch_id)
        elapsed_seconds = int(time.monotonic() - started_monotonic)
        final_failed_count = int(runtime.get("download_failed_count") or 0)
        final_scanned_count = int(runtime.get("scanned_count") or 0)
        final_status = "cancelled" if runtime.get("stopped") else "success"
        display_status = "已停止" if runtime.get("stopped") else "已完成"
        with create_session() as session:
            task = session.get(type(task_batch), task_batch_id)
            if task:
                CrawlTaskService(session).finish_task(
                    task,
                    status=final_status,
                    success_count=len(saved_ids),
                    failed_count=final_failed_count,
                )
        final_profile_key_count = get_profile_dedup_count("zhilian")
        added_profile_key_count = max(final_profile_key_count - start_profile_key_count, 0)
        log(f"本次采集新增下载前个人信息键 {added_profile_key_count} 条，当前共有 {final_profile_key_count} 条。")
        log(
            f"本次采集一共扫描了{final_scanned_count}位候选人，成功下载了{len(saved_ids)}份简历，失败了{final_failed_count}次。"
        )
        log(
            f"自动采集结束，入库记录数：{len(saved_ids)}，耗时：{elapsed_seconds} 秒。"
        )
        runtime["task_config"] = {
            **task_config,
            "任务状态": display_status,
            "已保存简历数": len(saved_ids),
            "raw_resume_ids": saved_ids,
        }
    except Exception as exc:
        if "登录窗口已关闭或登录流程已取消" in str(exc):
            log("登录窗口已关闭，任务已取消。")
            status = "cancelled"
            display_status = "已取消"
        else:
            log(f"采集任务失败：{exc}")
            status = "failed"
            display_status = "失败"
        if task_batch:
            with create_session() as session:
                task = session.get(type(task_batch), task_batch.id)
                if task:
                    CrawlTaskService(session).finish_task(
                        task,
                        status=status,
                        success_count=0,
                        failed_count=1,
                        error_message=str(exc),
                    )
        runtime["task_config"] = {**task_config, "任务状态": display_status}
    finally:
        runtime["running"] = False
        runtime["paused"] = False


def start_collect_task(task_config: dict, runtime: dict) -> None:
    task_to_run = {**task_config, "任务状态": "运行中"}
    runtime["running"] = True
    runtime["paused"] = False
    runtime["stopped"] = False
    runtime["logs"] = []
    runtime["candidates"] = []
    runtime["scanned_count"] = 0
    runtime["skipped_count"] = 0
    runtime["download_failed_count"] = 0
    runtime["task_config"] = task_to_run
    st.session_state.collect_task_logs = list(runtime.get("logs", []))
    st.session_state.collect_candidates = list(runtime.get("candidates", []))
    st.session_state.collect_running = True
    st.session_state.pending_collect_task = task_to_run
    thread = threading.Thread(target=run_collect_task, args=(task_to_run, runtime), daemon=True)
    runtime["thread"] = thread
    thread.start()


init_state()
runtime = sync_runtime_to_session()
pending_task = st.session_state.get("pending_collect_task") or default_task_config()
account_name = pending_task.get("账号标识") or "default"
adapter_module = importlib.reload(zhilian_adapter_module)
adapter = adapter_module.ZhilianAdapter(account_name=account_name)
has_login_state = adapter.state_path.exists()
start_label = "已登录开始任务" if has_login_state else "登录开始任务"

is_running = st.session_state.get("collect_running", False) or pending_task.get("任务状态") == "运行中"
if st.session_state.pop("auto_start_collect_task", False) and not runtime.get("running"):
    start_collect_task(pending_task, runtime)
    st.rerun()
pending_task = render_task_editor(pending_task, has_login_state, disabled=is_running)

b1, b2, b3, b4, b5, b6 = st.columns(6)
with b1:
    if st.button(start_label, type="primary", use_container_width=True, disabled=is_running):
        start_collect_task(pending_task, runtime)
        st.rerun()
with b2:
    pause_label = "继续任务" if runtime.get("paused") else "暂停任务"
    if st.button(pause_label, use_container_width=True, disabled=not runtime.get("running")):
        runtime["paused"] = not runtime.get("paused")
        runtime["task_config"] = {**pending_task, "任务状态": "已暂停" if runtime["paused"] else "运行中"}
        append_collect_log("任务已暂停。" if runtime["paused"] else "任务已继续。")
        st.rerun()
with b3:
    if st.button("停止任务", use_container_width=True, disabled=not runtime.get("running")):
        runtime["stopped"] = True
        runtime["paused"] = False
        runtime["task_config"] = {**pending_task, "任务状态": "正在停止"}
        append_collect_log("已请求停止任务；当前浏览器操作结束后停止。")
        st.rerun()
with b4:
    if st.button("打开简历目录", use_container_width=True):
        resume_dir = settings.attachment_dir.resolve()
        resume_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(resume_dir)
            append_collect_log(f"已打开简历目录：{resume_dir}")
        except Exception as exc:
            append_collect_log(f"打开简历目录失败：{exc}；目录路径：{resume_dir}")
            st.code(str(resume_dir))
with b5:
    if st.button("清空任务记录", use_container_width=True, disabled=runtime.get("running", False)):
        runtime["logs"] = []
        runtime["candidates"] = []
        runtime["task_config"] = default_task_config()
        sync_runtime_to_session()
        st.rerun()
with b6:
    if st.button("清空去重索引", use_container_width=True, disabled=runtime.get("running", False)):
        with create_session() as session:
            before_count = session.scalar(
                select(func.count())
                .select_from(platform_candidate_record)
                .where(platform_candidate_record.c.platform_code == "zhilian")
            ) or 0
            session.execute(delete(platform_candidate_record).where(platform_candidate_record.c.platform_code == "zhilian"))
            session.commit()
        append_collect_log(f"已清空智联下载前个人信息去重库：删除 {before_count} 条索引记录。")
        st.rerun()

title_placeholder = st.empty()
scanned_count = int(runtime.get("scanned_count") or 0)
skipped_count = int(runtime.get("skipped_count") or 0)
total_skipped_count = skipped_count
running_title = (
    f'<div style="display:flex;align-items:center;gap:14px;">'
    f'<span class="collect-running-text">任务执行中……</span>'
    f'<span class="collect-task-progress">当前任务已经扫描了{scanned_count}位候选人，其中下载前个人信息键去重跳过了{total_skipped_count}人。</span>'
    f'</div>'
    if is_running
    else ''
)
title_placeholder.markdown(f'<div class="plain-section-title"><h3>任务信息</h3>{running_title}</div>', unsafe_allow_html=True)
log_placeholder = st.empty()
render_log_html(log_placeholder)

st.markdown(
    '<div class="plain-section-title"><h3>候选人列表</h3><div class="collect-panel-stat">已经获取简历数：{}/{}</div></div>'.format(
        len(st.session_state.get("collect_candidates", [])),
        get_task_target_count(pending_task),
    ),
    unsafe_allow_html=True,
)
render_candidate_table()

if not runtime.get("running"):
    render_history_task_table()

if runtime.get("running"):
    time.sleep(1)
    st.rerun()

st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
