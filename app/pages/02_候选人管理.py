import streamlit as st

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.schemas.candidate import CandidateCreate
from recruitment_assistant.services.candidate_service import CandidateService
from recruitment_assistant.storage.db import create_session

st.set_page_config(page_title="简历管理", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("简历管理")
page_header("简历管理", "筛选、查看并维护候选人简历与技能标签。")

with st.container():
    f1, f2, f3, f4, f5 = st.columns([1.1, 1, 1, 1.4, 1])
    platform = f1.selectbox("平台", ["全部", "智联招聘", "BOSS直聘", "前程无忧"])
    degree = f2.selectbox("学历", ["全部", "博士", "硕士", "本科", "大专"])
    years = f3.selectbox("年限", ["全部", "1-3年", "3-5年", "5-10年", "10年以上"])
    skill = f4.text_input("技能标签", placeholder="Python / Java / 销售")
    keyword = f5.text_input("搜索", placeholder="姓名/职位")
    b1, b2 = st.columns([1, 8])
    b1.button("搜索")
    b2.button("重置")

with st.expander("新增候选人", expanded=False):
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
        highest_degree = col7.text_input("最高学历")
        exp = col8.number_input("工作年限", min_value=0.0, max_value=60.0, value=0.0, step=0.5)
        col9, col10 = st.columns(2)
        company = col9.text_input("当前/最近公司")
        position = col10.text_input("当前/最近职位")
        submitted = st.form_submit_button("保存候选人")
        if submitted:
            if not name.strip():
                st.error("姓名不能为空。")
            else:
                with create_session() as session:
                    CandidateService(session).create_candidate(
                        CandidateCreate(
                            name=name,
                            gender=gender or None,
                            age=age or None,
                            phone_plain=phone or None,
                            email_plain=email or None,
                            current_city=city or None,
                            highest_degree=highest_degree or None,
                            years_of_experience=exp,
                            current_company=company or None,
                            current_position=position or None,
                        )
                    )
                st.success("候选人已保存。")

with create_session() as session:
    candidates = CandidateService(session).list_candidates(keyword=keyword or None)

st.markdown('<div class="vibe-card"><h3>候选人卡片列表</h3>', unsafe_allow_html=True)
for item in candidates:
    tags = [x for x in [item.highest_degree, f"{item.years_of_experience}年" if item.years_of_experience is not None else None, item.current_city, item.status] if x]
    tag_html = "".join(f'<span class="vibe-pill">{tag}</span>' for tag in tags[:5])
    st.markdown(
        f"""
<div class="vibe-candidate">
  <div class="vibe-candidate-avatar">{(item.name or '?')[:1]}</div>
  <div class="vibe-candidate-main"><strong>{item.name}</strong><p>{item.current_position or '待补充职位'} · {item.current_company or '待补充公司'} · {platform}</p>{tag_html}</div>
  <div class="vibe-actions-icons">⌕ ✎ ⋯</div>
</div>
""",
        unsafe_allow_html=True,
    )
st.markdown('</div>', unsafe_allow_html=True)

with st.expander("候选人详情（800px 弹窗样式预览）", expanded=False):
    st.markdown(
        """
<div class="vibe-detail-grid">
  <div class="vibe-soft-card"><h3>基础信息</h3><p class="vibe-muted">姓名、电话、邮箱、城市、学历与工作年限。</p></div>
  <div>
    <div class="vibe-module"><b>工作经历</b><p class="vibe-muted">最近公司、岗位与项目贡献。</p></div>
    <div class="vibe-module"><b>项目经历</b><p class="vibe-muted">核心项目、技术栈与产出。</p></div>
    <div class="vibe-module"><b>教育经历</b><p class="vibe-muted">学校、专业、学历。</p></div>
    <div class="vibe-module"><b>技能标签</b><p class="vibe-muted">自动解析和手动维护标签。</p></div>
    <div class="vibe-module"><b>匹配建议</b><p class="vibe-muted">岗位匹配度和风险提示。</p></div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )
