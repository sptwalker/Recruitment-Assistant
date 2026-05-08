import streamlit as st

from recruitment_assistant.config.settings import get_settings

settings = get_settings()

st.set_page_config(page_title="招聘网站助手", layout="wide")

st.title("招聘网站助手")
st.caption("智联招聘优先 · PostgreSQL · 本地单用户")

col1, col2, col3, col4 = st.columns(4)
col1.metric("首期平台", "智联招聘")
col2.metric("采集入口", "主动搜索 / 已投递")
col3.metric("单次上限", settings.crawler_max_resumes_per_task)
col4.metric("采集间隔", f"{settings.crawler_min_interval_seconds}-{settings.crawler_max_interval_seconds}s")

st.divider()

st.subheader("当前开发阶段")
st.success("P1：项目骨架与基础设施已创建。")

st.subheader("本地目录")
st.write(
    {
        "导出目录": str(settings.export_dir),
        "附件目录": str(settings.attachment_dir),
        "登录态目录": str(settings.browser_state_dir),
        "页面快照目录": str(settings.snapshot_dir),
    }
)

st.subheader("下一步")
st.markdown(
    """
- 创建完整数据库 ORM 模型与迁移
- 创建候选人、岗位、简历基础页面
- 实现 Word 岗位 JD 读取
- 实现智联招聘人工登录态保存
"""
)
