import time
from datetime import datetime

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
.boss-panel { background:#fff; border:1px solid #E5EAF2; border-radius:22px; padding:20px; box-shadow:0 12px 32px rgba(31,41,55,.05); margin-bottom:18px; }
.boss-panel h3 { margin:0 0 14px; font-size:18px; color:#1F2937; }
.boss-log-box { height:280px; overflow-y:auto; background:#fff; border:1px solid #E5EAF2; border-radius:16px; padding:14px; font-family:Consolas,monospace; font-size:13px; line-height:1.55; white-space:pre-wrap; }
.boss-log-info { color:#1F2937; }
.boss-log-error { color:#C73552; font-weight:700; }
.boss-status-on { color:#168A45; font-weight:700; }
.boss-status-off { color:#94A3B8; }
</style>
""",
    unsafe_allow_html=True,
)


def get_ws_server() -> BossWSServer:
    if "boss_ws_server" not in st.session_state:
        server = BossWSServer()
        server.start()
        st.session_state["boss_ws_server"] = server
    return st.session_state["boss_ws_server"]


def get_bridge() -> BossWSBridge:
    if "boss_ws_bridge" not in st.session_state:
        server = get_ws_server()
        bridge = BossWSBridge(server)
        st.session_state["boss_ws_bridge"] = bridge
    return st.session_state["boss_ws_bridge"]


bridge = get_bridge()
runtime = bridge.runtime_state

# --- Connection Status ---
st.markdown('<div class="boss-panel"><h3>连接状态</h3>', unsafe_allow_html=True)
col1, col2, col3 = st.columns(3)
ws_connected = bridge.ws_server.is_extension_connected
col1.metric("WebSocket 服务", "运行中", delta=None)
if ws_connected:
    col2.markdown('<span class="boss-status-on">● 扩展已连接</span>', unsafe_allow_html=True)
else:
    col2.markdown('<span class="boss-status-off">○ 扩展未连接</span>', unsafe_allow_html=True)
page_ready = runtime.get("page_ready", False)
if page_ready:
    col3.markdown('<span class="boss-status-on">● Boss页面就绪</span>', unsafe_allow_html=True)
else:
    col3.markdown('<span class="boss-status-off">○ 等待打开Boss沟通页</span>', unsafe_allow_html=True)
st.markdown("</div>", unsafe_allow_html=True)

# --- Collection Config & Controls ---
st.markdown('<div class="boss-panel"><h3>采集配置</h3>', unsafe_allow_html=True)
c1, c2, c3 = st.columns(3)
max_resumes = c1.number_input("最大采集数量", min_value=1, max_value=100, value=5, step=1)
interval_ms = c2.number_input("点击间隔（毫秒）", min_value=2000, max_value=30000, value=5000, step=1000)
c3.write("")

btn_cols = st.columns(4)
is_running = runtime.get("running", False)
is_paused = runtime.get("paused", False)

if btn_cols[0].button("开始采集", disabled=is_running or not ws_connected, type="primary"):
    bridge.start_collect({"max_resumes": max_resumes, "interval_ms": interval_ms})
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

st.markdown("</div>", unsafe_allow_html=True)

# --- Progress ---
st.markdown('<div class="boss-panel"><h3>采集进度</h3>', unsafe_allow_html=True)
p1, p2, p3, p4 = st.columns(4)
p1.metric("已下载", runtime.get("downloaded_count", 0))
p2.metric("已跳过", runtime.get("skipped_count", 0))
p3.metric("当前索引", runtime.get("current_index", 0))
status_text = "采集中" if is_running and not is_paused else ("已暂停" if is_paused else "空闲")
p4.metric("状态", status_text)
st.markdown("</div>", unsafe_allow_html=True)

# --- Logs ---
st.markdown('<div class="boss-panel"><h3>实时日志</h3>', unsafe_allow_html=True)
logs = runtime.get("logs", [])
if logs:
    log_html = ""
    for entry in logs[-50:]:
        level = entry.get("level", "info")
        css_class = "boss-log-error" if level == "error" else "boss-log-info"
        msg = entry.get("message", "")
        at = entry.get("at", "")
        log_html += f'<div class="{css_class}">[{at}] {msg}</div>'
    st.markdown(f'<div class="boss-log-box">{log_html}</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="boss-log-box"><span style="color:#94A3B8">等待采集开始...</span></div>', unsafe_allow_html=True)
st.markdown("</div>", unsafe_allow_html=True)

# --- Candidates ---
candidates = runtime.get("candidates", [])
if candidates:
    st.markdown('<div class="boss-panel"><h3>候选人列表</h3>', unsafe_allow_html=True)
    for c in reversed(candidates[-20:]):
        sig = c.get("signature", "")
        status = c.get("status", "")
        icon = "✅" if "download" in status else "⏭️"
        detail = c.get("file", "") or c.get("reason", "")
        st.markdown(f"{icon} **{sig}** — {detail}")
    st.markdown("</div>", unsafe_allow_html=True)

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
