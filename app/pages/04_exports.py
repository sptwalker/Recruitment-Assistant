import streamlit as st

from recruitment_assistant.exporters.excel_exporter import export_candidates_excel
from recruitment_assistant.storage.db import create_session

st.set_page_config(page_title="导出中心", layout="wide")
st.title("导出中心")
st.info("通用 Excel 导出默认包含手机号、邮箱明文。")

include_plain_contact = st.checkbox("包含手机号、邮箱明文", value=True)

if st.button("导出候选人 Excel"):
    with create_session() as session:
        output_path = export_candidates_excel(session, include_plain_contact=include_plain_contact)
    st.success(f"导出完成：{output_path}")
