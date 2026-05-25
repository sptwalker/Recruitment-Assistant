import html
import json
import os
import time
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

from components.bridges import get_boss_bridge
from components.layout import get_theme_css, inject_vibe_style, page_header
from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.services.crawl_task_service import BossCandidateRecordService, CrawlTaskService
from recruitment_assistant.storage.db import create_session

st.set_page_config(page_title="BOSS采集", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("BOSS直聘采集")
page_header(
    "BOSS直聘采集",
    "通过 Chrome 扩展在页面内自动采集附件简历，完全绕过反检测。",
    icon="icon/boss直聘.png",
)

st.markdown(
    """
<style>
.vibe-page-title { background:transparent !important; border:0 !important; box-shadow:none !important; padding:0 !important; margin:0 0 12px !important; }
.vibe-page-title h1 { font-size:30px !important; line-height:1.04 !important; font-weight:900 !important; letter-spacing:-.8px !important; }
.vibe-page-title p { font-size:13px !important; margin-top:4px !important; }
.boss-section-title { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; margin:14px 0 7px; padding:0 0 6px; border-bottom:2px solid var(--color-border); color:var(--color-text); font-size:22px; line-height:1.2; font-weight:900; letter-spacing:-.3px; background:transparent; }
.boss-section-note { color:var(--color-warning); font-size:13px; font-weight:600; letter-spacing:0; }
[data-testid="stVerticalBlockBorderWrapper"] { background:var(--color-surface) !important; border:1px solid var(--color-border) !important; border-radius:14px !important; box-shadow:none !important; }
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlock"] { gap:.45rem !important; }
[data-testid="stMetric"] { background:var(--color-surface); border:1px solid var(--color-border); border-radius:12px; padding:7px 9px; }
[data-testid="stMetricLabel"] { font-size:11px !important; color:var(--color-text-secondary) !important; }
[data-testid="stMetricValue"] { font-size:16px !important; line-height:1.18 !important; }
[data-testid="stMetricDelta"] { font-size:11px !important; }
.boss-info-text, .boss-info-text span { font-size:12px; line-height:1.45; }
.boss-status-banner { min-height:58px; height:58px; box-sizing:border-box; background:var(--color-surface); border:1px solid var(--color-border); border-radius:12px; padding:8px 10px; display:flex; flex-direction:column; justify-content:center; gap:3px; overflow:hidden; }
.boss-status-banner.one-line { flex-direction:row; align-items:center; justify-content:space-between; gap:8px; }
.boss-status-label { font-size:11px; color:var(--color-text-secondary); line-height:1; white-space:nowrap; }
.boss-status-value { font-size:16px; font-weight:800; color:var(--color-text); line-height:1.1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.boss-status-sub { font-size:11px; color:var(--color-text-secondary); line-height:1.1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.boss-status-pair { display:flex; flex-direction:column; gap:5px; }
.boss-status-pair div { white-space:nowrap; }
.stButton > button, .stLinkButton > a { min-height:32px !important; padding:6px 12px !important; font-size:12px !important; border-radius:10px !important; white-space:nowrap !important; width:100% !important; }
.boss-status-on { color:#0a7d2e; font-weight:700; font-size:12px; }
.boss-status-off { color:var(--color-text-muted); font-size:12px; }
.boss-status-banner.is-listening .boss-status-value { color:#0a7d2e; }
.boss-status-banner.is-error .boss-status-value { color:#b91c1c; }
.boss-status-banner.is-idle .boss-status-value { color:#4b5563; }
.boss-status-ext-disconnected { color:#b91c1c; font-weight:800; font-size:12px; }
.boss-status-ext-error-code { color:#b91c1c; font-size:11px; opacity:.85; line-height:1.1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.boss-path { display:block; clear:both; font-family:Consolas,monospace; font-size:12px; color:var(--color-text-secondary); background:var(--color-bg-soft); border:1px solid var(--color-border); border-radius:10px; padding:8px 10px; word-break:break-all; margin-top:8px; }
.boss-checklist { margin:0 0 0 18px; padding:0; }
.boss-checklist li { margin:4px 0; line-height:1.38; font-size:12px; }
.boss-log-box { height:300px; overflow-y:auto; background:var(--color-surface); border:1px solid var(--color-border); border-radius:12px; padding:9px 10px; font-family:Consolas,monospace; font-size:12px; line-height:1.42; white-space:pre-wrap; color:var(--color-text); }
.boss-auto-scroll-frame { width:100%; height:316px; border:0; display:block; }
.boss-candidate-box { height:300px; overflow-y:auto; background:var(--color-surface); border:1px solid var(--color-border); border-radius:12px; padding:0; font-size:12px; }
.boss-candidate-table { width:100%; border-collapse:collapse; table-layout:fixed; }
.boss-candidate-table th { position:sticky; top:0; z-index:1; background:var(--color-bg-soft); color:var(--color-text-secondary); font-size:12px; text-align:left; padding:8px 7px; border-bottom:1px solid var(--color-border); }
.boss-candidate-table td { color:var(--color-text); padding:7px; border-bottom:1px solid var(--color-border); vertical-align:top; word-break:break-all; }
.boss-candidate-table tr:last-child td { border-bottom:0; }
.boss-candidate-status-downloaded { color:#0a7d2e; font-weight:800; white-space:nowrap; }
.boss-candidate-status-skipped { color:#b45309; font-weight:800; white-space:nowrap; }
.boss-empty-box { height:300px; overflow-y:auto; background:var(--color-surface); border:1px solid var(--color-border); border-radius:12px; padding:9px 10px; color:var(--color-text-muted); font-size:12px; }
.boss-log-highlight { color:#b45309 !important; font-weight:900; font-size:14px; }
.boss-log-info { color:var(--color-text); font-size:13px; }
.boss-log-success { color:#0a7d2e !important; font-weight:700; font-size:13px; }
.boss-log-error { color:var(--color-danger); font-weight:700; font-size:13px; }
.boss-log-skipped { color:#b45309 !important; font-weight:700; font-size:13px; }
.boss-log-stat { color:var(--color-primary); font-weight:700; font-size:13px; }
.plain-section-title { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:18px 0 10px; }
.plain-section-title h3 { margin:0; font-size:18px; line-height:1.3; color:var(--color-text); }
.collect-panel-stat { color:var(--color-primary); font-size:14px; font-weight:700; white-space:nowrap; }
.boss-result-title { display:flex; align-items:center; justify-content:space-between; gap:10px; margin:0 0 8px; min-height:26px; }
.boss-result-title strong { color:var(--color-text); font-size:18px; line-height:1.3; font-weight:700; }
.boss-result-title span { color:var(--color-primary); font-size:12px; font-weight:700; line-height:1.25; text-align:right; }
[data-testid="stCaptionContainer"] { font-size:12px !important; }
[data-testid="stSelectbox"] label, [data-testid="stSelectbox"] [data-testid="stWidgetLabel"] { padding-left:12px !important; }
[data-testid="stCheckbox"] { padding-top:14px !important; padding-left:8px !important; }
[data-testid="stCheckbox"] label, [data-testid="stCheckbox"] label p { font-size:15px !important; }
</style>
""",
    unsafe_allow_html=True,
)




def classify_boss_log(message: str, level: str = "info") -> str:
    if level == "success":
        return "boss-log-success"
    if level == "highlight" or any(token in message for token in ["附件简历调试", "发现弹出页面", "成功获取以下信息", "正在记录你的操作", "成功记录到你的点击操作", "学习任务已完成", "PDF iframe", "boss-svg", "捕获下载链接"]):
        return "boss-log-highlight"
    if "附件按钮:" in message and ("unknown_resume" in message or "附件简历" in message or "开始识别弹出页面" in message):
        return "boss-log-highlight"
    if level == "error" or any(token in message for token in ["失败", "错误", "断开"]):
        return "boss-log-error"
    if any(token in message for token in ["跳过", "duplicate", "已索要", "索要简历", "resume_requested", "resume_request_clicked"]):
        return "boss-log-skipped"
    if any(token in message for token in ["保存归档", "保存", "已下载", "下载完成"]):
        return "boss-log-success"
    if any(token in message for token in ["统计", "采集完成", "扫描完成", "沟通职位"]):
        return "boss-log-stat"
    return "boss-log-info"


def translate_boss_detail(value: str) -> str:
    if not value:
        return ""
    if value.startswith("button_disabled:"):
        state = value.split(":", 1)[1] or "未知状态"
        return f"附件简历按钮不可用（{state}）"
    if value.startswith("download_error:"):
        error = value.split(":", 1)[1] or "未知错误"
        return f"下载失败（{error}）"
    mapping = {
        "need_request_resume": "需要索要简历，已按设置跳过",
        "resume_requested": "已成功索要简历，等待候选人上传",
        "resume_requested_by_user": "已成功索要简历，等待候选人上传",
        "resume_request_unconfirmed": "已点击索要简历但未检测到请求发送成功",
        "resume_request_confirm_not_found": "未找到索要简历确认按钮，未检测到请求发送成功",
        "resume_already_requested": "此前已索要简历，等待候选人上传",
        "download_button_not_found": "未找到简历下载按钮",
        "download_timeout": "下载等待超时",
        "download_failed": "下载失败",
        "no_resume_button": "未找到附件简历按钮",
        "candidate_info_unrecognized": "候选人信息未识别",
        "click_failed": "点击候选人失败",
        "new_collect_started": "新采集任务已开始",
        "collect_stopped": "采集已停止",
        "unknown": "未知原因",
    }
    return mapping.get(value, value)


@st.cache_data(ttl=30)
def load_boss_history_rows() -> tuple[list[dict], int, int]:
    status_mapping = {
        "running": "运行中",
        "success": "成功",
        "cancelled": "已停止",
        "failed": "失败",
        "pending": "待开始",
    }
    with create_session() as session:
        task_service = CrawlTaskService(session)
        task_rows = task_service.list_tasks(limit=50, platform_code="boss")
        success_task_count, success_resume_count = task_service.success_summary(platform_code="boss")
        rows = [
            {
                "批次ID": row.id,
                "时间": row.started_at.strftime("%Y-%m-%d %H:%M:%S") if row.started_at else "",
                "目标数量": row.planned_count or 0,
                "获取数量": row.success_count,
                "跳过数量": row.failed_count,
                "耗时": f"{int((row.finished_at - row.started_at).total_seconds())}秒" if row.started_at and row.finished_at else "运行中",
                "状态": status_mapping.get(row.status, row.status),
                "任务名称": row.task_name,
                "错误信息": (row.error_message or "")[:80],
            }
            for row in task_rows
        ]
        return rows, success_task_count, success_resume_count


@st.cache_data(ttl=30)
def load_boss_dedup_record_count() -> int:
    with create_session() as session:
        return BossCandidateRecordService(session).count_records("boss")


def load_boss_records_for_display() -> list[dict]:
    """读取 BOSS 平台已归档候选人记录，按首次归档时间升序，供 UI 表格渲染。"""
    try:
        with create_session() as session:
            return BossCandidateRecordService(session).list_records_for_display("boss")
    except Exception as exc:
        st.warning(f"已获取候选人列表读取失败：{exc}")
        return []


def render_boss_history_task_table() -> None:
    try:
        task_rows, success_task_count, success_resume_count = load_boss_history_rows()
    except Exception as exc:
        st.warning(f"历史批次任务读取失败：{exc}")
        task_rows = []
        success_task_count = 0
        success_resume_count = 0

    render_result_title(
        "历史批次任务",
        f"已成功执行{success_task_count}次任务，共获取了{success_resume_count}份简历。",
    )
    st.dataframe(
        task_rows,
        use_container_width=True,
        hide_index=True,
        height=300,
    )



def section_title(title: str, note: str | None = None) -> None:
    if note:
        st.markdown(
            f'<div class="boss-section-title"><span>{title}</span><span class="boss-section-note">{note}</span></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(f'<div class="boss-section-title">{title}</div>', unsafe_allow_html=True)


def format_boss_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, sec = divmod(total_seconds, 60)
    return f"{minutes}分{sec:02d}秒"


def boss_run_elapsed_seconds(runtime_state: dict) -> float:
    started_at = runtime_state.get("run_started_at") or ""
    if not started_at:
        return 0
    try:
        return max(0, datetime.now().timestamp() - datetime.fromisoformat(started_at).timestamp())
    except ValueError:
        return 0


def render_result_title(title: str, summary: str) -> None:
    st.markdown(
        f'<div class="boss-result-title"><strong>{html.escape(title)}</strong><span>{html.escape(summary)}</span></div>',
        unsafe_allow_html=True,
    )


def render_log_title_with_copy(title: str, summary: str, copy_payload: str) -> None:
    theme_vars = get_theme_css()
    payload_json = json.dumps(copy_payload)
    components.html(
        f"""
<style>
{theme_vars}
html, body {{ margin:0; padding:0; background:transparent; font-family:Arial, 'Microsoft YaHei', sans-serif; }}
.boss-log-title-row {{ display:flex; align-items:center; gap:10px; min-height:26px; }}
.boss-log-title-row strong {{ color:var(--color-text); font-size:18px; line-height:1.3; font-weight:700; }}
.boss-log-title-row .summary {{ flex:1; color:var(--color-primary); font-size:12px; font-weight:700; line-height:1.25; text-align:right; }}
.boss-copy-btn {{ background:var(--color-primary); color:#fff; border:0; border-radius:6px; padding:3px 12px; font-size:12px; font-weight:800; cursor:pointer; letter-spacing:.5px; }}
.boss-copy-btn:hover {{ filter:brightness(1.1); }}
.boss-copy-btn.ok {{ background:var(--color-success); }}
.boss-copy-btn.fail {{ background:#c44; }}
</style>
<div class="boss-log-title-row">
  <strong>{html.escape(title)}</strong>
  <span class="summary">{html.escape(summary)}</span>
  <button class="boss-copy-btn" id="boss-log-copy">COPY</button>
</div>
<script>
const btn = document.getElementById("boss-log-copy");
const payload = {payload_json};
const flash = (cls, text) => {{
  btn.classList.remove("ok", "fail");
  btn.classList.add(cls);
  btn.textContent = text;
  setTimeout(() => {{ btn.classList.remove(cls); btn.textContent = "COPY"; }}, 1500);
}};
const fallbackCopy = () => {{
  const ta = document.createElement("textarea");
  ta.value = payload;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try {{
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    if (ok) {{ flash("ok", "OK"); }} else {{ flash("fail", "FAIL"); }}
  }} catch (e) {{
    document.body.removeChild(ta);
    flash("fail", "FAIL");
  }}
}};
btn.addEventListener("click", () => {{
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(payload).then(() => flash("ok", "OK")).catch(fallbackCopy);
  }} else {{
    fallbackCopy();
  }}
}});
</script>
""",
        height=36,
    )


def build_log_summary(runtime_state: dict) -> str:
    candidates = runtime_state.get("candidates", []) or []
    current_index = int(runtime_state.get("current_index") or 0)
    progress_scanned = int(runtime_state.get("scanned_count") or 0)
    fallback_scanned = current_index + 1 if candidates else 0
    scanned_count = max(progress_scanned, fallback_scanned, len(candidates))
    downloaded_count = int(runtime_state.get("downloaded_count") or 0)
    elapsed_seconds = boss_task_elapsed_seconds(runtime_state)
    avg_seconds = int(round(elapsed_seconds / scanned_count)) if scanned_count else 0
    return (
        f"已扫描{scanned_count}位候选人，已成功下载{downloaded_count}简历，"
        f"已扫描{format_boss_duration(elapsed_seconds)}，平均每人耗时{avg_seconds}秒"
    )


def boss_task_elapsed_seconds(runtime_state: dict) -> float:
    started_at = runtime_state.get("task_started_at") or runtime_state.get("run_started_at") or ""
    if not started_at:
        return 0
    try:
        return max(0, datetime.now().timestamp() - datetime.fromisoformat(started_at).timestamp())
    except ValueError:
        return 0


def build_candidate_summary_db(current_total: int, baseline: int) -> str:
    added = max(0, int(current_total or 0) - int(baseline or 0))
    return f"当前记录{int(current_total or 0)}人，本轮增加记录{added}人。"


def build_candidate_summary(runtime_state: dict) -> str:
    candidates = runtime_state.get("candidates", [])
    skipped_count = int(runtime_state.get("skipped_count") or 0)
    skip_counts = runtime_state.get("skip_reason_counts", {}) or {}
    dedup_skipped_count = int(skip_counts.get("boss_dedup_hit") or 0)
    resume_request_count = int(runtime_state.get("resume_request_count") or 0)
    downloaded_count = int(runtime_state.get("downloaded_count") or 0)
    return f"已记录{len(candidates)}人，跳过{skipped_count}人（去重{dedup_skipped_count}人），向{resume_request_count}人索要了简历，成功下载{downloaded_count}份简历"


def render_auto_scroll_html(body_html: str, anchor: str, height: int = 316) -> None:
    theme_vars = get_theme_css()
    components.html(
        f"""
<style>
{theme_vars}
html, body {{ margin:0; padding:0; background:transparent; font-family:Arial, 'Microsoft YaHei', sans-serif; font-size:12px; overflow:hidden; color:var(--color-text); }}
.boss-log-box {{ height:300px; overflow-y:auto; background:var(--color-surface); border:1px solid var(--color-border); border-radius:12px; padding:9px 10px; font-family:Consolas, 'Microsoft YaHei', monospace; font-size:12px; line-height:1.42; white-space:pre-wrap; box-sizing:border-box; color:var(--color-text); }}
.boss-candidate-box {{ height:300px; overflow-y:auto; background:var(--color-surface); border:1px solid var(--color-border); border-radius:12px; padding:0; font-size:12px; box-sizing:border-box; }}
.boss-candidate-table {{ width:100%; border-collapse:collapse; table-layout:fixed; font-size:12px; }}
.boss-candidate-table th {{ position:sticky; top:0; z-index:1; background:var(--color-bg-soft); color:var(--color-text-secondary); font-size:12px; text-align:left; padding:8px 7px; border-bottom:1px solid var(--color-border); }}
.boss-candidate-table td {{ color:var(--color-text); padding:7px; border-bottom:1px solid var(--color-border); vertical-align:top; word-break:break-all; font-size:12px; line-height:1.35; }}
.boss-candidate-table tr:last-child td {{ border-bottom:0; }}
.boss-candidate-status-downloaded {{ color:#0a7d2e; font-weight:800; white-space:nowrap; }}
.boss-candidate-status-skipped {{ color:#b45309; font-weight:800; white-space:nowrap; }}
.boss-empty-box {{ height:300px; overflow-y:auto; background:var(--color-surface); border:1px solid var(--color-border); border-radius:12px; padding:9px 10px; color:var(--color-text-muted); font-size:12px; box-sizing:border-box; }}
.boss-log-highlight {{ color:#b45309 !important; font-weight:900; font-size:12px; }}
.boss-log-info {{ color:var(--color-text); font-size:12px; }}
.boss-log-success {{ color:#0a7d2e !important; font-weight:700; font-size:12px; }}
.boss-log-error {{ color:var(--color-danger); font-weight:700; font-size:12px; }}
.boss-log-skipped {{ color:#b45309 !important; font-weight:700; font-size:12px; }}
.boss-log-stat {{ color:var(--color-primary); font-weight:700; font-size:12px; }}
</style>
<div id="{anchor}-wrap">{body_html}</div>
<script>
function scrollBossResultToBottom() {{
  const scrollers = document.querySelectorAll(".boss-log-box, .boss-candidate-box, .boss-empty-box");
  scrollers.forEach((scroller) => {{
    scroller.scrollTop = scroller.scrollHeight;
    const lastRow = scroller.querySelector("tbody tr:last-child");
    if (lastRow) {{
      lastRow.scrollIntoView({{ block: "end", inline: "nearest" }});
    }}
  }});
}}
requestAnimationFrame(scrollBossResultToBottom);
setTimeout(scrollBossResultToBottom, 50);
setTimeout(scrollBossResultToBottom, 200);
</script>
""",
        height=height,
        scrolling=False,
    )


bridge = get_boss_bridge()
runtime = bridge.runtime_state
ws_connected = bridge.ws_server.is_extension_connected
is_running = runtime.get("running", False)
is_paused = runtime.get("paused", False)

# --- Status & Run ---
section_title("运行状态")
with st.container(border=True):
    ws_listening = getattr(bridge.ws_server, "is_listening", False)
    startup_error = getattr(bridge.ws_server, "startup_error", "")
    page_ready = runtime.get("page_ready", False)
    page_url = runtime.get("page_url") or "-"

    status_cols = st.columns([1.25, 1.35, 1, 1, 1])
    if ws_listening:
        ws_value = "监听中"
        ws_sub = f"{bridge.ws_server.host}:{bridge.ws_server.port}"
        ws_state_class = "is-listening"
    elif startup_error:
        ws_value = "启动失败"
        ws_sub = startup_error
        ws_state_class = "is-error"
    else:
        ws_value = "未监听"
        ws_sub = f"{bridge.ws_server.host}:{bridge.ws_server.port}"
        ws_state_class = "is-idle"
    status_cols[0].markdown(
        f'<div class="boss-status-banner one-line {ws_state_class}"><span class="boss-status-label">WebSocket</span><span class="boss-status-value">{html.escape(ws_value)}</span><span class="boss-status-sub">{html.escape(ws_sub)}</span></div>',
        unsafe_allow_html=True,
    )
    last_disconnect_reason = runtime.get("last_disconnect_reason") or ""
    if ws_connected:
        ext_main_html = '<div class="boss-info-text boss-status-on">● 扩展已连接</div>'
    else:
        ext_main_html = '<div class="boss-info-text boss-status-ext-disconnected">○ 未连接</div>'
        if last_disconnect_reason:
            ext_main_html += f'<div class="boss-status-ext-error-code">{html.escape(last_disconnect_reason)}</div>'
    page_text = "● Boss页面就绪" if page_ready else "○ 等待沟通页"
    page_class = "boss-status-on" if page_ready else "boss-status-off"
    status_cols[1].markdown(
        f'<div class="boss-status-banner"><div class="boss-status-pair">{ext_main_html}<div class="boss-info-text {page_class}">{page_text}</div></div></div>',
        unsafe_allow_html=True,
    )
    status_cols[2].metric("扩展版本", runtime.get("extension_version") or "-")
    try:
        current_dedup_total = load_boss_dedup_record_count()
    except Exception:
        current_dedup_total = 0
    status_cols[3].metric("去重记录数", current_dedup_total)
    status_cols[4].metric("最近事件", runtime.get("last_event_at") or "-")

    action_cols = st.columns([1.25, 1.25, 1.1, 1.25, 4.15], gap="medium")
    action_cols[0].link_button("打开 BOSS 登录页面", "https://www.zhipin.com/web/user/?ka=header-login")
    if action_cols[1].button("重新检测 BOSS 页面", disabled=not ws_connected):
        bridge.probe_page()
        st.rerun()
    if action_cols[2].button("重置日志信息", disabled=runtime.get("running", False)):
        bridge.reset_run()
        st.rerun()
    if action_cols[3].button("清除去重数据库", disabled=runtime.get("running", False)):
        try:
            deleted_count = bridge.clear_boss_dedup_records()
            st.session_state["boss_dedup_clear_message"] = f"已清除 BOSS 去重记录 {deleted_count} 条"
        except Exception as exc:
            st.session_state["boss_dedup_clear_message"] = f"清除 BOSS 去重记录失败：{exc}"
        st.rerun()
    clear_message = st.session_state.get("boss_dedup_clear_message")
    if clear_message:
        action_cols[4].caption(clear_message)
    summary = st.session_state.get("boss_run_summary")
    if summary:
        st.code(json.dumps(summary, ensure_ascii=False, indent=2), language="json")

# --- Collection & Results ---
section_title("采集与结果", "注意：如果要自动保存简历，请在chorm浏览器设置中关闭“下载前询问每个文件的保存位置”")
with st.container(border=True):
    top_cols = st.columns([1.1, 1.1, 5.8])
    collect_mode = top_cols[0].selectbox("采集模式", ["按数量采集", "按时间采集"])
    if collect_mode == "按时间采集":
        collect_minutes = top_cols[1].number_input("采集时间（分钟）", min_value=5, max_value=120, value=10, step=5)
        max_resumes = 0
    else:
        max_resumes = top_cols[1].number_input("目标下载份数", min_value=1, max_value=100, value=5, step=1)
        collect_minutes = 0

    btn_cols = st.columns([1, 1, 1, 1, 1.4, 4])
    if btn_cols[0].button("开始采集", disabled=is_running or not ws_connected, type="primary", use_container_width=True):
        if collect_mode == "按时间采集":
            effective_max = 0
        else:
            effective_max = max_resumes
        bridge.start_collect({
            "max_resumes": effective_max,
            "collect_mode": collect_mode,
            "collect_minutes": int(collect_minutes) if collect_mode == "按时间采集" else 0,
        })
        st.rerun()
    if btn_cols[1].button("暂  停", disabled=not is_running or is_paused, use_container_width=True):
        bridge.pause_collect()
        st.rerun()
    if btn_cols[2].button("继  续", disabled=not is_paused, use_container_width=True):
        bridge.resume_collect()
        st.rerun()
    if btn_cols[3].button("停  止", disabled=not is_running, use_container_width=True):
        bridge.stop_collect()
        st.rerun()
    if btn_cols[4].button("打开简历目录", use_container_width=True):
        settings = get_settings()
        boss_save_dir = (settings.attachment_dir / "boss" / datetime.now().strftime("%Y%m%d")).resolve()
        boss_save_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(boss_save_dir))
        except Exception as exc:
            st.error(f"打开目录失败：{exc}")
            st.code(str(boss_save_dir))

    skip_counts = runtime.get("skip_reason_counts", {})
    if skip_counts:
        st.caption("跳过原因统计：" + "；".join(f"{k}={v}" for k, v in skip_counts.items()))

    result_cols = st.columns([1.15, 1])
    with result_cols[0]:
        logs = runtime.get("logs", [])
        copy_payload = "\n".join(
            f"[{entry.get('at', '')}] {entry.get('message', '')}"
            for entry in logs[-120:]
        )
        render_log_title_with_copy("实时日志", build_log_summary(runtime), copy_payload)
        if logs:
            log_html = ""
            for entry in logs[-120:]:
                level = entry.get("level", "info")
                raw_msg = entry.get("message", "")
                msg = html.escape(raw_msg)
                at = html.escape(entry.get("at", ""))
                css_class = classify_boss_log(raw_msg, level)
                log_html += f'<div class="{css_class}">[{at}] {msg}</div>'
            if is_running:
                render_auto_scroll_html(f'<div class="boss-log-box">{log_html}</div>', "boss-log-bottom")
            else:
                st.markdown(f'<div class="boss-log-box">{log_html}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="boss-empty-box">等待采集开始...</div>', unsafe_allow_html=True)

    with result_cols[1]:
        render_boss_history_task_table()

# --- Archived Candidates (full width, page bottom) ---
with st.container(border=True):
    try:
        current_dedup_total_for_summary = load_boss_dedup_record_count()
    except Exception:
        current_dedup_total_for_summary = 0
    baseline = int(runtime.get("dedup_record_count_baseline") or 0)
    render_result_title(
        "已入库候选人",
        build_candidate_summary_db(current_dedup_total_for_summary, baseline),
    )
    records = load_boss_records_for_display()
    if records:
        display_rows = [
            {"删除": False, **{k: v for k, v in r.items() if k != "id"}}
            for r in records
        ]
        edited = st.data_editor(
            display_rows,
            use_container_width=True,
            hide_index=True,
            height=400,
            num_rows="fixed",
            disabled=[c for c in display_rows[0].keys() if c != "删除"],
            key="boss_records_editor",
        )
        to_delete_ids: list[int] = [
            int(records[i]["id"])
            for i, row in enumerate(edited)
            if row.get("删除")
        ]
        del_cols = st.columns([1.4, 1.4, 5.2])
        delete_disabled = len(to_delete_ids) == 0 or is_running
        if del_cols[0].button(
            f"删除选中（{len(to_delete_ids)}）",
            disabled=delete_disabled,
            type="primary",
            use_container_width=True,
        ):
            try:
                with create_session() as session:
                    svc = BossCandidateRecordService(session)
                    deleted = sum(1 for rid in to_delete_ids if svc.delete_record_by_id(rid))
                st.session_state["boss_record_delete_message"] = f"已删除 {deleted} 条记录"
            except Exception as exc:
                st.session_state["boss_record_delete_message"] = f"删除失败：{exc}"
            st.rerun()
        if del_cols[1].button(
            "全部删除",
            disabled=is_running,
            use_container_width=True,
        ):
            st.session_state["boss_confirm_delete_all"] = True
            st.rerun()
        msg = st.session_state.pop("boss_record_delete_message", None)
        if msg:
            del_cols[2].caption(msg)

        if st.session_state.get("boss_confirm_delete_all"):
            with st.container(border=True):
                st.warning("警告：这将删除BOSS直聘网站所有的已入库候选人记录！")
                confirm_cols = st.columns([1.4, 1.4, 5.2])
                if confirm_cols[0].button(
                    "确认全部删除",
                    type="primary",
                    use_container_width=True,
                    key="boss_confirm_delete_all_yes",
                ):
                    try:
                        with create_session() as session:
                            svc = BossCandidateRecordService(session)
                            deleted = svc.delete_all_by_platform("boss")
                        st.session_state["boss_record_delete_message"] = f"已全部删除 {deleted} 条记录"
                    except Exception as exc:
                        st.session_state["boss_record_delete_message"] = f"全部删除失败：{exc}"
                    st.session_state["boss_confirm_delete_all"] = False
                    st.rerun()
                if confirm_cols[1].button(
                    "取消",
                    use_container_width=True,
                    key="boss_confirm_delete_all_no",
                ):
                    st.session_state["boss_confirm_delete_all"] = False
                    st.rerun()
    else:
        st.markdown('<div class="boss-empty-box">暂无已入库候选人。采集成功后会归档到这里。</div>', unsafe_allow_html=True)

# Auto-refresh when collecting
if is_running:
    time.sleep(2)
    st.rerun()
