import streamlit as st

from recruitment_assistant.schemas.candidate import CandidateCreate
from recruitment_assistant.services.candidate_service import CandidateService
from recruitment_assistant.storage.db import create_session

st.set_page_config(page_title="候选人管理", layout="wide")
st.title("候选人管理")

with st.expander("新增候选人", expanded=True):
    with st.form("candidate_form"):
        col1, col2, col3 = st.columns(3)
        name = col1.text_input("姓名 *")
        gender = col2.selectbox("性别", ["", "男", "女", "未知"])
        age = col3.number_input("年龄", min_value=0, max_value=100, value=0)

        col4, col5 = st.columns(2)
        phone = col4.text_input("手机号明文")
        email = col5.text_input("邮箱明文")

        col6, col7, col8 = st.columns(3)
        city = col6.text_input("当前城市")
        degree = col7.text_input("最高学历")
        years = col8.number_input("工作年限", min_value=0.0, max_value=60.0, value=0.0, step=0.5)

        col9, col10 = st.columns(2)
        company = col9.text_input("当前/最近公司")
        position = col10.text_input("当前/最近职位")

        submitted = st.form_submit_button("保存候选人")
        if submitted:
            if not name.strip():
                st.error("姓名不能为空。")
            else:
                with create_session() as session:
                    service = CandidateService(session)
                    service.create_candidate(
                        CandidateCreate(
                            name=name,
                            gender=gender or None,
                            age=age or None,
                            phone_plain=phone or None,
                            email_plain=email or None,
                            current_city=city or None,
                            highest_degree=degree or None,
                            years_of_experience=years,
                            current_company=company or None,
                            current_position=position or None,
                        )
                    )
                st.success("候选人已保存。")

keyword = st.text_input("按姓名搜索")
with create_session() as session:
    candidates = CandidateService(session).list_candidates(keyword=keyword or None)

st.subheader("候选人列表")
st.dataframe(
    [
        {
            "ID": item.id,
            "姓名": item.name,
            "性别": item.gender,
            "年龄": item.age,
            "手机": item.phone_plain,
            "邮箱": item.email_plain,
            "城市": item.current_city,
            "学历": item.highest_degree,
            "工作年限": item.years_of_experience,
            "公司": item.current_company,
            "职位": item.current_position,
            "状态": item.status,
        }
        for item in candidates
    ],
    use_container_width=True,
)
