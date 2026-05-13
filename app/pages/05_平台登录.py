import time

import streamlit as st

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter
from recruitment_assistant.schemas.raw_resume import RawResumeCreate
from recruitment_assistant.services.raw_resume_service import RawResumeService
from recruitment_assistant.storage.db import create_session

st.set_page_config(page_title="系统设置", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("系统设置")
page_header("系统设置", "管理账号登录态、采集策略、风控参数与通知方式。")

tabs = st.tabs(["账号", "采集", "风控", "通知"])

with tabs[0]:
    st.markdown('<div class="vibe-card"><h3>账号设置</h3>', unsafe_allow_html=True)
    platform_options = {
        "智联招聘": {"adapter": ZhilianAdapter, "snapshot_url": "https://rd5.zhaopin.com/"},
    }
    platform_name = st.selectbox("登录平台", list(platform_options.keys()), index=0, key="login_platform_name")
    account_name = st.text_input("账号标识", value="default", help="仅用于区分本地登录态文件，不需要填写真实密码。")
    adapters = {name: meta["adapter"](account_name=account_name or "default") for name, meta in platform_options.items()}
    adapter = adapters[platform_name]
    status_cols = st.columns(len(adapters))
    for index, (name, item_adapter) in enumerate(adapters.items()):
        item_user_data_dir = getattr(item_adapter, "user_data_dir", item_adapter.state_path.with_name(f"{item_adapter.state_path.stem}_profile"))
        item_login_status_key = f"{item_adapter.platform_code}_login_status_{account_name or 'default'}"
        item_artifact_saved = item_adapter.state_path.exists() or item_user_data_dir.exists()
        item_status = st.session_state.get(item_login_status_key) or ("已保存，待检测" if item_artifact_saved else "未保存")
        status_cols[index].metric(f"{name}登录态", item_status)
    c1, c2 = st.columns(2)
    user_data_dir = getattr(adapter, "user_data_dir", adapter.state_path.with_name(f"{adapter.state_path.stem}_profile"))
    c1.code(f"当前平台：{platform_name}\n登录态文件：{adapter.state_path}\n浏览器档案：{user_data_dir}")
    login_status_key = f"{adapter.platform_code}_login_status_{account_name or 'default'}"
    login_status = st.session_state.get(login_status_key)
    login_artifact_saved = adapter.state_path.exists() or user_data_dir.exists()
    c2.metric("当前平台登录态", login_status or ("已保存，待检测" if login_artifact_saved else "未保存"))
    if st.button("检测是否已登录"):
        with st.spinner("正在打开无头浏览器检测登录态..."):
            is_logged_in = adapter.is_logged_in()
        st.session_state[login_status_key] = "已登录" if is_logged_in else "未登录或已失效"
        st.success("已登录") if is_logged_in else st.error("未登录或登录态已失效")
        st.rerun()
    wait_seconds = st.number_input("等待人工登录秒数", min_value=30, max_value=900, value=180, step=30)
    if st.button(f"打开{platform_name}登录并保存登录态"):
        with st.spinner(f"请在弹出的{platform_name}页面完成人工登录，系统会自动保存登录态..."):
            state_path = adapter.login_manually(wait_seconds=int(wait_seconds), keep_open=False)
            is_logged_in = adapter.is_logged_in()
        st.session_state[login_status_key] = "已登录" if is_logged_in else "已保存，待验证"
        st.session_state[f"{adapter.platform_code}_login_state_updated_at"] = time.time()
        st.success(f"登录态已保存：{state_path}\n浏览器档案：{user_data_dir}")
        st.rerun()
    st.info("BOSS 采集已迁移到独立的 Chrome 扩展页面，请从左侧进入“BOSS采集”。")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="vibe-card"><h3>开发工具</h3>', unsafe_allow_html=True)
    snapshot_url = st.text_input("快照 URL", value=platform_options[platform_name]["snapshot_url"])
    snapshot_wait_seconds = st.number_input("快照等待秒数", min_value=5, max_value=120, value=30, step=5)
    if st.button(f"{platform_name}页面快照保存"):
        if not adapter.state_path.exists():
            st.error(f"登录态不存在，请先完成{platform_name}登录。")
        elif not hasattr(adapter, "capture_current_page"):
            st.warning(f"{platform_name}暂未提供页面快照工具。")
        else:
            with st.spinner(f"正在打开{platform_name}页面并保存 HTML 快照..."):
                data = adapter.capture_current_page(target_url=snapshot_url or None, wait_seconds=int(snapshot_wait_seconds))
                with create_session() as session:
                    raw_resume = RawResumeService(session).create_raw_resume(RawResumeCreate(**data))
            st.success(f"快照已保存 raw_resume_id={raw_resume.id}")
            st.code(data["raw_html_path"])
    st.markdown('</div>', unsafe_allow_html=True)

with tabs[1]:
    st.markdown('<div class="vibe-card"><h3>采集设置</h3>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    c1.number_input("单次采集上限", min_value=1, max_value=200, value=50)
    c2.selectbox("默认采集频率", ["手动", "每小时", "每天", "每周"])
    st.checkbox("自动去重", value=True)
    st.checkbox("下载后自动解析", value=True)
    st.markdown('</div>', unsafe_allow_html=True)

with tabs[2]:
    st.markdown('<div class="vibe-card"><h3>风控设置</h3>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    c1.slider("最小点击间隔（秒）", 1, 30, 5)
    c2.slider("最大点击间隔（秒）", 5, 120, 30)
    st.checkbox("启用随机等待", value=True)
    st.checkbox("异常时自动暂停任务", value=True)
    st.markdown('</div>', unsafe_allow_html=True)

with tabs[3]:
    st.markdown('<div class="vibe-card"><h3>通知设置</h3>', unsafe_allow_html=True)
    st.text_input("通知邮箱")
    st.text_input("Webhook 地址")
    st.multiselect("通知事件", ["任务完成", "任务失败", "登录态失效", "导出完成"], default=["任务完成", "登录态失效"])
    st.markdown('</div>', unsafe_allow_html=True)

if st.button("统一保存设置", type="primary"):
    st.success("设置已保存。")
