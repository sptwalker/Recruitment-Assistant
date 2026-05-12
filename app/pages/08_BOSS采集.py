import html
import json
import time

import streamlit as st

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.services.boss_ws_bridge import BossWSBridge
from recruitment_assistant.services.ws_server import BossWSServer

st.set_page_config(page_title="BOSS采集", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("BOSS采集")
page_header("BOSS直聘采集", "通过 Chrome 扩展在页面内自动采集附件简历，完全绕过反检测。")

st.markdown(
    """
<style>
.vibe-page-title { background:transparent !important; border:0 !important; box-shadow:none !important; padding:0 !important; margin:0 0 12px !important; }
.vibe-page-title h1 { font-size:36px !important; line-height:1.08 !important; font-weight:900 !important; letter-spacing:-.8px !important; }
.vibe-page-title p { font-size:13px !important; margin-top:4px !important; }
.boss-section-title { margin:14px 0 7px; padding:0 0 6px; border-bottom:2px solid #DDE7F2; color:#172033; font-size:22px; line-height:1.2; font-weight:900; letter-spacing:-.3px; background:transparent; }
[data-testid="stVerticalBlockBorderWrapper"] { background:#FFFFFF !important; border:1px solid #E5EAF2 !important; border-radius:14px !important; box-shadow:none !important; }
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlock"] { gap:.45rem !important; }
[data-testid="stMetric"] { background:#FFFFFF; border:1px solid #EEF2F7; border-radius:12px; padding:7px 9px; }
[data-testid="stMetricLabel"] { font-size:11px !important; color:#64748B !important; }
[data-testid="stMetricValue"] { font-size:16px !important; line-height:1.18 !important; }
[data-testid="stMetricDelta"] { font-size:11px !important; }
.boss-info-text, .boss-info-text span { font-size:12px; line-height:1.45; }
.boss-status-on { color:#168A45; font-weight:700; font-size:12px; }
.boss-status-off { color:#94A3B8; font-size:12px; }
.boss-path { display:block; clear:both; font-family:Consolas,monospace; font-size:12px; color:#475569; background:#F8FAFC; border:1px solid #E5EAF2; border-radius:10px; padding:8px 10px; word-break:break-all; margin-top:8px; }
.boss-checklist { margin:0 0 0 18px; padding:0; }
.boss-checklist li { margin:4px 0; line-height:1.38; font-size:12px; }
.boss-log-box { height:260px; overflow-y:auto; background:#fff; border:1px solid #E5EAF2; border-radius:12px; padding:11px 12px; font-family:Consolas,monospace; font-size:13px; line-height:1.55; white-space:pre-wrap; }
.boss-log-info { color:#1F2937; font-size:13px; }
.boss-log-error { color:#C73552; font-weight:700; font-size:13px; }
.stButton > button { min-height:32px !important; padding:6px 12px !important; font-size:12px !important; border-radius:10px !important; }
[data-testid="stCaptionContainer"] { font-size:12px !important; }
</style>
""",
    unsafe_allow_html=True,
)


def section_title(title: str) -> None:
    st.markdown(f'<div class="boss-section-title">{title}</div>', unsafe_allow_html=True)


@st.cache_resource
def get_bridge() -> BossWSBridge:
    server = BossWSServer()
    server.start()
    return BossWSBridge(server)


bridge = get_bridge()
runtime = bridge.runtime_state
ws_connected = bridge.ws_server.is_extension_connected
is_running = runtime.get("running", False)
is_paused = runtime.get("paused", False)

# --- Connection Status ---
section_title("连接状态")
with st.container(border=True):
    col1, col2, col3, col4 = st.columns(4)
    ws_listening = getattr(bridge.ws_server, "is_listening", False)
    startup_error = getattr(bridge.ws_server, "startup_error", "")
    if ws_listening:
        col1.metric("WebSocket 服务", "监听中", delta=f"{bridge.ws_server.host}:{bridge.ws_server.port}")
    elif startup_error:
        col1.metric("WebSocket 服务", "启动失败", delta=startup_error)
    else:
        col1.metric("WebSocket 服务", "未监听", delta=f"{bridge.ws_server.host}:{bridge.ws_server.port}")
    if ws_connected:
        col2.markdown('<div class="boss-info-text boss-status-on">● 扩展已连接</div>', unsafe_allow_html=True)
    else:
        col2.markdown('<div class="boss-info-text boss-status-off">○ 扩展未连接</div>', unsafe_allow_html=True)
    page_ready = runtime.get("page_ready", False)
    if page_ready:
        col3.markdown('<div class="boss-info-text boss-status-on">● Boss页面就绪</div>', unsafe_allow_html=True)
    else:
        col3.markdown('<div class="boss-info-text boss-status-off">○ 等待打开Boss沟通页</div>', unsafe_allow_html=True)
    col4.metric("扩展版本", runtime.get("extension_version") or "-")
    page_url = runtime.get("page_url") or "-"
    st.caption(f"Boss 页面：{page_url}")

# --- Test Run ---
section_title("测试轮次")
with st.container(border=True):
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Run ID", runtime.get("run_id") or "-")
    r2.metric("开始时间", runtime.get("run_started_at") or "-")
    r3.metric("最近事件", runtime.get("last_event_at") or "-")
    r4.metric("日志事件数", getattr(bridge, "_event_seq", 0))
    run_cols = st.columns(2)
    if run_cols[0].button("重置本轮测试", disabled=runtime.get("running", False)):
        bridge.reset_run()
        st.rerun()
    if run_cols[1].button("生成本轮摘要"):
        st.session_state["boss_run_summary"] = bridge.get_run_summary()
    st.markdown(f'<div class="boss-path">日志文件：{html.escape(runtime.get("log_file") or "-")}</div>', unsafe_allow_html=True)
    summary = st.session_state.get("boss_run_summary")
    if summary:
        st.code(json.dumps(summary, ensure_ascii=False, indent=2), language="json")

# --- Collection Config & Controls ---
section_title("采集配置")
with st.container(border=True):
    c1, c2, c3 = st.columns(3)
    max_resumes = c1.number_input("最大采集数量", min_value=1, max_value=100, value=5, step=1)
    interval_ms = c2.number_input("点击间隔（毫秒）", min_value=2000, max_value=30000, value=5000, step=1000)
    test_mode = c3.selectbox("测试模式", ["连续采集", "单步采集1人"])

    btn_cols = st.columns(4)
    if btn_cols[0].button("开始采集", disabled=is_running or not ws_connected, type="primary"):
        effective_max = 1 if test_mode == "单步采集1人" else max_resumes
        bridge.start_collect({"max_resumes": effective_max, "interval_ms": interval_ms, "test_mode": test_mode})
        st.rerun()
    if btn_cols[1].button("暂停", disabled=not is_running or is_paused):
        bridge.pause_collect()
        st.rerun()
    if btn_cols[2].button("继续", disabled=not is_paused):
        bridge.resume_collect()
        st.rerun()
    if btn_cols[3].button("停止", disabled=not is_running):
        bridge.stop_collect()
        st.rerun()

# --- Progress ---
section_title("采集进度")
with st.container(border=True):
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("已下载", runtime.get("downloaded_count", 0))
    p2.metric("已跳过", runtime.get("skipped_count", 0))
    p3.metric("当前索引", runtime.get("current_index", 0))
    status_text = "采集中" if is_running and not is_paused else ("已暂停" if is_paused else "空闲")
    p4.metric("状态", status_text)
    skip_counts = runtime.get("skip_reason_counts", {})
    if skip_counts:
        st.caption("跳过原因统计：" + "；".join(f"{k}={v}" for k, v in skip_counts.items()))

# --- Test Checklist ---
section_title("测试操作循环")
with st.container(border=True):
    st.markdown(
        """
<ol class="boss-checklist">
<li>点击“重置本轮测试”，确认生成新的 Run ID 和 JSONL 日志路径。</li>
<li>Chrome 打开 Boss 沟通页，确认“扩展已连接”和“Boss页面就绪”。</li>
<li>先选择“单步采集1人”，观察候选人点击、跳过、下载事件是否完整写入日志。</li>
<li>点击“生成本轮摘要”，根据下载数、跳过原因和最后事件定位问题。</li>
<li>修复后重新加载扩展或刷新页面，再进入下一轮 Run ID 测试。</li>
<li>单步稳定后切换“连续采集”，逐步扩大最大采集数量。</li>
</ol>
""",
        unsafe_allow_html=True,
    )

# --- Logs ---
section_title("实时日志")
with st.container(border=True):
    logs = runtime.get("logs", [])
    if logs:
        log_html = ""
        for entry in logs[-50:]:
            level = entry.get("level", "info")
            css_class = "boss-log-error" if level == "error" else "boss-log-info"
            msg = html.escape(entry.get("message", ""))
            at = html.escape(entry.get("at", ""))
            log_html += f'<div class="{css_class}">[{at}] {msg}</div>'
        st.markdown(f'<div class="boss-log-box">{log_html}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="boss-log-box"><span style="color:#94A3B8">等待采集开始...</span></div>', unsafe_allow_html=True)

# --- Candidates ---
candidates = runtime.get("candidates", [])
if candidates:
    section_title("候选人列表")
    with st.container(border=True):
        for c in reversed(candidates[-20:]):
            sig = c.get("signature", "")
            status = c.get("status", "")
            prefix = "已下载" if "download" in status else "已跳过"
            detail = c.get("file", "") or c.get("reason", "")
            st.markdown(f"**{prefix}** `{sig}` — {detail}")

# --- Setup Guide ---
with st.expander("首次使用？查看扩展安装指引"):
    st.markdown("""
### 安装 Chrome 扩展

1. 打开 Chrome，访问 `chrome://extensions/`
2. 开启右上角「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择项目目录下的 `chrome_extension/` 文件夹
5. 扩展安装完成后，打开 Boss直聘沟通页面
6. 扩展会自动连接本页面的 WebSocket 服务

### 使用流程

1. 确保本页面已打开（WebSocket 服务随页面启动）
2. 在 Chrome 中打开 `https://www.zhipin.com/web/chat/index`
3. 确认上方显示「扩展已连接」和「Boss页面就绪」
4. 配置采集参数后点击「开始采集」
""")

# Auto-refresh when collecting
if is_running:
    time.sleep(2)
    st.rerun()
