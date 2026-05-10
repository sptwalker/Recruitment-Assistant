import streamlit as st

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter

settings = get_settings()
st.set_page_config(page_title="简历智采助手", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("首页")
page_header("首页", "用更轻、更稳的方式完成招聘平台采集、简历解析与数据导出。")


if "show_new_task_dialog" not in st.session_state:
    st.session_state.show_new_task_dialog = False
if st.query_params.get("new_task") == "1":
    st.session_state.show_new_task_dialog = True
    st.query_params.clear()


@st.dialog("新建采集任务", width="small")
def new_task_dialog() -> None:
    st.markdown('<div class="vibe-card"><h3>采集参数</h3>', unsafe_allow_html=True)
    target_site = st.selectbox("目标网站", ["智联招聘"])
    collect_target = st.radio("采集目标", ["指定数量简历", "所有新简历"], horizontal=True)
    max_resumes = None
    search_minutes = None
    if collect_target == "指定数量简历":
        max_resumes = st.number_input("简历数量", min_value=1, max_value=500, value=20, step=1)
    else:
        search_minutes = st.number_input("搜索时间（分钟）", min_value=10, max_value=900, value=60, step=10)
    speed_mode = st.radio("采集速度", ["快速采集（5-15s间隔）", "慢速采集（10-45s间隔）"], horizontal=True)
    account_name = st.text_input("账号标识", value="default")
    st.markdown('</div>', unsafe_allow_html=True)

    task_config = {
        "目标网站": target_site,
        "采集目标": collect_target,
        "简历数量": int(max_resumes) if max_resumes else None,
        "搜索时间分钟": int(search_minutes) if search_minutes else None,
        "采集速度": speed_mode,
        "账号标识": account_name or "default",
        "间隔秒": "5-15" if speed_mode.startswith("快速") else "10-45",
    }
    adapter = ZhilianAdapter(account_name=task_config["账号标识"])
    has_login_state = adapter.state_path.exists()
    button_label = "已登录开始任务" if has_login_state else "登录开始任务"

    if st.button(button_label, type="primary"):
        st.session_state.pending_collect_task = {
            **task_config,
            "任务状态": "等待启动",
        }
        st.session_state.auto_start_collect_task = True
        st.session_state.collect_task_logs = []
        st.session_state.collect_candidates = []
        st.session_state.collect_runtime_state = {
            "running": False,
            "paused": False,
            "stopped": False,
            "logs": [],
            "candidates": [],
            "skipped_count": 0,
            "task_config": st.session_state.pending_collect_task,
            "thread": None,
        }
        st.session_state.show_new_task_dialog = False
        st.switch_page("pages/06_智联采集.py")


col1, col2, col3 = st.columns(3)
with col1:
    st.markdown(
        """
<div class="vibe-card">
  <div class="vibe-icon">⌁</div>
  <h3>采集任务</h3>
  <p class="vibe-muted">配置智联采集入口、采集目标、速度与登录任务。</p>
  <div class="vibe-btn-row"><a class="vibe-primary-btn" href="/?new_task=1" target="_self">新建任务</a><a class="vibe-outline-btn" href="/智联采集" target="_self">查看列表</a></div>
  <p class="vibe-muted" style="margin-top:18px;">运行中 3 · 今日采集 126</p>
</div>
""",
        unsafe_allow_html=True,
    )
with col2:
    st.markdown(
        """
<div class="vibe-card">
  <div class="vibe-icon">☷</div>
  <h3>简历管理</h3>
  <p class="vibe-muted">解析候选人资料、技能标签与岗位匹配信息。</p>
  <div class="vibe-btn-row"><a class="vibe-primary-btn" href="/简历下载解析" target="_self">解析简历</a><a class="vibe-outline-btn" href="/候选人管理" target="_self">候选人库</a></div>
  <p class="vibe-muted" style="margin-top:18px;">总简历 1,284 · 待处理 42</p>
</div>
""",
        unsafe_allow_html=True,
    )
with col3:
    st.markdown(
        """
<div class="vibe-card">
  <div class="vibe-icon">⇩</div>
  <h3>数据导出</h3>
  <p class="vibe-muted">按范围、字段和格式生成可交付 Excel。</p>
  <div class="vibe-btn-row"><a class="vibe-primary-btn" href="/导出中心" target="_self">立即导出</a><a class="vibe-outline-btn" href="/导出中心" target="_self">导出历史</a></div>
  <p class="vibe-muted" style="margin-top:18px;">本周导出 18 · 成功率 98%</p>
</div>
""",
        unsafe_allow_html=True,
    )

if st.session_state.show_new_task_dialog:
    new_task_dialog()

st.write("")
s1, s2, s3 = st.columns(3)
for col, icon, label, value, trend in [
    (s1, "◌", "今日采集", "126", "+18% 较昨日"),
    (s2, "◇", "有效简历", "1,084", "84.4% 有效率"),
    (s3, "✓", "导出完成", "98%", "最近 24 小时"),
]:
    with col:
        st.markdown(
            f"""
<div class="vibe-soft-card">
  <div class="vibe-icon" style="width:36px;height:36px;font-size:18px;float:right;">{icon}</div>
  <div class="vibe-muted">{label}</div>
  <div class="vibe-stat">{value}</div>
  <div class="vibe-muted">{trend}</div>
</div>
""",
            unsafe_allow_html=True,
        )

st.divider()
col1, col2, col3, col4 = st.columns(4)
col1.metric("首期平台", "智联招聘")
col2.metric("采集入口", "主动搜索 / 已投递")
col3.metric("单次上限", settings.crawler_max_resumes_per_task)
col4.metric("采集间隔", f"{settings.crawler_min_interval_seconds}-{settings.crawler_max_interval_seconds}s")
