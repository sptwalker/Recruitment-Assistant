import streamlit as st

from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter

st.set_page_config(page_title="任务看板", layout="wide")
st.title("任务看板")

st.info("这里将展示智联招聘采集任务、进度、成功数量、失败数量与最近日志。")

st.subheader("智联招聘登录态")
adapter = ZhilianAdapter()
col1, col2, col3 = st.columns(3)
col1.metric("平台", "智联招聘")
col2.metric("登录态文件", "存在" if adapter.state_path.exists() else "不存在")

if col3.button("检测登录态"):
    with st.spinner("正在检测..."):
        st.success("已登录") if adapter.is_logged_in() else st.error("未登录或已失效")

st.caption("如需登录，请进入侧边栏 `平台登录` 页面。")
