import streamlit as st

from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter
from recruitment_assistant.schemas.raw_resume import RawResumeCreate
from recruitment_assistant.services.raw_resume_service import RawResumeService
from recruitment_assistant.storage.db import create_session

st.set_page_config(page_title="智联采集", layout="wide")
st.title("智联采集")
st.caption("P4 初版：先保存当前页面 HTML 快照与 raw_resume 记录，用于确认页面结构和后续解析规则。")

account_name = st.text_input("账号标识", value="default")
target_url = st.text_input("打开 URL", value="https://rd5.zhaopin.com/")
wait_seconds = st.number_input("打开后等待秒数", min_value=5, max_value=120, value=30, step=5)

if st.button("打开页面并保存快照"):
    adapter = ZhilianAdapter(account_name=account_name or "default")
    if not adapter.state_path.exists():
        st.error("登录态不存在，请先进入 `平台登录` 完成登录。")
    else:
        with st.spinner("正在打开浏览器，等待页面加载并保存快照..."):
            data = adapter.capture_current_page(target_url=target_url or None, wait_seconds=int(wait_seconds))
            with create_session() as session:
                raw_resume = RawResumeService(session).create_raw_resume(RawResumeCreate(**data))
        st.success(f"已保存 raw_resume_id={raw_resume.id}")
        st.code(data["raw_html_path"])

st.divider()
st.subheader("最近原始页面记录")
with create_session() as session:
    rows = RawResumeService(session).list_raw_resumes(limit=50)

st.dataframe(
    [
        {
            "ID": row.id,
            "平台": row.platform_code,
            "URL": row.source_url,
            "快照": row.raw_html_path,
            "解析状态": row.parsed_status,
            "创建时间": row.created_at,
        }
        for row in rows
    ],
    use_container_width=True,
)
