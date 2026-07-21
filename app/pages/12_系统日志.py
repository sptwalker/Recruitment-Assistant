"""系统日志 + AI 用量监测页面。

- 系统日志：按日期翻阅用户主要操作（采集/解析/匹配/邀约/评价/大纲）及结果、耗时。
- AI 用量监测：今日/累计调用次数与 token，按功能模块分组，按日期查明细。
"""
from datetime import date

import pandas as pd
import streamlit as st

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.storage.resume_db import init_resume_database
from recruitment_assistant.services import monitoring


@st.cache_resource
def _ensure_db() -> bool:
    init_resume_database()
    return True


_ensure_db()

st.set_page_config(page_title="系统日志", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("系统日志")
page_header("系统日志与 AI 用量", "记录采集/解析/匹配/面试等操作，以及所有 AI 调用的用量。")

tab_ops, tab_ai = st.tabs(["📋 系统日志", "🤖 AI 用量监测"])

# ============ 系统日志 ============
with tab_ops:
    day = st.date_input("查询日期", value=date.today(), key="ops_day")
    summ = monitoring.operation_summary(day)
    if summ:
        st.caption("当天操作汇总：" + " ｜ ".join(f"{k} {v}" for k, v in summ.items()))
    rows = monitoring.list_operations(day)
    if not rows:
        st.info("该日期暂无操作记录。")
    else:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(f"共 {len(rows)} 条")

# ============ AI 用量监测 ============
with tab_ai:
    s = monitoring.ai_usage_summary()
    m = st.columns(4)
    m[0].metric("今日调用次数", s["today_calls"])
    m[1].metric("今日 tokens", f"{s['today_tokens']:,}")
    m[2].metric("累计调用次数", s["all_calls"])
    m[3].metric("累计 tokens", f"{s['all_tokens']:,}")

    if s["by_feature"]:
        st.markdown("**按功能模块（累计）**")
        st.dataframe(pd.DataFrame(s["by_feature"]), use_container_width=True, hide_index=True)

    st.divider()
    aday = st.date_input("查询日期（调用明细）", value=date.today(), key="ai_day")
    arows = monitoring.list_ai_usage(aday)
    if not arows:
        st.info("该日期暂无 AI 调用记录。")
    else:
        st.dataframe(pd.DataFrame(arows), use_container_width=True, hide_index=True)
        st.caption(f"共 {len(arows)} 次调用")
