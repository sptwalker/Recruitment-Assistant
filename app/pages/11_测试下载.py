"""测试下载页面 — 针对特定候选人进行简历下载测试。

仅支持 BOSS 直聘平台，通过 set_target_candidates 精确过滤目标候选人。
输出详尽的调试日志，方便定位采集流程中的问题。
"""

import html
import json
import os
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from components.bridges import get_boss_bridge
from components.layout import get_theme_css, inject_vibe_style, page_header
from recruitment_assistant.config.settings import get_settings

st.set_page_config(page_title="测试下载", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("测试下载")
page_header(
    "测试下载",
    "针对特定候选人进行简历下载测试，输出详细日志方便定位问题。",
    icon="icon/boss直聘.png",
)

# ───── CSS ─────
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
.boss-log-box { height:460px; overflow-y:auto; background:var(--color-surface); border:1px solid var(--color-border); border-radius:12px; padding:9px 10px; font-family:Consolas,monospace; font-size:12px; line-height:1.42; white-space:pre-wrap; color:var(--color-text); }
.boss-auto-scroll-frame { width:100%; height:476px; border:0; display:block; }
.boss-empty-box { height:460px; overflow-y:auto; background:var(--color-surface); border:1px solid var(--color-border); border-radius:12px; padding:9px 10px; color:var(--color-text-muted); font-size:12px; }
.boss-log-highlight { color:#b45309 !important; font-weight:900; font-size:14px; }
.boss-log-info { color:var(--color-text); font-size:13px; }
.boss-log-success { color:#0a7d2e !important; font-weight:700; font-size:13px; }
.boss-log-error { color:var(--color-danger); font-weight:700; font-size:13px; }
.boss-log-skipped { color:#b45309 !important; font-weight:700; font-size:13px; }
.boss-log-stat { color:var(--color-primary); font-weight:700; font-size:13px; }
.boss-log-debug { color:#6b7280; font-size:12px; }
.plain-section-title { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:18px 0 10px; }
.plain-section-title h3 { margin:0; font-size:18px; line-height:1.3; color:var(--color-text); }
.collect-panel-stat { color:var(--color-primary); font-size:14px; font-weight:700; white-space:nowrap; }
.test-target-box { background:var(--color-bg-soft); border:1px solid var(--color-border); border-radius:10px; padding:10px 12px; font-size:13px; line-height:1.5; }
.test-target-tag { display:inline-block; background:var(--color-primary); color:#fff; border-radius:6px; padding:2px 10px; margin:2px 4px 2px 0; font-size:12px; font-weight:700; }
.debug-block { background:var(--color-bg-soft); border:1px solid var(--color-border); border-radius:10px; padding:10px 12px; font-family:Consolas,monospace; font-size:11px; line-height:1.45; color:var(--color-text-secondary); max-height:200px; overflow-y:auto; white-space:pre-wrap; word-break:break-all; }
.candidate-detail-table { width:100%; border-collapse:collapse; font-size:12px; }
.candidate-detail-table th { text-align:left; padding:6px 8px; background:var(--color-bg-soft); border-bottom:1px solid var(--color-border); color:var(--color-text-secondary); font-weight:700; }
.candidate-detail-table td { padding:6px 8px; border-bottom:1px solid var(--color-border); color:var(--color-text); vertical-align:top; word-break:break-all; }
.candidate-detail-table tr:last-child td { border-bottom:0; }
.status-downloaded { color:#0a7d2e; font-weight:800; }
.status-skipped { color:#b45309; font-weight:800; }
.status-waiting { color:#6b7280; }
</style>
""",
    unsafe_allow_html=True,
)


# ───── Helpers ─────

def section_title(text: str, note: str = "") -> None:
    note_html = f'<span class="boss-section-note">{note}</span>' if note else ""
    st.markdown(f'<div class="boss-section-title">{text}{note_html}</div>', unsafe_allow_html=True)


def classify_log(message: str, level: str = "info") -> str:
    """更细致的日志分类，增加 debug 级别。"""
    if level == "success":
        return "boss-log-success"
    if level == "highlight" or any(tok in message for tok in [
        "附件简历调试", "发现弹出页面", "成功获取以下信息", "正在记录你的操作",
        "成功记录到你的点击操作", "学习任务已完成", "PDF iframe", "boss-svg", "捕获下载链接",
    ]):
        return "boss-log-highlight"
    if "附件按钮:" in message and ("unknown_resume" in message or "附件简历" in message or "开始识别弹出页面" in message):
        return "boss-log-highlight"
    if level == "error" or any(tok in message for tok in ["失败", "错误", "断开", "异常", "exception", "Error"]):
        return "boss-log-error"
    if any(tok in message for tok in ["跳过", "duplicate", "已索要", "索要简历", "resume_requested",
                                       "resume_request_clicked", "非目标候选人", "target_filter"]):
        return "boss-log-skipped"
    if any(tok in message for tok in ["保存归档", "保存", "已下载", "下载完成", "归档成功"]):
        return "boss-log-success"
    if any(tok in message for tok in ["统计", "采集完成", "扫描完成", "沟通职位"]):
        return "boss-log-stat"
    if level == "debug":
        return "boss-log-debug"
    return "boss-log-info"


def build_log_summary(runtime_state: dict) -> str:
    downloaded = runtime_state.get("downloaded_count", 0)
    scanned = runtime_state.get("scanned_count", 0)
    skipped = runtime_state.get("skipped_count", 0)
    request_count = runtime_state.get("resume_request_count", 0)
    parts = []
    if scanned:
        parts.append(f"扫描 {scanned}")
    if downloaded:
        parts.append(f"下载 {downloaded}")
    if skipped:
        parts.append(f"跳过 {skipped}")
    if request_count:
        parts.append(f"索要 {request_count}")
    return " ｜ ".join(parts) if parts else ""


def render_auto_scroll_html(body_html: str, anchor: str, height: int = 476) -> None:
    theme_css = get_theme_css()
    components.html(
        f"""<!DOCTYPE html><html><head><style>
{theme_css}
html,body{{margin:0;padding:0;background:transparent;overflow:hidden;}}
.boss-log-box{{height:{height - 16}px;overflow-y:auto;background:var(--color-surface);border:1px solid var(--color-border);border-radius:12px;padding:9px 10px;font-family:Consolas,monospace;font-size:12px;line-height:1.42;white-space:pre-wrap;color:var(--color-text);}}
.boss-log-highlight{{color:#b45309!important;font-weight:900;font-size:14px;}}
.boss-log-info{{color:var(--color-text);font-size:13px;}}
.boss-log-success{{color:#0a7d2e!important;font-weight:700;font-size:13px;}}
.boss-log-error{{color:var(--color-danger);font-weight:700;font-size:13px;}}
.boss-log-skipped{{color:#b45309!important;font-weight:700;font-size:13px;}}
.boss-log-stat{{color:var(--color-primary);font-weight:700;font-size:13px;}}
.boss-log-debug{{color:#6b7280;font-size:12px;}}
</style></head><body>
{body_html}
<div id="{anchor}"></div>
<script>
function scrollToBottom(){{
  var el=document.getElementById('{anchor}');
  if(el)el.scrollIntoView({{behavior:'auto',block:'end'}});
  var box=document.querySelector('.boss-log-box');
  if(box)box.scrollTop=box.scrollHeight;
}}
requestAnimationFrame(scrollToBottom);
setTimeout(scrollToBottom,50);
setTimeout(scrollToBottom,200);
</script></body></html>""",
        height=height,
        scrolling=False,
    )


def render_log_title_with_copy(title: str, summary: str, copy_payload: str) -> None:
    summary_html = f'<span class="collect-panel-stat">{html.escape(summary)}</span>' if summary else ""
    escaped_payload = json.dumps(copy_payload)
    components.html(
        f"""<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:26px;margin:0 0 4px;">
<strong style="font-size:18px;font-weight:700;">{title}</strong>
{summary_html}
<button onclick="navigator.clipboard.writeText({escaped_payload})" style="cursor:pointer;background:transparent;border:1px solid #ccc;border-radius:6px;padding:2px 10px;font-size:11px;color:#666;">COPY</button>
</div>""",
        height=34,
        scrolling=False,
    )


# ───── Bridge ─────

bridge = get_boss_bridge()
runtime = bridge.runtime_state
ws_connected = bridge.ws_server.is_extension_connected
is_running = runtime.get("running", False)
is_paused = runtime.get("paused", False)


# ───── 目标候选人 ─────

section_title("目标候选人", "输入需要测试下载的候选人姓名，每行一个")
with st.container(border=True):
    input_cols = st.columns([1, 1])
    with input_cols[0]:
        candidate_text = st.text_area(
            "候选人姓名列表",
            value=st.session_state.get("test_target_names", ""),
            height=120,
            placeholder="张三\n李四\n王五\n\n每行填写一个候选人姓名，采集时仅下载匹配的简历",
            key="test_candidate_input",
        )
        names = {n.strip() for n in candidate_text.strip().splitlines() if n.strip()}
        st.session_state["test_target_names"] = candidate_text

    with input_cols[1]:
        if names:
            tags = "".join(f'<span class="test-target-tag">{html.escape(n)}</span>' for n in sorted(names))
            st.markdown(
                f'<div class="test-target-box">'
                f'<strong>将筛选以下候选人（共 {len(names)} 人）：</strong><br/>{tags}'
                f'<br/><br/>采集过程中，扩展会逐个浏览候选人沟通列表，遇到姓名匹配的候选人才会触发简历下载，其余跳过。'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.warning("请在左侧输入至少一个候选人姓名后再开始测试。")

        # 当前 bridge 内部的目标候选人白名单状态
        internal_targets = getattr(bridge, "_target_candidate_names", set())
        if internal_targets:
            st.caption(f"Bridge 当前白名单：{', '.join(sorted(internal_targets))}")
        else:
            st.caption("Bridge 当前白名单：未设置（将接受所有候选人）")


# ───── 连接状态 ─────

section_title("连接状态")
with st.container(border=True):
    status_cols = st.columns([1.5, 1.5, 1, 1, 1])

    ws_listening = getattr(bridge.ws_server, "is_listening", False)
    startup_error = getattr(bridge.ws_server, "startup_error", "")
    page_ready = runtime.get("page_ready", False)

    if ws_listening:
        ws_value, ws_sub = "监听中", f"{bridge.ws_server.host}:{bridge.ws_server.port}"
        ws_state_class = "is-listening"
    elif startup_error:
        ws_value, ws_sub = "启动失败", startup_error
        ws_state_class = "is-error"
    else:
        ws_value, ws_sub = "未监听", "-"
        ws_state_class = "is-idle"

    status_cols[0].markdown(
        f'<div class="boss-status-banner one-line {ws_state_class}">'
        f'<span class="boss-status-label">WebSocket</span>'
        f'<span class="boss-status-value">{html.escape(ws_value)}</span>'
        f'<span class="boss-status-sub">{html.escape(ws_sub)}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    last_disconnect_reason = runtime.get("last_disconnect_reason") or ""
    if ws_connected:
        ext_html = '<div class="boss-info-text boss-status-on">● 扩展已连接</div>'
    else:
        ext_html = '<div class="boss-info-text boss-status-ext-disconnected">○ 未连接</div>'
        if last_disconnect_reason:
            ext_html += f'<div style="color:#b91c1c;font-size:11px;">{html.escape(last_disconnect_reason)}</div>'
    page_text = "● BOSS页面就绪" if page_ready else "○ 等待沟通页"
    page_class = "boss-status-on" if page_ready else "boss-status-off"
    page_url = runtime.get("page_url") or ""
    page_url_html = f'<div style="font-size:10px;color:var(--color-text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{html.escape(page_url)}</div>' if page_url else ""
    status_cols[1].markdown(
        f'<div class="boss-status-banner">'
        f'<div class="boss-status-pair">{ext_html}'
        f'<div class="boss-info-text {page_class}">{page_text}</div>{page_url_html}</div></div>',
        unsafe_allow_html=True,
    )

    status_cols[2].metric("扩展版本", runtime.get("extension_version") or "-")
    status_cols[3].metric("已下载", runtime.get("downloaded_count", 0))
    status_cols[4].metric("最近事件", runtime.get("last_event_at") or "-")

    act_cols = st.columns([1.2, 1.2, 1.2, 5.4])
    act_cols[0].link_button("打开 BOSS 沟通页", "https://www.zhipin.com/web/boss/chat")
    if act_cols[1].button("重新检测页面", disabled=not ws_connected):
        bridge.probe_page()
        st.rerun()
    if act_cols[2].button("重置日志", disabled=is_running):
        bridge.reset_run()
        st.rerun()


# ───── 测试控制 ─────

section_title("测试控制", "请先在浏览器中打开 BOSS 沟通页面并确保扩展已连接")
with st.container(border=True):
    can_start = bool(names) and ws_connected and not is_running
    if not names and not is_running:
        st.caption("⚠️ 请先在上方输入候选人姓名")

    btn_cols = st.columns([1.2, 1, 1, 1, 1.4, 3.4])

    if btn_cols[0].button("🚀 开始测试", disabled=not can_start, type="primary", use_container_width=True):
        # 设置目标候选人白名单
        bridge.set_target_candidates(names)
        # 以按数量模式启动，max = 目标人数 * 2（冗余量，因为扫描可能遇到非目标候选人）
        # 实际下载由白名单过滤控制，max_resumes 只决定最大扫描范围
        config = {
            "max_resumes": max(len(names) * 3, 20),
            "collect_mode": "按数量采集",
            "collect_minutes": 0,
        }
        bridge.start_collect(config)
        st.rerun()

    if btn_cols[1].button("暂  停", disabled=not is_running or is_paused, use_container_width=True):
        bridge.pause_collect()
        st.rerun()

    if btn_cols[2].button("继  续", disabled=not is_paused, use_container_width=True):
        bridge.resume_collect()
        st.rerun()

    if btn_cols[3].button("停  止", disabled=not is_running, use_container_width=True):
        bridge.stop_collect()
        bridge.clear_target_candidates()
        st.rerun()

    if btn_cols[4].button("打开简历目录", use_container_width=True):
        settings = get_settings()
        save_dir = (settings.attachment_dir / "boss" / datetime.now().strftime("%Y%m%d")).resolve()
        save_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(save_dir))
        except Exception as exc:
            st.error(f"打开目录失败：{exc}")
            st.code(str(save_dir))

    # ── 进度摘要行 ──
    skip_counts = runtime.get("skip_reason_counts", {})
    progress_parts = []
    progress_parts.append(f"扫描 {runtime.get('scanned_count', 0)}")
    progress_parts.append(f"当前索引 {runtime.get('current_index', 0)}")
    progress_parts.append(f"下载 {runtime.get('downloaded_count', 0)}")
    progress_parts.append(f"跳过 {runtime.get('skipped_count', 0)}")
    progress_parts.append(f"索要 {runtime.get('resume_request_count', 0)}")
    if skip_counts:
        skip_detail = "；".join(f"{k}={v}" for k, v in skip_counts.items())
        progress_parts.append(f"跳过明细: {skip_detail}")
    st.caption(" ｜ ".join(progress_parts))

    # ── 日志 + 候选人详情 ──
    result_cols = st.columns([1.2, 0.8])

    with result_cols[0]:
        logs = runtime.get("logs", [])
        copy_payload = "\n".join(
            f"[{entry.get('at', '')}] [{entry.get('level', 'info')}] {entry.get('message', '')}"
            for entry in logs[-200:]
        )
        render_log_title_with_copy("实时日志", build_log_summary(runtime), copy_payload)
        if logs:
            log_html = ""
            for entry in logs[-200:]:
                level = entry.get("level", "info")
                raw_msg = entry.get("message", "")
                msg = html.escape(raw_msg)
                at = html.escape(entry.get("at", ""))
                css_class = classify_log(raw_msg, level)
                log_html += f'<div class="{css_class}">[{at}] [{level}] {msg}</div>'
            if is_running:
                render_auto_scroll_html(f'<div class="boss-log-box">{log_html}</div>', "test-log-bottom")
            else:
                st.markdown(f'<div class="boss-log-box">{log_html}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="boss-empty-box">等待测试开始...\n\n使用说明：\n1. 在上方输入目标候选人姓名（每行一个）\n2. 在浏览器中打开 BOSS 直聘沟通页面\n3. 确认扩展连接状态为"已连接"\n4. 点击"🚀 开始测试"按钮\n\n采集过程中，扩展会逐个浏览沟通列表：\n- 匹配目标姓名 → 触发简历下载\n- 不匹配 → 跳过并记录\n- 所有目标下载完成或列表扫描完毕后自动停止</div>', unsafe_allow_html=True)

    with result_cols[1]:
        # ── 目标候选人追踪 ──
        st.markdown('<div class="plain-section-title"><h3>目标追踪</h3></div>', unsafe_allow_html=True)
        candidates = runtime.get("candidates", [])

        if names:
            # 构建每个目标的状态
            target_status: dict[str, dict] = {n: {"status": "waiting", "file": "", "at": "", "path": ""} for n in sorted(names)}

            for c in candidates:
                if not isinstance(c, dict):
                    continue
                c_name = (c.get("info", {}).get("name", "") or c.get("name", "")).strip()
                c_sig = c.get("signature", "")
                c_status = c.get("status", "")

                for target in names:
                    if target in c_name or c_name in target:
                        if c_status == "downloaded":
                            target_status[target] = {
                                "status": "downloaded",
                                "file": c.get("file", ""),
                                "at": c.get("at", ""),
                                "path": c.get("path", ""),
                                "signature": c_sig,
                                "size": c.get("file_size_bytes", 0),
                            }
                        elif c_status == "skipped":
                            target_status[target] = {
                                "status": "skipped",
                                "reason": c.get("skip_reason", ""),
                                "signature": c_sig,
                                "at": c.get("at", ""),
                            }

            # 渲染状态表格
            table_html = '<table class="candidate-detail-table"><tr><th>姓名</th><th>状态</th><th>详情</th></tr>'
            for tname, info in target_status.items():
                status = info["status"]
                if status == "downloaded":
                    s_html = '<span class="status-downloaded">✅ 已下载</span>'
                    file_short = info.get("file", "")
                    size_kb = (info.get("size", 0) or 0) / 1024
                    detail = f'{file_short}<br/><span style="font-size:10px;color:#888;">{size_kb:.0f} KB · {info.get("at", "")}</span>'
                elif status == "skipped":
                    s_html = '<span class="status-skipped">⚠️ 跳过</span>'
                    detail = info.get("reason", "") or info.get("signature", "")
                else:
                    s_html = '<span class="status-waiting">⏳ 等待</span>'
                    detail = "尚未扫描到"
                table_html += f'<tr><td><strong>{html.escape(tname)}</strong></td><td>{s_html}</td><td style="font-size:11px;">{detail}</td></tr>'
            table_html += '</table>'
            st.markdown(table_html, unsafe_allow_html=True)

            # 进度指示
            done_count = sum(1 for v in target_status.values() if v["status"] == "downloaded")
            st.caption(f"完成进度：{done_count} / {len(names)}")
        else:
            st.caption("请先输入目标候选人姓名")

        # ── 运行状态 ──
        st.divider()
        if is_running and not is_paused:
            st.markdown("🟢 **采集进行中...**")
            started = runtime.get("run_started_at", "")
            if started:
                st.caption(f"开始于 {started}")
            run_id = runtime.get("run_id", "")
            if run_id:
                st.caption(f"Run ID: {run_id}")
        elif is_paused:
            st.markdown("🟡 **已暂停**")
        else:
            finish_reason = runtime.get("finish_reason", "")
            if finish_reason:
                st.markdown(f"⚪ **已停止** — {finish_reason}")
            else:
                st.markdown("⚪ **未运行**")

        # ── 已处理的候选人明细 ──
        if candidates:
            st.divider()
            st.markdown('<div class="plain-section-title"><h3>已处理候选人</h3></div>', unsafe_allow_html=True)
            detail_html = '<table class="candidate-detail-table"><tr><th>#</th><th>签名</th><th>状态</th><th>文件</th></tr>'
            for idx, c in enumerate(candidates[-30:], 1):
                if isinstance(c, dict):
                    sig = html.escape(c.get("signature", "?"))
                    st_val = c.get("status", "?")
                    cls = "status-downloaded" if st_val == "downloaded" else ("status-skipped" if st_val == "skipped" else "")
                    fname = html.escape(c.get("file", "") or c.get("skip_reason", "") or "-")
                    detail_html += f'<tr><td>{idx}</td><td style="font-size:11px;">{sig}</td><td><span class="{cls}">{st_val}</span></td><td style="font-size:11px;">{fname}</td></tr>'
            detail_html += '</table>'
            st.markdown(detail_html, unsafe_allow_html=True)
            if len(candidates) > 30:
                st.caption(f"（仅显示最近 30 条，共 {len(candidates)} 条）")


# ───── 调试信息 ─────

section_title("调试信息")
with st.container(border=True):
    debug_cols = st.columns([1, 1])

    with debug_cols[0]:
        st.markdown("**Runtime State 关键字段**")
        debug_fields = {
            "running": runtime.get("running"),
            "paused": runtime.get("paused"),
            "run_id": runtime.get("run_id", ""),
            "task_id": runtime.get("task_id"),
            "task_status": runtime.get("task_status", ""),
            "downloaded_count": runtime.get("downloaded_count", 0),
            "skipped_count": runtime.get("skipped_count", 0),
            "scanned_count": runtime.get("scanned_count", 0),
            "current_index": runtime.get("current_index", 0),
            "resume_request_count": runtime.get("resume_request_count", 0),
            "dedup_record_count": runtime.get("dedup_record_count", 0),
            "dedup_baseline": runtime.get("dedup_record_count_baseline", 0),
            "extension_connected": runtime.get("extension_connected"),
            "extension_version": runtime.get("extension_version", ""),
            "page_ready": runtime.get("page_ready"),
            "page_url": runtime.get("page_url", ""),
            "last_event_at": runtime.get("last_event_at", ""),
            "last_heartbeat_at": runtime.get("last_heartbeat_at", ""),
            "last_disconnect_reason": runtime.get("last_disconnect_reason", ""),
            "finish_reason": runtime.get("finish_reason", ""),
            "finish_status": runtime.get("finish_status", ""),
            "log_count": len(runtime.get("logs", [])),
            "candidate_count": len(runtime.get("candidates", [])),
        }
        st.markdown(
            f'<div class="debug-block">{html.escape(json.dumps(debug_fields, ensure_ascii=False, indent=2, default=str))}</div>',
            unsafe_allow_html=True,
        )

    with debug_cols[1]:
        st.markdown("**跳过原因统计**")
        skip_counts = runtime.get("skip_reason_counts", {})
        if skip_counts:
            st.markdown(
                f'<div class="debug-block">{html.escape(json.dumps(skip_counts, ensure_ascii=False, indent=2))}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.caption("暂无跳过记录")

        st.markdown("**事件日志文件**")
        log_file = runtime.get("log_file", "")
        if log_file and Path(log_file).exists():
            st.code(log_file, language=None)
            file_size = Path(log_file).stat().st_size
            st.caption(f"文件大小: {file_size / 1024:.1f} KB")
            if st.button("📋 查看最近事件日志", key="view_event_log"):
                try:
                    lines = Path(log_file).read_text(encoding="utf-8").strip().splitlines()
                    recent = lines[-20:] if len(lines) > 20 else lines
                    formatted = []
                    for line in recent:
                        try:
                            obj = json.loads(line)
                            formatted.append(json.dumps(obj, ensure_ascii=False, indent=1, default=str))
                        except json.JSONDecodeError:
                            formatted.append(line)
                    st.code("\n---\n".join(formatted), language="json")
                except Exception as exc:
                    st.error(f"读取日志失败: {exc}")
        elif log_file:
            st.caption(f"日志路径: {log_file}（文件尚未创建）")
        else:
            st.caption("未启动采集，无日志文件")

        st.markdown("**简历存储目录**")
        settings = get_settings()
        save_dir = settings.attachment_dir / "boss" / datetime.now().strftime("%Y%m%d")
        st.code(str(save_dir.resolve()), language=None)
        if save_dir.exists():
            files = list(save_dir.glob("*"))
            st.caption(f"今日已存储 {len(files)} 个文件")
        else:
            st.caption("目录尚未创建")

        st.markdown("**Bridge 内部状态**")
        bridge_info = {
            "target_candidates": sorted(getattr(bridge, "_target_candidate_names", set())),
            "seen_records_count": len(getattr(bridge, "_seen_candidate_records", set())),
            "event_seq": getattr(bridge, "_event_seq", 0),
        }
        st.markdown(
            f'<div class="debug-block">{html.escape(json.dumps(bridge_info, ensure_ascii=False, indent=2, default=str))}</div>',
            unsafe_allow_html=True,
        )


# ── Auto-refresh during collection ──
if is_running:
    time.sleep(1.5)
    st.rerun()
