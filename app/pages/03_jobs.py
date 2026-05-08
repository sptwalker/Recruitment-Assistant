from pathlib import Path
from tempfile import NamedTemporaryFile

import streamlit as st

from recruitment_assistant.schemas.job import JobPositionCreate
from recruitment_assistant.services.job_service import JobService
from recruitment_assistant.storage.db import create_session
from recruitment_assistant.utils.docx_utils import extract_docx_text

st.set_page_config(page_title="岗位管理", layout="wide")
st.title("岗位管理")

with st.expander("新增岗位 JD", expanded=True):
    uploaded_file = st.file_uploader("上传 Word 格式 JD", type=["docx"])
    extracted_text = ""
    source_file_name = None
    if uploaded_file is not None:
        source_file_name = uploaded_file.name
        with NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
            tmp.write(uploaded_file.getbuffer())
            tmp_path = Path(tmp.name)
        extracted_text = extract_docx_text(tmp_path)
        tmp_path.unlink(missing_ok=True)
        st.text_area("Word 文本预览", value=extracted_text, height=220)

    with st.form("job_form"):
        col1, col2, col3 = st.columns(3)
        job_name = col1.text_input("岗位名称 *")
        department = col2.text_input("部门")
        city = col3.text_input("城市")

        col4, col5 = st.columns(2)
        salary_min = col4.number_input("月薪下限", min_value=0, value=0, step=1000)
        salary_max = col5.number_input("月薪上限", min_value=0, value=0, step=1000)

        col6, col7, col8 = st.columns(3)
        degree = col6.text_input("学历要求")
        exp_min = col7.number_input("最低经验", min_value=0.0, max_value=60.0, value=0.0, step=0.5)
        exp_max = col8.number_input("最高经验", min_value=0.0, max_value=60.0, value=0.0, step=0.5)

        required_skills = st.text_input("必备技能，逗号分隔")
        preferred_skills = st.text_input("加分技能，逗号分隔")
        description = st.text_area("JD 文本", value=extracted_text, height=180)

        submitted = st.form_submit_button("保存岗位")
        if submitted:
            if not job_name.strip():
                st.error("岗位名称不能为空。")
            else:
                with create_session() as session:
                    JobService(session).create_job(
                        JobPositionCreate(
                            job_name=job_name,
                            department=department or None,
                            city=city or None,
                            salary_min=salary_min or None,
                            salary_max=salary_max or None,
                            degree_requirement=degree or None,
                            experience_min_years=exp_min,
                            experience_max_years=exp_max,
                            required_skills=[x.strip() for x in required_skills.split(",") if x.strip()],
                            preferred_skills=[x.strip() for x in preferred_skills.split(",") if x.strip()],
                            description=description or None,
                            source_file_name=source_file_name,
                        )
                    )
                st.success("岗位已保存。")

keyword = st.text_input("按岗位名称搜索")
with create_session() as session:
    jobs = JobService(session).list_jobs(keyword=keyword or None)

st.subheader("岗位列表")
st.dataframe(
    [
        {
            "ID": item.id,
            "岗位": item.job_name,
            "部门": item.department,
            "城市": item.city,
            "薪资下限": item.salary_min,
            "薪资上限": item.salary_max,
            "学历": item.degree_requirement,
            "最低经验": item.experience_min_years,
            "最高经验": item.experience_max_years,
            "来源文件": item.source_file_name,
            "状态": item.status,
        }
        for item in jobs
    ],
    use_container_width=True,
)
