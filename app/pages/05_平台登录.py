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
    account_name = st.text_input("账号标识", value="default", help="仅用于区分本地登录态文件，不需要填写真实密码。")
    adapter = ZhilianAdapter(account_name=account_name or "default")
    c1, c2 = st.columns(2)
    c1.code(str(adapter.state_path))
    c2.metric("登录态文件", "存在" if adapter.state_path.exists() else "不存在")
    if st.button("检测是否已登录"):
        with st.spinner("正在打开无头浏览器检测登录态..."):
            st.success("已登录") if adapter.is_logged_in() else st.error("未登录或登录态已失效")
    wait_seconds = st.number_input("等待人工登录秒数", min_value=30, max_value=600, value=180, step=30)
    if st.button("打开智联招聘登录并保存登录态"):
        with st.spinner("请在弹出的浏览器中完成人工登录..."):
            state_path = adapter.login_manually(wait_seconds=int(wait_seconds), keep_open=True)
        st.success(f"登录态已保存：{state_path}")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="vibe-card"><h3>开发工具</h3>', unsafe_allow_html=True)
    snapshot_url = st.text_input("快照 URL", value="https://rd5.zhaopin.com/")
    snapshot_wait_seconds = st.number_input("快照等待秒数", min_value=5, max_value=120, value=30, step=5)
    if st.button("智联页面快照保存"):
        if not adapter.state_path.exists():
            st.error("登录态不存在，请先完成智联登录。")
        else:
            with st.spinner("正在打开智联页面并保存 HTML 快照..."):
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
