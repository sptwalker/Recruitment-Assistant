import streamlit as st

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.exporters.excel_exporter import export_candidates_excel
from recruitment_assistant.storage.db import create_session

st.set_page_config(page_title="数据导出", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("导出")
page_header("数据导出", "配置导出范围、字段、格式与下载历史。")

st.markdown('<div class="vibe-card"><h3>导出设置</h3>', unsafe_allow_html=True)
col1, col2 = st.columns(2)
with col1:
    export_range = st.selectbox("导出范围", ["全部候选人", "今日新增", "本周新增", "筛选结果"])
    export_format = st.selectbox("导出格式", ["Excel .xlsx", "CSV .csv"])
    file_name = st.text_input("命名规则", value="候选人导出_{日期}")
with col2:
    fields = st.multiselect(
        "导出字段",
        ["姓名", "手机号", "邮箱", "城市", "学历", "工作年限", "当前公司", "当前职位", "技能标签"],
        default=["姓名", "手机号", "邮箱", "城市", "学历", "当前职位"],
    )
    include_plain_contact = st.checkbox("包含手机号、邮箱明文", value=True)
    st.button("预览")
st.markdown('</div>', unsafe_allow_html=True)

if st.button("导出候选人 Excel"):
    with st.spinner("正在生成导出文件..."):
        with create_session() as session:
            output_path = export_candidates_excel(session, include_plain_contact=include_plain_contact)
    st.success(f"导出完成：{output_path}")

st.markdown('<div class="vibe-card"><h3>导出历史</h3>', unsafe_allow_html=True)
st.dataframe(
    [
        {"文件名": f"{file_name}.xlsx", "范围": export_range, "字段数": len(fields), "格式": export_format, "进度": "100%", "操作": "下载"},
        {"文件名": "候选人导出_昨日.xlsx", "范围": "本周新增", "字段数": 8, "格式": "Excel .xlsx", "进度": "86%", "操作": "等待"},
    ],
    use_container_width=True,
)
st.markdown('<div class="vibe-progress"><i style="width:86%"></i></div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)
