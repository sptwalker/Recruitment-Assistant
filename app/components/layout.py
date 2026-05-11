import streamlit as st

PRIMARY = "#4A90E2"
BG = "#F5F7FA"
WHITE = "#FFFFFF"
SUCCESS_BG = "#E6F4EA"
ACCENT = "#FF9F43"
APP_VERSION = "V0.74"

MENU_ITEMS = [
    ("首页", "⌂", "/"),
    ("采集任务", "◌", "/智联采集"),
    ("简历管理", "◫", "/简历管理"),
    ("系统设置", "⚙", "/平台登录"),
]


def inject_vibe_style(active: str = "首页") -> None:
    menu_html = "".join(
        f'<a class="vibe-side-item {"active" if label == active else ""}" href="{href}" target="_self"><span>{icon}</span><b>{label}</b></a>'
        for label, icon, href in MENU_ITEMS
    )
    st.markdown(
        f"""
<style>
:root {{
  --primary:{PRIMARY}; --bg:{BG}; --white:{WHITE}; --success:{SUCCESS_BG}; --accent:{ACCENT};
  --text:#1F2937; --muted:#6B7280; --line:#E5EAF2; --danger:#E85D75; --warn:#FF9F43;
}}
* {{ font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }}
html, body, [data-testid="stAppViewContainer"] {{ background: var(--bg); color: var(--text); }}
[data-testid="stHeader"] {{ height: 0; background: transparent; }}
[data-testid="stSidebar"],
[data-testid="stSidebarNav"],
[data-testid="collapsedControl"],
button[kind="header"],
a[href="/首页看板"],
a[href="/候选人管理"],
a[href="/岗位管理"],
a[href="/导出中心"] {{ display:none !important; }}
section[data-testid="stSidebar"] {{ width:0 !important; min-width:0 !important; }}
[data-testid="stAppViewContainer"] {{ margin-left:0 !important; }}
.block-container {{ max-width: 1680px; padding: 84px 28px 48px 248px !important; }}
.vibe-topbar {{ position:fixed; top:0; left:0; right:0; z-index:9999; height:60px; background:rgba(255,255,255,.96); backdrop-filter:blur(16px); box-shadow:0 8px 24px rgba(31,41,55,.06); display:flex; align-items:center; justify-content:space-between; padding:0 28px; }}
.vibe-brand {{ display:flex; align-items:center; gap:12px; font-size:18px; font-weight:800; letter-spacing:.2px; }}
.vibe-logo {{ width:34px; height:34px; border-radius:12px; background:linear-gradient(135deg,var(--primary),#8FC4FF); display:grid; place-items:center; color:white; box-shadow:0 10px 20px rgba(74,144,226,.22); }}
.vibe-actions {{ display:flex; align-items:center; gap:20px; color:var(--muted); font-size:14px; }}
.vibe-actions a {{ color:var(--muted) !important; text-decoration:none !important; cursor:pointer; transition:.22s ease; }} .vibe-actions a:hover {{ color:var(--primary) !important; transform:translateY(-1px); }}
.vibe-avatar {{ width:34px; height:34px; border-radius:50%; background:var(--success); color:var(--primary); display:grid; place-items:center; font-weight:800; cursor:pointer; }}
.vibe-sidebar {{ position:fixed; top:60px; left:0; bottom:0; z-index:9998; width:220px; background:#F2F5F8; border-right:1px solid var(--line); padding:18px 10px; display:flex; flex-direction:column; transition:.24s ease; }}
.vibe-side-item {{ height:44px; border-radius:14px; display:flex; align-items:center; gap:12px; padding:0 14px; margin:4px 0; color:#5D6B7A !important; font-size:14px; cursor:pointer; border-left:3px solid transparent; transition:.2s ease; text-decoration:none !important; }}
.vibe-side-item span {{ width:20px; text-align:center; color:var(--primary); font-weight:600; }}
.vibe-side-item:hover {{ background:#EAF3FF; color:var(--primary); transform:translateX(2px); }}
.vibe-side-item.active {{ background:var(--success); color:var(--primary); border-left-color:var(--primary); font-weight:700; }}
.vibe-version {{ margin-top:auto; padding:12px 14px; color:#94A3B8; font-size:12px; }}
.vibe-page-title {{ display:flex; align-items:flex-end; justify-content:space-between; gap:16px; margin-bottom:18px; }}
.vibe-page-title h1 {{ margin:0; font-size:28px; line-height:1.2; letter-spacing:-.4px; }}
.vibe-page-title p {{ margin:6px 0 0; color:var(--muted); font-size:14px; }}
.vibe-card {{ background:var(--white); border:1px solid rgba(229,234,242,.9); border-radius:22px; padding:22px; box-shadow:0 12px 32px rgba(31,41,55,.05); transition:.24s ease; }}
.vibe-card:hover {{ transform:translateY(-3px); box-shadow:0 18px 40px rgba(31,41,55,.08); }}
.vibe-soft-card {{ background:#F7F9FC; border-radius:18px; padding:18px; border:1px solid var(--line); }}
.vibe-icon {{ width:42px; height:42px; border-radius:15px; background:#EAF3FF; color:var(--primary); display:grid; place-items:center; font-size:22px; margin-bottom:14px; }}
.vibe-stat {{ font-size:30px; font-weight:800; color:#172033; margin:6px 0; }}
.vibe-muted {{ color:var(--muted); font-size:13px; }}
.vibe-btn-row {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:16px; }}
.vibe-primary-btn,.vibe-outline-btn,.vibe-accent-btn {{ border-radius:12px; padding:9px 14px; font-size:13px; font-weight:700; display:inline-flex; align-items:center; gap:8px; transition:.18s ease; text-decoration:none; }}
.vibe-primary-btn {{ background:var(--primary); color:white !important; }} .vibe-outline-btn {{ border:1px solid var(--primary); color:var(--primary) !important; background:white; }} .vibe-accent-btn {{ background:var(--accent); color:white !important; }}
.vibe-primary-btn:hover,.vibe-outline-btn:hover,.vibe-accent-btn:hover {{ filter:brightness(.96); transform:translateY(-1px); }}
.vibe-pill {{ display:inline-flex; align-items:center; border-radius:999px; padding:5px 10px; font-size:12px; font-weight:700; background:#EAF3FF; color:var(--primary); margin:3px; }}
.vibe-pill.ok {{ background:var(--success); color:#168A45; }} .vibe-pill.warn {{ background:#FFF4E6; color:#C96E08; }} .vibe-pill.err {{ background:#FFECEF; color:#C73552; }}
.vibe-candidate {{ display:flex; align-items:center; gap:16px; background:white; border:1px solid var(--line); border-radius:20px; padding:16px 18px; margin:12px 0; box-shadow:0 10px 26px rgba(31,41,55,.04); transition:.2s ease; }}
.vibe-candidate:hover {{ transform:translateY(-2px); border-color:#C8DDF7; }}
.vibe-candidate-avatar {{ width:48px; height:48px; border-radius:50%; background:#EAF3FF; color:var(--primary); display:grid; place-items:center; font-weight:800; flex:0 0 auto; }}
.vibe-candidate-main {{ flex:1; }} .vibe-candidate-main strong {{ font-size:16px; }} .vibe-candidate-main p {{ margin:4px 0 0; color:var(--muted); font-size:13px; }}
.vibe-actions-icons {{ color:var(--muted); display:flex; gap:12px; font-size:18px; }}
.vibe-detail-grid {{ display:grid; grid-template-columns:280px 1fr; gap:18px; }}
.vibe-module {{ border-left:4px solid var(--primary); background:white; border-radius:16px; padding:16px; margin-bottom:12px; box-shadow:0 8px 22px rgba(31,41,55,.04); }}
.vibe-progress {{ height:8px; border-radius:99px; background:#E9EEF5; overflow:hidden; }} .vibe-progress i {{ display:block; height:100%; background:linear-gradient(90deg,var(--primary),#87BFFF); border-radius:99px; }}
.vibe-card-button-row [data-testid="column"] {{ width:auto !important; flex:0 0 auto !important; min-width:0 !important; }}
.vibe-card-button-row div.stButton > button {{ width:auto !important; min-height:34px !important; padding:7px 13px !important; font-size:13px !important; }}
div.stButton > button, div.stDownloadButton > button, [data-testid="stFormSubmitButton"] button {{ border-radius:12px !important; border:1px solid var(--primary) !important; background:var(--primary) !important; color:white !important; font-weight:700 !important; transition:.16s ease !important; }}
div.stButton > button:hover, div.stDownloadButton > button:hover, [data-testid="stFormSubmitButton"] button:hover {{ filter:brightness(.95); transform:translateY(-1px); }}
div.stButton > button:active, div.stDownloadButton > button:active {{ transform:scale(.98); }}
div.stButton > button:disabled {{ background:#D1D5DB !important; border-color:#D1D5DB !important; color:white !important; }}
[data-baseweb="input"]:focus-within, [data-baseweb="select"]:focus-within, textarea:focus {{ border-color:var(--primary) !important; box-shadow:0 0 0 3px rgba(74,144,226,.12) !important; }}
[data-testid="stDataFrame"] {{ border-radius:18px; overflow:hidden; border:1px solid var(--line); box-shadow:0 8px 24px rgba(31,41,55,.04); }}
.stTabs [data-baseweb="tab-list"] {{ gap:8px; }} .stTabs [data-baseweb="tab"] {{ border-radius:12px; padding:8px 16px; }}
@media (max-width:1200px) {{ .vibe-sidebar {{ width:60px; }} .vibe-side-item {{ justify-content:center; padding:0; }} .vibe-side-item b,.vibe-version {{ display:none; }} .block-container {{ padding-left:84px !important; padding-right:18px !important; }} .vibe-actions span {{ display:none; }} .vibe-detail-grid {{ grid-template-columns:1fr; }} }}
@media (max-width:760px) {{ .block-container {{ padding-top:76px !important; padding-left:78px !important; }} .vibe-page-title {{ display:block; }} .vibe-topbar {{ padding:0 14px; }} .vibe-card {{ padding:16px; border-radius:18px; }} }}
</style>
<div class="vibe-topbar">
  <div class="vibe-brand"><div class="vibe-logo">⌁</div><span>简历智采助手 {APP_VERSION}</span></div>
  <div class="vibe-actions"><a href="/智联采集" target="_self">⌁ 采集任务</a><a href="/简历管理" target="_self">☷ 简历管理</a><a href="/平台登录" target="_self">⚙ 系统设置</a><div class="vibe-avatar">HR</div></div>
</div>
<aside class="vibe-sidebar">{menu_html}<div class="vibe-version">Resume AI Collector<br/>{APP_VERSION}</div></aside>
""",
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str = "", action: str | None = None) -> None:
    action_html = f'<a class="vibe-accent-btn">{action}</a>' if action else ""
    st.markdown(
        f'<div class="vibe-page-title"><div><h1>{title}</h1><p>{subtitle}</p></div>{action_html}</div>',
        unsafe_allow_html=True,
    )


def toast(message: str, kind: str = "success") -> None:
    if kind == "error":
        st.error(message)
    elif kind == "warning":
        st.warning(message)
    else:
        st.success(message)
