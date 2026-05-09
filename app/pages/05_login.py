import streamlit as st

from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter

st.set_page_config(page_title="平台登录", layout="wide")
st.title("平台登录")
st.caption("首期支持智联招聘，采用人工扫码/短信登录后保存 Playwright 登录态。")

account_name = st.text_input("账号标识", value="default", help="仅用于区分本地登录态文件，不需要填写真实密码。")
adapter = ZhilianAdapter(account_name=account_name or "default")

col1, col2 = st.columns(2)

with col1:
    st.subheader("登录态文件")
    st.code(str(adapter.state_path))
    st.write("文件存在：", adapter.state_path.exists())

with col2:
    st.subheader("登录态检测")
    if st.button("检测是否已登录"):
        with st.spinner("正在打开无头浏览器检测登录态..."):
            st.success("已登录") if adapter.is_logged_in() else st.error("未登录或登录态已失效")

st.divider()

st.warning("点击下方按钮会在当前进程打开浏览器，并等待你完成人工登录。登录完成或等待超时后会保存登录态。")
wait_seconds = st.number_input("等待人工登录秒数", min_value=30, max_value=600, value=180, step=30)

if st.button("打开智联招聘登录并保存登录态"):
    with st.spinner("请在弹出的浏览器中完成人工登录..."):
        state_path = adapter.login_manually(wait_seconds=int(wait_seconds), keep_open=True)
    st.success(f"登录态已保存：{state_path}")
