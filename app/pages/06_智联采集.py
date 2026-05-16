import html
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
from recruitment_assistant.storage.db import create_session, init_database
from recruitment_assistant.storage.models import RawResume
from recruitment_assistant.utils.hash_utils import text_hash

init_database()

settings = get_settings()
st.set_page_config(page_title="采集任务", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("智联招聘采集")
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
.collect-log-line { display:block; }
.collect-log-success { color:#168A45; font-weight:700; }
.collect-log-failed { color:#C73552; font-weight:700; }
.collect-log-skipped { color:#B7791F; font-weight:700; }
.collect-log-stat { color:#2563EB; font-weight:700; }
.collect-log-line strong { font-weight:800; }
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


def get_platform_collect_meta(target_site: str | None) -> dict:
    platform_meta = {
        "智联招聘": {
            "code": "zhilian",
            "name": "智联招聘",
            "task_prefix": "智联采集",
            "target_url": "https://rd5.zhaopin.com/",
            "module": zhilian_adapter_module,
            "class_name": "ZhilianAdapter",
            "login_error_tokens": ["智联自动登录未成功", "登录态不存在"],
        },
    }
    return platform_meta.get(target_site or "智联招聘", platform_meta["智联招聘"])


def default_task_config() -> dict:
    return {
        "目标网站": "智联招聘",
        "采集目标": "指定数量简历",
        "简历数量": 5,
        "搜索时间分钟": None,
        "采集速度": "快速采集（5-15s间隔）",
        "每候选人最大等待秒数": 20,
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
            "last_heartbeat_at": 0.0,
            "last_log_at": 0.0,
            "last_log_count": 0,
            "stale_warned": False,
            "ui_refresh_requested": False,
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
    task_config = runtime.get("task_config") or pending_task or default_task_config()
    if thread is not None and runtime.get("running") and not thread.is_alive():
        runtime["running"] = False
        runtime["paused"] = False
        if task_config.get("任务状态") == "运行中":
            task_config = {**task_config, "任务状态": "未知中断"}
            runtime["task_config"] = task_config
            append_collect_log("检测到后台采集线程已停止，但未返回完成状态，任务已标记为未知中断。")
        runtime["status_dirty"] = True

    now_monotonic = time.monotonic()
    last_activity_at = max(float(runtime.get("last_log_at") or 0), float(runtime.get("last_heartbeat_at") or 0))
    if runtime.get("running") and last_activity_at and now_monotonic - last_activity_at > 180 and not runtime.get("stale_warned"):
        runtime["stale_warned"] = True
        append_collect_log('任务已超过3分钟没有新日志或心跳。若浏览器窗口已消失，请点击"停止任务"后重新开始。')
        runtime["status_dirty"] = True

    task_config = runtime.get("task_config") or pending_task or default_task_config()
    current_status = task_config.get("任务状态", "等待启动")
    previous_status = runtime.get("last_synced_task_status")
    runtime["needs_status_refresh"] = bool(runtime.pop("status_dirty", False)) or previous_status != current_status
    runtime["last_synced_task_status"] = current_status
    st.session_state.collect_task_logs = list(runtime.get("logs", []))
    st.session_state.collect_candidates = list(runtime.get("candidates", []))
    st.session_state.collect_paused = runtime["paused"]
    st.session_state.collect_stopped = runtime["stopped"]
    st.session_state.collect_running = runtime["running"]
    st.session_state.pending_collect_task = task_config
    if "scanned_count" not in runtime:
        runtime["scanned_count"] = 0
    if "skipped_count" not in runtime:
        runtime["skipped_count"] = 0
    return runtime


def append_collect_log(message: str) -> None:
    normalized = normalize_collect_diagnostic(message)
    if not normalized.strip():
        return
    runtime = get_runtime_state()
    timestamp = datetime.now().strftime("%H:%M:%S")
    runtime["logs"].append(f"[{timestamp}] {normalized}")
    runtime["logs"] = runtime["logs"][-5000:]
    runtime["last_log_at"] = time.monotonic()
    runtime["last_log_count"] = len(runtime["logs"])
    st.session_state.collect_task_logs = list(runtime.get("logs", []))


def _format_log_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:120]


def _bold(text) -> str:
    text = _format_log_value(text)
    return f"**{text}**" if text else ""


def _duration_text(cost_ms: int | float | None = None, wait_ms: int | float | None = None) -> str:
    value = cost_ms if cost_ms is not None else wait_ms
    if value is None:
        return ""
    ms = int(value or 0)
    if ms >= 1000:
        return f"，耗时**{ms / 1000:.1f}秒**"
    return f"，耗时**{ms}毫秒**"


def _plain_duration_text(cost_ms: int | float | None = None, wait_ms: int | float | None = None) -> str:
    value = cost_ms if cost_ms is not None else wait_ms
    if value is None:
        return ""
    ms = int(value or 0)
    if ms >= 1000:
        return f"{ms / 1000:.1f}秒"
    return f"{ms}毫秒"


def _candidate_name(candidate: str) -> str:
    text = _format_log_value(candidate)
    if not text:
        return "候选人"
    text = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", " ", text)
    text = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", " ", text)
    text = re.sub(r"\b\d{1,2}[-/]\d{1,2}\b", " ", text)
    text = text.replace("不合适", " ")
    text = re.sub(r"\s+", " ", text).strip(" /|｜,，;；:：-—")
    if not text:
        return "候选人"
    first_part = text.split("/")[0].strip() or text.split()[0].strip() or text
    try:
        cleaned = clean_candidate_name(first_part)
    except NameError:
        cleaned = first_part
    return cleaned if cleaned and cleaned != "待识别" else first_part


def format_collect_event(
    stage: str,
    action: str = "",
    status: str = "",
    cost_ms: int | float | None = None,
    wait_ms: int | float | None = None,
    candidate: str = "",
    **fields,
) -> str:
    stage_key = f"{stage}.{action}" if action else stage
    candidate_text = _format_log_value(candidate)
    candidate_name = _candidate_name(candidate_text)
    duration = _duration_text(cost_ms, wait_ms)

    if stage_key == "task.version":
        return f"当前页面版本 {_bold(fields.get('version'))}。"
    if stage_key == "task.start":
        return f"采集任务已启动，批次编号 {_bold(fields.get('batch_id'))}。"
    if stage_key == "task.config":
        return f"目标平台 {_bold(fields.get('platform'))}，计划采集 {_bold(fields.get('target'))} 份简历，速度 {_bold(fields.get('speed'))}。"
    if stage_key == "task.performance":
        return (
            f"统计：成功候选人平均耗时 {_bold(str(fields.get('success_avg_ms') or 0) + '毫秒')}，"
            f"跳过候选人平均耗时 {_bold(str(fields.get('skipped_avg_ms') or 0) + '毫秒')}，"
            f"当前最慢环节 {_bold(fields.get('slowest_stage') or '无')}。"
        )
    if stage_key == "task.summary":
        avg_ms = fields.get("average_ms") or fields.get("avg_ms") or 0
        avg_text = f"，平均每人耗时 {_bold(str(avg_ms) + '毫秒')}" if avg_ms else ""
        return f"统计：任务完成，扫描 {_bold(fields.get('scanned'))} 人，保存 {_bold(fields.get('saved'))} 份，跳过 {_bold(fields.get('skipped'))} 人，失败 {_bold(fields.get('failed'))} 人{duration}{avg_text}。"
    if stage_key == "task.finish":
        return f"任务状态 {_bold(status)}，共保存 {_bold(fields.get('saved'))} 份简历{duration}。"
    if stage_key == "task.fail":
        return f"失败：采集任务失败，原因：{_bold(fields.get('reason'))}。"
    if stage_key == "task.cancel":
        return f"警告：任务已取消，原因：{_bold(fields.get('reason'))}。"

    if stage_key == "auth.check":
        return "登录状态正常，可以开始采集。" if status == "ready" else "警告：未检测到登录状态，需要重新登录。"
    if stage_key == "auth.login":
        return f"登录已完成{duration}。"
    if stage_key == "collect.open_chat":
        return "正在打开招聘平台聊天页面。" if status == "start" else f"聊天页面已打开，开始采集 {_bold(fields.get('target'))} 份简历。"
    if stage_key == "collect.finish":
        return f"本轮采集流程结束，已保存 {_bold(fields.get('saved'))} 份简历。"
    if stage_key == "dedup.load_index":
        return f"已加载去重库，强匹配 {_bold(fields.get('strong_keys'))} 条，弱匹配 {_bold(fields.get('weak_keys'))} 条。"
    if stage_key == "dedup.index_delta":
        return f"去重库已更新，新增 {_bold(fields.get('added'))} 条，当前共 {_bold(fields.get('total'))} 条。"
    if stage_key == "adapter.capability":
        return "采集器准备完成，支持下载前个人信息去重。"

    if stage_key == "candidate.click":
        return f"正在查看候选人 {_bold(candidate_name)}。"
    if stage_key == "profile.extract":
        if str(fields.get("duplicate")).lower() == "true":
            return f"跳过：候选人 {_bold(candidate_text)} 已在去重库中。"
        return f"已读取候选人 {_bold(candidate_text)} 的基础信息。"
    if stage_key == "dedup.check_profile":
        if status == "skip":
            return f"跳过：候选人 {_bold(candidate_text)} 与历史记录重复。"
        return ""
    if stage_key == "candidate.skip":
        return f"跳过：候选人 {_bold(candidate_text)}，原因：{_bold(fields.get('reason'))}。"
    if stage_key == "candidate.violation_dialog":
        return f"警告：候选人 {_bold(candidate_name)} 触发平台限制提示，已放弃本次操作。"
    if stage_key == "candidate.summary":
        if status == "success":
            return ""
        if fields.get("reason"):
            return f"跳过：候选人 {_bold(candidate_text)}，原因：{_bold(fields.get('reason'))}{duration}。"
        return ""

    if stage_key == "attachment.request_wait":
        if status == "ready":
            return f"候选人 {_bold(candidate_text)} 已同意提供附件简历。"
        return f"警告：候选人 {_bold(candidate_text)} 已索要附件，但暂未即时提供。"
    if stage_key == "attachment.capture":
        if fields.get("reason") == "requested_attachment_not_ready_fast_skip":
            return ""
        return f"警告：候选人 {_bold(candidate_text)} 未捕获到可下载附件。"
    if stage_key == "attachment.browser_download":
        return f"已接收到 {_bold(candidate_name)} 的附件文件 {_bold(fields.get('filename'))}。"
    if stage_key == "profile.read_saved":
        return f"保存成功：已读取 {_bold(candidate_text)}，岗位 {_bold(fields.get('job'))}，电话 {_bold(fields.get('phone'))}。"
    if stage_key == "attachment.save":
        return f"保存成功：候选人 {_bold(candidate_text)} 的简历已保存为 {_bold(fields.get('file'))}{duration}。"
    if stage_key in {"attachment.request_button", "attachment.view_button", "attachment.queue_url", "candidate.scan", "candidate.scan_round", "candidate.detail_switch"}:
        return ""

    if status in {"failed", "error", "missing", "timeout"}:
        return f"失败：{_bold(candidate_text or stage_key)} 操作未完成{duration}。"
    if status in {"skipped", "skip", "ignored"}:
        return f"跳过：{_bold(candidate_text or stage_key)}。"
    return ""


def normalize_collect_diagnostic(payload) -> str:
    if isinstance(payload, dict):
        stage = str(payload.get("stage") or "diagnostic")
        action = str(payload.get("action") or "")
        status = str(payload.get("status") or "")
        cost_ms = payload.get("cost_ms", payload.get("duration_ms"))
        wait_ms = payload.get("wait_ms")
        candidate = str(payload.get("candidate") or "")
        fields = {
            key: value
            for key, value in payload.items()
            if key not in {"stage", "action", "status", "cost_ms", "duration_ms", "wait_ms", "candidate"}
        }
        return format_collect_event(stage, action, status, cost_ms, wait_ms, candidate, **fields)
    return str(payload)


def classify_collect_log_line(line: str) -> str:
    if any(token in line for token in ["失败：", "警告：", "放弃", "取消"]):
        return "collect-log-failed"
    if any(token in line for token in ["跳过：", "忽略", "去重", "重复"]):
        return "collect-log-skipped"
    if "保存成功：" in line:
        return "collect-log-success"
    if "统计：" in line:
        return "collect-log-stat"
    return ""


def collect_log_line_style(css_class: str) -> str:
    base = "display:block;white-space:pre-wrap;word-break:break-word;margin-bottom:4px;"
    if css_class == "collect-log-success":
        return base + "color:#168A45!important;font-weight:600!important;"
    if css_class == "collect-log-failed":
        return base + "color:#C73552!important;font-weight:600!important;"
    if css_class == "collect-log-skipped":
        return base + "color:#B7791F!important;font-weight:600!important;"
    if css_class == "collect-log-stat":
        return base + "color:#2563EB!important;font-weight:600!important;"
    return base + "color:#1F2937!important;font-weight:400!important;"


def _render_log_markup(line: str) -> str:
    escaped = html.escape(line)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def build_collect_log_body(logs: list[str]) -> str:
    html_lines = []
    for line in logs[-300:]:
        if not line or not line.strip():
            continue
        css_class = classify_collect_log_line(line)
        style_attr = collect_log_line_style(css_class)
        class_attr = f"collect-log-line {css_class}" if css_class else "collect-log-line"
        html_lines.append(f'<div class="{class_attr}" style="{style_attr}">{_render_log_markup(line)}</div>')
    return "".join(html_lines) if html_lines else '<div class="collect-empty" style="color:#94A3B8!important;font-size:13px;">暂无任务输出。任务启动后将自动显示最新日志。</div>'


def render_log_html(container=None) -> None:
    body = build_collect_log_body(st.session_state.get("collect_task_logs", []))
    target = container or st
    target.markdown(f'<div id="collect-log-box" class="collect-log-box" data-collect-log-box="1">{body}</div>', unsafe_allow_html=True)


def render_live_log_panel(refresh_seconds: float = 1.5) -> None:
    live_runtime = get_runtime_state()
    logs = live_runtime.get("logs") or st.session_state.get("collect_task_logs", [])
    body = build_collect_log_body(list(logs))
    components.html(
        f"""
<!doctype html>
<html>
<head>
<style>
body {{ margin:0; background:#fff; color:#1F2937; font:13px/1.55 Consolas, Monaco, monospace; }}
#collect-log-box {{ height:240px; overflow-y:auto; background:#FFFFFF; color:#1F2937; border:1px solid #E5EAF2; border-radius:16px; padding:14px; white-space:pre-wrap; box-sizing:border-box; }}
.collect-log-line {{ display:block; }}
.collect-log-success {{ color:#168A45; font-weight:700; }}
.collect-log-failed {{ color:#C73552; font-weight:700; }}
.collect-log-skipped {{ color:#B7791F; font-weight:700; }}
.collect-log-stat {{ color:#2563EB; font-weight:700; }}
.collect-log-line strong {{ font-weight:800; }}
.collect-empty {{ color:#94A3B8; font-size:13px; }}
</style>
</head>
<body>
<div id="collect-log-box">{body}</div>
<script>
const box = document.getElementById('collect-log-box');
box.scrollTop = box.scrollHeight;
</script>
</body>
</html>
""",
        height=252,
    )


if hasattr(st, "fragment"):
    render_auto_log_panel = st.fragment(run_every=1.5)(render_live_log_panel)
else:
    render_auto_log_panel = render_live_log_panel


def render_candidate_table(container=None) -> None:
    target = container or st
    candidates = runtime.get("candidates", st.session_state.get("collect_candidates", []))
    if candidates:
        target.dataframe(candidates, use_container_width=True, hide_index=True)
    else:
        target.markdown('<div class="collect-empty">暂无候选人。简历下载成功后会自动出现在这里。</div>', unsafe_allow_html=True)


def render_live_status_and_candidates_panel(task_config: dict) -> None:
    if runtime.pop("ui_refresh_requested", False):
        sync_runtime_to_session()
        try:
            st.rerun(scope="app")
        except TypeError:
            st.rerun()
    scanned_count = int(runtime.get("scanned_count") or 0)
    skipped_count = int(runtime.get("skipped_count") or 0)
    candidates = list(runtime.get("candidates", []))
    target_count = get_task_target_count(task_config)
    is_task_running = bool(runtime.get("running")) or (runtime.get("task_config") or {}).get("任务状态") == "运行中"
    running_title = (
        f'<div style="display:flex;align-items:center;gap:14px;">'
        f'<span class="collect-running-text">任务执行中……</span>'
        f'<span class="collect-task-progress">当前任务已经扫描了{scanned_count}位候选人，累计去重/无附件跳过了{skipped_count}人。</span>'
        f'</div>'
        if is_task_running
        else ''
    )
    st.markdown(f'<div class="plain-section-title"><h3>任务信息</h3>{running_title}</div>', unsafe_allow_html=True)
    render_live_log_panel()
    st.markdown(
        '<div class="plain-section-title"><h3>候选人列表</h3><div class="collect-panel-stat">已经获取简历数：{}/{}</div></div>'.format(
            len(candidates),
            target_count,
        ),
        unsafe_allow_html=True,
    )
    render_candidate_table()


if hasattr(st, "fragment"):
    render_auto_status_and_candidates_panel = st.fragment(run_every=1.5)(render_live_status_and_candidates_panel)
else:
    render_auto_status_and_candidates_panel = render_live_status_and_candidates_panel

def render_history_task_table(platform_code: str | None = None, platform_name: str | None = None) -> None:
    try:
        with create_session() as session:
            task_service = CrawlTaskService(session)
            task_rows = task_service.list_tasks(limit=50, platform_code=platform_code)
            success_task_count, success_resume_count = task_service.success_summary(platform_code=platform_code)
    except Exception as exc:
        st.warning(f"历史批次任务读取失败：{exc}")
        task_rows = []
        success_task_count = 0
        success_resume_count = 0

    title = f"{platform_name or '全部平台'}历史批次任务列表"
    st.markdown(
        '<div class="plain-section-title"><h3>{}</h3><div class="collect-panel-stat">已成功执行{}次任务，共获取了{}份简历。</div></div>'.format(
            title,
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
                "目标网站": {"zhilian": "智联招聘", "boss": "BOSS直聘"}.get(row.platform_code, row.platform_code),
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


def build_profile_weak_dedup_key(platform_code: str, info: dict) -> str:
    name = normalize_profile_value(info.get("name") or info.get("姓名") or "")
    education = normalize_profile_education(info.get("education") or info.get("highest_degree") or info.get("学历") or "")
    if not name or not education:
        return ""
    return text_hash("|".join([platform_code, "profile_name_education", name, education])) or ""


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


def build_profile_weak_lookup_keys(platform_code: str = "zhilian") -> set[str]:
    keys: set[str] = set()
    with create_session() as session:
        rows = session.execute(
            select(platform_candidate_record.c.name, RawResume.raw_json)
            .select_from(platform_candidate_record.outerjoin(RawResume, platform_candidate_record.c.raw_resume_id == RawResume.id))
            .where(platform_candidate_record.c.platform_code == platform_code)
        ).mappings()
        for row in rows:
            raw_json = row.get("raw_json") or {}
            info = raw_json.get("pre_download_candidate_info") or raw_json.get("candidate_info") or {}
            if not info and row.get("name"):
                info = {"name": row.get("name")}
            weak_key = build_profile_weak_dedup_key(platform_code, info)
            if weak_key:
                keys.add(weak_key)
    return keys


def get_profile_dedup_count(platform_code: str = "zhilian") -> int:
    return len(build_profile_lookup_keys(platform_code))


def upsert_profile_dedup_record(session, row: dict, profile_key: str, raw_resume_id: int, task_id: int | None = None) -> None:
    raw_json = row.get("raw_json", {}) or {}
    info = raw_json.get("pre_download_candidate_info", {}) or {}
    candidate_info = raw_json.get("candidate_info", {}) or {}
    attachment = raw_json.get("attachment", {}) or {}
    merged_info = info or candidate_info
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
                candidate_signature=existing.get("candidate_signature") or raw_json.get("candidate_signature"),
                job_title=existing.get("job_title") or candidate_info.get("job_title"),
                phone=existing.get("phone") or candidate_info.get("phone"),
                resume_file_name=existing.get("resume_file_name") or attachment.get("file_name"),
                content_hash=existing.get("content_hash") or row.get("content_hash"),
                raw_resume_id=existing.get("raw_resume_id") or raw_resume_id,
                task_id=existing.get("task_id") or task_id,
            )
        )
        session.commit()
        return
    session.execute(
        platform_candidate_record.insert().values(
            platform_code=row.get("platform_code", "zhilian"),
            target_site={"zhilian": "智联招聘", "boss": "BOSS直聘"}.get(row.get("platform_code", "zhilian"), row.get("platform_code", "zhilian")),
            candidate_key=profile_key,
            candidate_signature=raw_json.get("candidate_signature"),
            name=normalize_profile_value(merged_info.get("name") or merged_info.get("姓名") or "") or None,
            gender=candidate_info.get("gender") if candidate_info.get("gender") != "待识别" else None,
            job_title=candidate_info.get("job_title") if candidate_info.get("job_title") != "待识别" else None,
            phone=candidate_info.get("phone") if candidate_info.get("phone") != "待识别" else None,
            resume_file_name=attachment.get("file_name"),
            source_url=row.get("source_url"),
            content_hash=row.get("content_hash"),
            raw_resume_id=raw_resume_id,
            task_id=task_id,
        )
    )
    session.commit()


def build_profile_debug_index(platform_code: str = "zhilian") -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    with create_session() as session:
        rows = session.execute(
            select(
                platform_candidate_record.c.name,
                platform_candidate_record.c.candidate_key,
                platform_candidate_record.c.raw_resume_id,
                platform_candidate_record.c.task_id,
                RawResume.raw_json,
            )
            .select_from(platform_candidate_record.outerjoin(RawResume, platform_candidate_record.c.raw_resume_id == RawResume.id))
            .where(platform_candidate_record.c.platform_code == platform_code)
        ).mappings()
        for row in rows:
            raw_json = row.get("raw_json") or {}
            info = raw_json.get("pre_download_candidate_info") or raw_json.get("candidate_info") or {}
            name = normalize_profile_value(info.get("name") or info.get("姓名") or row.get("name") or "")
            if not name:
                continue
            age = normalize_profile_age(info.get("age") or info.get("年龄") or "") or "年龄缺失"
            education = normalize_profile_education(info.get("education") or info.get("highest_degree") or info.get("学历") or "") or "学历缺失"
            values = index.setdefault(name, [])
            key_text = str(row.get("candidate_key") or "")[:12]
            item = f"{name}/{age}/{education}/key:{key_text}/task:{row.get('task_id') or '-'}"
            if item not in values:
                values.append(item)
    return index


def clean_candidate_name(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"^(?:\d+|[一二三四五六七八九十]+)[\.、\)）\s]+", "", text)
    if not text or text == "待识别":
        return "待识别"
    stop_tokens = [
        "沟通", "聊天", "附件", "简历", "查看", "下载", "电话", "手机号", "求职", "职位", "岗位",
        "未读", "已读", "在线", "打招呼", "要附件", "本科", "专科", "硕士", "博士", "经验",
        "设置备注", "不合适", "已向对方要附件简历", "待识别",
    ]
    candidates = []
    for part in re.split(r"[｜|/\\,，;；:：\n\r\t ]+", text):
        part = re.sub(r"^(?:\d+|[一二三四五六七八九十]+)[\.、\)）\s]+", "", part)
        part = part.strip(" ·-—_()（）[]【】")
        if not part or any(token in part for token in stop_tokens):
            continue
        if re.fullmatch(r"[\u4e00-\u9fa5]{1,3}(?:先生|女士)", part):
            candidates.append(part)
            continue
        if re.fullmatch(r"男|女|男性|女性", part) or re.search(r"\d|岁|年", part):
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
    "架构", "实施", "运维", "财务", "出纳", "法务", "分析师", "需求分析", "策划", "企划", "营销", "品牌",
    "市场", "编导", "导演", "剪辑", "摄像", "摄影", "视频", "深度学习", "图像识别", "图像处理", "机器视觉",
    "Golang", "Go开发", "后台开发", "后端开发", "玩具设计", "动画设计", "商业/经营分析", "经营分析", "质量管理",
    "质量测试", "移动产品经理", "美术设计师", "视觉设计", "电气工程师", "电商运营", "国内电商运营",
]
COMPANY_NOISE_TOKENS = ["有限公司", "分公司", "集团", "科技", "公司", "企业", "中心", "事业部", "工作室", "系统集成"]
JOB_SECTION_NOISE_TOKENS = ["工作经历", "项目经历", "教育经历", "实习经历", "培训经历", "校园经历"]
JOB_DIRECTION_NOISE_TOKENS = ["AI方向", "ai方向", "A I方向", "方向"]


def extract_core_job_title(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" -—｜|:：")
    if not text:
        return ""
    text = re.sub(r"[（(][^（）()]{0,24}方向[）)]", "", text)
    text = re.sub(r"^(求职岗位|求职职位|应聘岗位|应聘职位|期望职位|期望岗位|目标职位|目标岗位|职位|岗位)[:： ]*", "", text).strip(" -—｜|")
    text = re.sub(r"^(" + "|".join(JOB_SECTION_NOISE_TOKENS) + r")\s*[（(]?\s*\d+(?:\.\d+)?\s*年\s*[）)]?\s*", "", text).strip(" -—｜|")
    text = re.split(r"电话|手机|性别|姓名|男|女|\d{2,}|岁|经验|本科|专科|硕士|博士|学历|在线|沟通|附件|简历", text)[0].strip(" -—｜|")
    parts = [part.strip(" -—｜|/\\,，;；:：()（）[]【】") for part in re.split(r"[·•|｜/\\,，;；\n\r\t]+", text)]
    candidates = [part for part in parts if part]
    candidates.append(text)
    for part in reversed(candidates):
        if not (2 <= len(part) <= 40):
            continue
        if any(token in part for token in COMPANY_NOISE_TOKENS + JOB_SECTION_NOISE_TOKENS + JOB_DIRECTION_NOISE_TOKENS):
            continue
        if any(hint.lower() in part.lower() for hint in JOB_TITLE_HINTS):
            return part
    return text if 2 <= len(text) <= 40 and not any(token in text for token in COMPANY_NOISE_TOKENS + JOB_SECTION_NOISE_TOKENS + JOB_DIRECTION_NOISE_TOKENS) else ""


def clean_job_title(value: str, candidate_name: str = "") -> str:
    text = extract_core_job_title(value)
    if not text or text == "待识别":
        return "待识别"
    has_job_hint = any(hint.lower() in text.lower() for hint in JOB_TITLE_HINTS)
    if text == candidate_name or (not has_job_hint and is_probably_person_name(text)):
        return "待识别"
    if any(token in text for token in ["电话", "手机", "附件", "简历", "聊天", "沟通", "未读", "已读", "设置备注", "不合适"]):
        return "待识别"
    if any(token in text for token in COMPANY_NOISE_TOKENS + JOB_SECTION_NOISE_TOKENS + JOB_DIRECTION_NOISE_TOKENS):
        return "待识别"
    return text if 2 <= len(text) <= 40 else "待识别"


def build_candidate_record(row: dict) -> dict:
    raw_json = row.get("raw_json", {})
    info = raw_json.get("candidate_info", {}) or {}
    attachment = raw_json.get("attachment", {}) or {}
    file_path = attachment.get("file_path") or ""
    signature_name, _ = parse_candidate_signature(raw_json.get("candidate_signature") or "")
    info_name = clean_candidate_name(info.get("name") or "")
    candidate_name = signature_name if is_unknown_or_noise(info_name) else info_name
    job_from_info = clean_job_title(info.get("job_title") or "", candidate_name)
    job_title = job_from_info
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


def render_task_editor(task_config: dict, login_states: dict[str, bool], disabled: bool = False) -> dict:
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
            key="collect_target_site",
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
        per_candidate_wait_seconds = int(task_config.get("每候选人最大等待秒数") or task_config.get("每候选人等待秒数") or 20)
        if target_mode == "指定数量简历":
            resume_count = int(col4.number_input("简历数量", min_value=1, max_value=500, value=resume_count, step=1, disabled=disabled))
            search_minutes_value = None
        else:
            search_minutes = int(col4.number_input("搜索时间（分钟）", min_value=10, max_value=900, value=search_minutes, step=10, disabled=disabled))
            search_minutes_value = search_minutes
            resume_count = settings.crawler_max_resumes_per_task
        per_candidate_wait_seconds = int(col5.number_input("每候选人最大等待秒数", min_value=5, max_value=180, value=per_candidate_wait_seconds, step=5, disabled=disabled))
        task_status = task_config.get("任务状态", "等待启动")
        has_login_state = bool(login_states.get(target_site))
        login_state_text = "已登录" if has_login_state else "未登录 / 请登录"
        login_state_color = "#16A34A" if has_login_state else "#DC2626"
        login_state_bg = "#E6F4EA" if has_login_state else "#FEE2E2"
        login_state_border = "#BBF7D0" if has_login_state else "#FECACA"
        zhilian_state = "已保存" if login_states.get("智联招聘") else "未保存"
        col6.markdown(
            f'<div class="collect-info-item" style="border-color:{login_state_border};background:{login_state_bg};">'
            f'<div class="collect-info-label">当前平台登录态 / 任务状态</div>'
            f'<div class="collect-info-value" style="font-size:13px;color:{login_state_color};">{target_site}：{login_state_text}</div>'
            f'<div class="collect-info-value" style="font-size:12px;color:#6B7280;">智联招聘：{zhilian_state}</div>'
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
        "每候选人最大等待秒数": per_candidate_wait_seconds,
        "账号标识": task_config.get("账号标识") or "default",
        "间隔秒": "5-15" if speed_mode.startswith("快速") else "10-45",
    }
    if not disabled:
        st.session_state.pending_collect_task = updated_task
        runtime = get_runtime_state()
        if not runtime.get("running"):
            runtime["task_config"] = updated_task
    return updated_task


def check_login_state(adapter, verify: bool = False) -> bool:
    user_data_dir = getattr(adapter, "user_data_dir", adapter.state_path.with_name(f"{adapter.state_path.stem}_profile"))
    login_artifact_saved = adapter.state_path.exists() or user_data_dir.exists()
    login_status_key = f"{getattr(adapter, 'platform_code', 'platform')}_login_status_{getattr(adapter, 'account_name', 'default') or 'default'}"
    if not login_artifact_saved:
        st.session_state[login_status_key] = "未保存"
        return False
    current_status = st.session_state.get(login_status_key)
    if current_status in {"未登录或已失效", "已保存但已失效", "未保存"}:
        return False
    if current_status == "已登录" and not verify:
        return True
    if not verify:
        st.session_state.setdefault(login_status_key, "已保存，待验证")
        return False
    try:
        is_logged_in = bool(adapter.is_logged_in(headless=True))
    except Exception as exc:
        append_collect_log(f"登录态校验失败：{exc}")
        st.session_state[login_status_key] = "已保存，待验证"
        return False
    st.session_state[login_status_key] = "已登录" if is_logged_in else "已保存但已失效"
    return is_logged_in




def create_platform_adapter(target_site: str | None, account_name: str):
    platform_meta = get_platform_collect_meta(target_site)
    adapter_module = importlib.reload(platform_meta["module"])
    adapter = getattr(adapter_module, platform_meta["class_name"])(account_name=account_name)
    return platform_meta, adapter


def get_platform_login_states(account_name: str) -> dict[str, bool]:
    states = {}
    for target_site in ["智联招聘"]:
        try:
            _, adapter = create_platform_adapter(target_site, account_name)
            states[target_site] = check_login_state(adapter, verify=False)
        except Exception:
            states[target_site] = False
    return states


def get_task_target_count(task_config: dict) -> int:
    max_resumes = task_config.get("简历数量")
    return int(max_resumes) if max_resumes else settings.crawler_max_resumes_per_task


def run_collect_task(task_config: dict, runtime: dict | None = None) -> None:
    runtime = runtime or get_runtime_state()
    account_name = task_config.get("账号标识") or "default"
    platform_meta = get_platform_collect_meta(task_config.get("目标网站"))
    platform_code = platform_meta["code"]
    platform_name = platform_meta["name"]
    adapter_module = importlib.reload(platform_meta["module"])
    adapter = getattr(adapter_module, platform_meta["class_name"])(account_name=account_name)
    has_login_state = check_login_state(adapter, verify=False)
    started_monotonic = time.monotonic()
    task_batch = None

    def log(message: str) -> None:
        normalized = normalize_collect_diagnostic(message)
        if not normalized.strip():
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        runtime["logs"].append(f"[{timestamp}] {normalized}")
        runtime["logs"] = runtime["logs"][-5000:]
        runtime["last_log_at"] = time.monotonic()
        runtime["last_log_count"] = len(runtime["logs"])

    def log_event(
        stage: str,
        action: str = "",
        status: str = "",
        cost_ms: int | float | None = None,
        wait_ms: int | float | None = None,
        candidate: str = "",
        **fields,
    ) -> None:
        log(format_collect_event(stage, action, status, cost_ms, wait_ms, candidate, **fields))


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
        runtime["performance_candidates"] = []
        runtime["performance_stages"] = []
        runtime["task_config"] = {**task_config, "任务状态": "运行中"}
        target_count = get_task_target_count(task_config)
        with create_session() as session:
            task_batch = CrawlTaskService(session).create_task(
                platform_code=platform_code,
                task_name=f"{platform_meta['task_prefix']}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                task_type="chat_attachment_resume",
                query_params=task_config,
                planned_count=target_count,
            )
            task_batch_id = task_batch.id
        log_event("task", "version", "ok", version=APP_VERSION)
        log_event("task", "start", "running", batch_id=task_batch_id)
        log_event(
            "task",
            "config",
            "ok",
            platform=platform_name,
            account=account_name,
            target=target_count,
            speed=task_config.get('采集速度', '快速采集（5-15s间隔）'),
        )
        if not has_login_state:
            log_event("auth", "check", "missing")
            login_started = time.monotonic()
            adapter.login_manually(wait_seconds=900, keep_open=False)
            st.session_state[f"{platform_code}_login_status_{account_name}"] = "已登录"
            login_cost_ms = int((time.monotonic() - login_started) * 1000)
            log_event("auth", "login", "saved", cost_ms=login_cost_ms, wait_ms=login_cost_ms, wait_limit_ms=900000)
        else:
            log_event("auth", "check", "ready")


        search_minutes = task_config.get("搜索时间分钟")
        speed_mode = task_config.get("采集速度") or "快速采集（5-15s间隔）"
        min_download_interval = 3 if speed_mode.startswith("快速") else 8
        run_seconds = int(search_minutes) * 60 if search_minutes else 900
        per_candidate_wait = int(task_config.get("每候选人最大等待秒数") or task_config.get("每候选人等待秒数") or 20)
        log_event(
            "collect",
            "open_chat",
            "start",
            target=target_count,
            max_run_ms=run_seconds * 1000,
            per_candidate_wait_ms=per_candidate_wait * 1000,
            download_interval_ms=min_download_interval * 1000,
        )
        profile_lookup_keys = build_profile_lookup_keys(platform_code)
        profile_weak_lookup_keys = build_profile_weak_lookup_keys(platform_code)
        start_profile_key_count = len(profile_lookup_keys)
        log_event(
            "dedup",
            "load_index",
            "ok",
            strong_keys=start_profile_key_count,
            weak_keys=len(profile_weak_lookup_keys),
        )

        scanned_candidate_keys: set[str] = set()
        skipped_candidate_keys: set[str] = set()
        saved_candidate_keys: set[str] = set()
        profile_debug_index = build_profile_debug_index(platform_code)

        def candidate_skip_key(row: dict, record: dict | None = None) -> str:
            raw_json = row.get("raw_json", {}) or {}
            signature = normalize_duplicate_text(raw_json.get("candidate_signature") or "")
            if signature:
                signature_head = re.sub(r"^(?:\d+|[一二三四五六七八九十]+)[\.、\)）\s]+", "", signature.splitlines()[0]).strip()
                sig_name, _ = clean_candidate_signature(signature_head)
                sig_name = normalize_profile_value(sig_name or "")
                age_match = re.search(r"(\d{2})\s*岁", signature)
                education_match = re.search(r"(博士|硕士|本科|大专|专科|高中|中专)", signature)
                if sig_name:
                    parts = [sig_name]
                    if age_match:
                        parts.append(age_match.group(1))
                    if education_match:
                        parts.append(education_match.group(1))
                    return "signature_identity:" + "|".join(parts)
            pre_info = raw_json.get("pre_download_candidate_info", {}) or {}
            candidate_info = raw_json.get("candidate_info", {}) or {}
            profile_key = build_profile_dedup_key(platform_code, pre_info or candidate_info)
            if profile_key:
                return f"profile:{profile_key}"
            weak_key = build_profile_weak_dedup_key(platform_code, pre_info or candidate_info)
            if weak_key:
                return f"profile_weak:{weak_key}"
            if record is None:
                record = build_candidate_record(row)
            name = normalize_profile_value(record.get("姓名") or "")
            age = normalize_profile_age(record.get("年龄") or "")
            education = normalize_profile_education(record.get("学历") or "")
            if name and (age or education):
                return "display:" + "|".join([name, age, education])
            return f"signature:{signature[:120]}" if signature else ""

        def on_resume_saved(row: dict) -> None:
            if not should_pause_or_stop():
                return
            record = build_candidate_record(row)
            skip_key = candidate_skip_key(row, record)
            if skip_key:
                saved_candidate_keys.add(skip_key)
                skipped_candidate_keys.discard(skip_key)
            runtime["candidates"].append(record)
            runtime["candidates"] = runtime["candidates"][-200:]
            platform_code = row.get("platform_code", "zhilian")
            raw_json = row.get("raw_json", {}) or {}
            pre_info = raw_json.get("pre_download_candidate_info", {}) or {}
            profile_key = build_profile_dedup_key(platform_code, pre_info)
            if profile_key:
                profile_lookup_keys.add(profile_key)
                weak_key = build_profile_weak_dedup_key(platform_code, pre_info)
                if weak_key:
                    profile_weak_lookup_keys.add(weak_key)
                profile_name = normalize_profile_value(pre_info.get("name") or pre_info.get("姓名") or "")
                if profile_name:
                    values = profile_debug_index.setdefault(profile_name, [])
                    item = build_profile_dedup_label(pre_info)
                    if item not in values:
                        values.append(item)
            log_event(
                "profile",
                "read_saved",
                "ok",
                candidate=f"{record['姓名']}/{record['年龄']}/{record['学历']}",
                job=record["求职岗位"],
                phone=record["电话"],
            )


        def on_resume_skipped(row: dict) -> None:
            if not should_pause_or_stop():
                return
            raw_json = row.get("raw_json", {}) or {}
            record = build_candidate_record(row)
            skip_stage = raw_json.get("skip_stage") or "before_download_profile"
            skip_key = candidate_skip_key(row, record)
            if skip_key and skip_key in saved_candidate_keys:
                log_event("skip", "duplicate_saved", "ignored", candidate=f"{record['姓名']}/{record['学历']}")
                return

            if skip_key and skip_key in skipped_candidate_keys:
                return
            if skip_key:
                skipped_candidate_keys.add(skip_key)
            stage_label = {
                "before_download_profile": "下载前个人信息键",
                "before_click_signature": "点击前签名",
                "request_attachment_disabled": "无可下载附件",
                "requested_attachment_not_ready": "已索要附件但未即时提供",
                "attachment_url_not_captured": "附件链接未捕获",
                "duplicate_content_hash": "疑似重复附件内容",
                "violation_candidate_dialog": "违规候选人警告",
                "candidate_detail_not_switched": "候选人详情未切换",
            }.get(skip_stage, skip_stage)
            runtime["skipped_count"] = int(runtime.get("skipped_count") or 0) + 1
            skipped_count = runtime["skipped_count"]
            log_event(
                "candidate",
                "skip",
                "skipped",
                candidate=f"{record['姓名']}/{record['学历']}",
                reason=stage_label,
                skipped_total=skipped_count,
            )


        def on_diagnostic(message) -> None:
            if isinstance(message, dict):
                stage = str(message.get("stage") or "")
                action = str(message.get("action") or "")
                status = str(message.get("status") or "")
                cost_ms = message.get("cost_ms")
                wait_ms = message.get("wait_ms")
                if stage == "candidate" and action == "summary" and cost_ms is not None:
                    runtime.setdefault("performance_candidates", []).append({
                        "status": status,
                        "cost_ms": int(cost_ms or 0),
                        "wait_ms": int(wait_ms or 0),
                        "candidate": message.get("candidate") or "",
                    })
                if cost_ms is not None:
                    runtime.setdefault("performance_stages", []).append({
                        "stage": f"{stage}.{action}" if action else stage,
                        "status": status,
                        "cost_ms": int(cost_ms or 0),
                        "wait_ms": int(wait_ms or 0),
                        "candidate": message.get("candidate") or "",
                    })
            log(message)

        def on_download_failed(payload: dict) -> None:
            runtime["download_failed_count"] = int(runtime.get("download_failed_count") or 0) + 1
            candidate = normalize_duplicate_text(payload.get("candidate_signature") or "")[:60] or "未知候选人"
            log_event(
                "attachment",
                "download",
                "failed",
                candidate=candidate,
                url_hash=payload.get("url_hash") or "空",
                reason=payload.get("error") or "未知错误",
            )


        def should_skip_candidate_profile_with_log(info: dict, signature: str = "") -> bool:
            check_started = time.monotonic()
            profile_key = build_profile_dedup_key(platform_code, info)
            weak_profile_key = build_profile_weak_dedup_key(platform_code, info)
            profile_label = build_profile_dedup_label(info)
            scan_key = normalize_duplicate_text(signature) or profile_label
            if scan_key and scan_key not in scanned_candidate_keys:
                scanned_candidate_keys.add(scan_key)
                runtime["scanned_count"] = int(runtime.get("scanned_count") or 0) + 1
            strong_hit = bool(profile_key and profile_key in profile_lookup_keys)
            weak_hit = bool(not strong_hit and weak_profile_key and weak_profile_key in profile_weak_lookup_keys)
            should_skip = strong_hit or weak_hit
            profile_name = normalize_profile_value(info.get("name") or info.get("姓名") or "")
            same_name_history = profile_debug_index.get(profile_name, []) if profile_name else []
            same_name_text = "；".join(same_name_history[:3]) if same_name_history and should_skip else ""
            key_hash_text = profile_key[:12] if profile_key else "缺失"
            weak_key_hash_text = weak_profile_key[:12] if weak_profile_key else "缺失"
            log_event(
                "dedup",
                "check_profile",
                "skip" if should_skip else "pass",
                cost_ms=(time.monotonic() - check_started) * 1000,
                candidate=profile_label,
                strong_hit=strong_hit,
                weak_hit=weak_hit,
                key_hash=key_hash_text,
                weak_key_hash=weak_key_hash_text,
                same_name=same_name_text,
            )
            return should_skip


        collect_kwargs = {
            "target_url": platform_meta["target_url"],
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
        log_event(
            "adapter",
            "capability",
            "ok",
            profile_dedup="支持" if "should_skip_candidate_profile" in collect_signature.parameters else "不支持",
        )

        if "should_skip_candidate_profile" in collect_signature.parameters:
            collect_kwargs["should_skip_candidate_profile"] = should_skip_candidate_profile_with_log
        else:
            log_event("adapter", "capability", "missing", profile_dedup="不支持")

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
        def run_auto_collect_with_login_recovery() -> list[dict]:
            try:
                return adapter.auto_click_chat_attachment_resumes(**collect_kwargs)
            except RuntimeError as exc:
                message = str(exc)
                if not any(token in message for token in platform_meta["login_error_tokens"]):
                    raise
                st.session_state[f"{platform_code}_login_status_{account_name}"] = "未登录或已失效"
                log_event("auth", "relogin", "required")
                relogin_started = time.monotonic()
                adapter.login_manually(wait_seconds=900, keep_open=False)
                st.session_state[f"{platform_code}_login_status_{account_name}"] = "已登录"
                relogin_cost_ms = int((time.monotonic() - relogin_started) * 1000)
                log_event("auth", "relogin", "saved", cost_ms=relogin_cost_ms, wait_ms=relogin_cost_ms, wait_limit_ms=900000)

                return adapter.auto_click_chat_attachment_resumes(**collect_kwargs)



        rows = run_auto_collect_with_login_recovery()
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
        final_profile_key_count = get_profile_dedup_count(platform_code)
        added_profile_key_count = max(final_profile_key_count - start_profile_key_count, 0)
        perf_candidates = list(runtime.get("performance_candidates") or [])
        success_perf = [item for item in perf_candidates if item.get("status") == "success"]
        skipped_perf = [item for item in perf_candidates if item.get("status") == "skipped"]
        perf_stages = [
            item for item in list(runtime.get("performance_stages") or [])
            if item.get("stage") not in {"candidate.summary", "task.performance"}
        ]
        slowest_stage = max(perf_stages, key=lambda item: int(item.get("cost_ms") or 0), default={})
        total_wait_ms = sum(int(item.get("wait_ms") or 0) for item in perf_stages)
        avg_success_ms = int(sum(int(item.get("cost_ms") or 0) for item in success_perf) / len(success_perf)) if success_perf else 0
        avg_skipped_ms = int(sum(int(item.get("cost_ms") or 0) for item in skipped_perf) / len(skipped_perf)) if skipped_perf else 0
        handled_perf = success_perf + skipped_perf
        avg_handled_ms = int(sum(int(item.get("cost_ms") or 0) for item in handled_perf) / len(handled_perf)) if handled_perf else 0
        log_event(
            "task",
            "performance",
            "ok",
            success_avg_ms=avg_success_ms,
            skipped_avg_ms=avg_skipped_ms,
            total_wait_ms=total_wait_ms,
            slowest_stage=slowest_stage.get("stage") or "空",
            slowest_cost_ms=slowest_stage.get("cost_ms") or 0,
            recommendation="附件按钮改为单击，减少多开页和点击耗时，继续观察dom_fallback占比",
        )
        log_event(
            "dedup",
            "index_delta",
            "ok",
            added=added_profile_key_count,
            total=final_profile_key_count,
        )
        log_event(
            "task",
            "summary",
            final_status,
            cost_ms=elapsed_seconds * 1000,
            scanned=final_scanned_count,
            saved=len(saved_ids),
            skipped=int(runtime.get("skipped_count") or 0),
            failed=final_failed_count,
            average_ms=avg_handled_ms,
        )
        log_event("task", "finish", display_status, cost_ms=elapsed_seconds * 1000, saved=len(saved_ids))

        runtime["task_config"] = {
            **task_config,
            "任务状态": display_status,
            "已保存简历数": len(saved_ids),
            "raw_resume_ids": saved_ids,
        }
    except Exception as exc:
        error_message = str(exc)
        if any(token in error_message for token in platform_meta["login_error_tokens"]) or "登录" in error_message:
            st.session_state[f"{platform_code}_login_status_{account_name}"] = "未登录或已失效"
        if "登录窗口已关闭或登录流程已取消" in error_message:
            log_event("task", "cancel", "cancelled", reason="登录窗口已关闭")

            status = "cancelled"
            display_status = "已取消"
        else:
            log_event("task", "fail", "failed", reason=exc)

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
        runtime["status_dirty"] = True
        runtime["ui_refresh_requested"] = True


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
    runtime["last_heartbeat_at"] = time.monotonic()
    runtime["last_log_at"] = 0.0
    runtime["last_log_count"] = 0
    runtime["stale_warned"] = False
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
login_states = get_platform_login_states(account_name)
current_target_site = pending_task.get("目标网站") or "智联招聘"
has_login_state = bool(login_states.get(current_target_site))
start_label = f"开始{current_target_site}任务" if has_login_state else f"登录并开始{current_target_site}任务"

if runtime.pop("ui_refresh_requested", False):
    sync_runtime_to_session()
    st.rerun()

is_running = st.session_state.get("collect_running", False) or pending_task.get("任务状态") == "运行中"
collect_action_feedback = st.session_state.pop("collect_action_feedback", "")
if collect_action_feedback:
    st.success(collect_action_feedback)
if st.session_state.pop("auto_start_collect_task", False) and not runtime.get("running"):
    start_collect_task(pending_task, runtime)
    st.rerun()
pending_task = render_task_editor(pending_task, login_states, disabled=is_running)
current_target_site = pending_task.get("目标网站") or "智联招聘"
has_login_state = bool(login_states.get(current_target_site))
start_label = f"开始{current_target_site}任务" if has_login_state else f"登录并开始{current_target_site}任务"

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
        open_platform_meta = get_platform_collect_meta(pending_task.get("目标网站"))
        resume_dir = (settings.attachment_dir / open_platform_meta["code"]).resolve()
        resume_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(resume_dir)
            append_collect_log(f"已打开{open_platform_meta['name']}简历目录：{resume_dir}")
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
        clear_platform_meta = get_platform_collect_meta(pending_task.get("目标网站"))
        clear_platform_code = clear_platform_meta["code"]
        clear_platform_name = clear_platform_meta["name"]
        with create_session() as session:
            before_count = session.scalar(
                select(func.count())
                .select_from(platform_candidate_record)
                .where(platform_candidate_record.c.platform_code == clear_platform_code)
            ) or 0
            session.execute(delete(platform_candidate_record).where(platform_candidate_record.c.platform_code == clear_platform_code))
            session.commit()
        feedback_message = f"清空去重索引成功：删除 {before_count} 条索引记录。"
        append_collect_log(f"已清空{clear_platform_name}下载前个人信息去重库：删除 {before_count} 条索引记录。")
        st.session_state.collect_action_feedback = feedback_message
        st.rerun()

render_auto_status_and_candidates_panel(pending_task)

if not runtime.get("running"):
    history_platform_meta = get_platform_collect_meta(pending_task.get("目标网站"))
    render_history_task_table(history_platform_meta["code"], history_platform_meta["name"])

if runtime.get("running"):
    st.caption("任务正在后台执行。任务输出窗口会自动刷新并滚动到底部，不会刷新整个页面框架。")
elif runtime.pop("needs_status_refresh", False):
    st.caption("任务状态已更新，任务输出窗口会自动同步最新日志。")

st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
