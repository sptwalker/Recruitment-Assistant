import streamlit as st

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter

st.set_page_config(page_title="首页看板", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("首页")
page_header("首页看板", "实时查看采集任务、简历资产与导出状态。")

adapter = ZhilianAdapter()
col1, col2, col3 = st.columns(3)
for col, icon, title, desc, primary, secondary, stat in [
    (col1, "⌁", "采集任务", "统一管理任务状态、频率与平台授权。", "检测登录态", "进入采集", "运行中 3 · 队列 12"),
    (col2, "☷", "简历管理", "沉淀候选人画像、技能标签与解析结果。", "查看简历", "批量解析", "总量 1,284 · 新增 126"),
    (col3, "⇩", "数据导出", "按业务字段生成 Excel 与交付记录。", "导出 Excel", "查看历史", "成功 98% · 失败 2"),
]:
    with col:
        st.markdown(
            f"""
<div class="vibe-card">
  <div class="vibe-icon">{icon}</div><h3>{title}</h3>
  <p class="vibe-muted">{desc}</p>
  <div class="vibe-btn-row"><a class="vibe-primary-btn">{primary}</a><a class="vibe-outline-btn">{secondary}</a></div>
  <p class="vibe-muted" style="margin-top:18px;">{stat}</p>
</div>
""",
            unsafe_allow_html=True,
        )

st.write("")
s1, s2, s3 = st.columns(3)
summary = [
    (s1, "◌", "登录态", "存在" if adapter.state_path.exists() else "缺失", "智联招聘 default"),
    (s2, "◇", "今日采集", "126", "自动过滤重复候选人"),
    (s3, "✓", "解析成功率", "96%", "PDF/DOC/DOCX"),
]
for col, icon, label, value, desc in summary:
    with col:
        st.markdown(f'<div class="vibe-soft-card"><div class="vibe-icon">{icon}</div><div class="vibe-muted">{label}</div><div class="vibe-stat">{value}</div><div class="vibe-muted">{desc}</div></div>', unsafe_allow_html=True)

if st.button("检测登录态"):
    with st.spinner("正在检测..."):
        st.success("已登录") if adapter.is_logged_in() else st.error("未登录或已失效")
