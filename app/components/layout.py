import json
from pathlib import Path

import streamlit as st

APP_VERSION = "V2.30"
STYLE_DIR = Path("app/styles")
STYLE_FILES = ("theme.css", "global.css", "components.css")
THEME_DIR = STYLE_DIR / "themes"
THEME_CONFIG_PATH = Path("data/theme_config.json")
DEFAULT_THEME_ID = "luxury_business"

MENU_ITEMS = [
    ("首页", "⌂", "/"),
    ("智联招聘采集", "◌", "/智联采集"),
    ("BOSS直聘采集", "◇", "/BOSS采集"),
    ("51前程无忧采集", "◈", "/51前程无忧采集"),
    ("简历管理", "◫", "/简历管理"),
    ("面试管理", "▣", "/面试管理"),
    ("系统设置", "⚙", "/平台登录"),
]

TOPBAR_LINKS = [
    ("⌁ 智联招聘采集", "/智联采集"),
    ("◇ BOSS直聘采集", "/BOSS采集"),
    ("◈ 51前程无忧采集", "/51前程无忧采集"),
    ("☷ 简历管理", "/简历管理"),
    ("▣ 面试管理", "/面试管理"),
    ("⚙ 系统设置", "/平台登录"),
]

THEME_CSS_HOOK = """
/* UI_THEME_EXTENSION_HOOK: 后续主题 CSS 统一接入入口，请在此处覆盖 :root 变量或扩展 .vibe-* 样式。 */
"""


def _read_css_file(file_name: str) -> str:
    try:
        return (STYLE_DIR / file_name).read_text(encoding="utf-8")
    except OSError:
        return ""


def _parse_theme_meta(css_path: Path) -> dict[str, str]:
    meta = {"id": css_path.stem, "name": css_path.stem, "description": ""}
    try:
        for line in css_path.read_text(encoding="utf-8").splitlines()[:12]:
            clean_line = line.strip().strip("/*").strip("*/").strip()
            if clean_line.startswith("theme-id:"):
                meta["id"] = clean_line.split(":", 1)[1].strip() or css_path.stem
            elif clean_line.startswith("theme-name:"):
                meta["name"] = clean_line.split(":", 1)[1].strip() or css_path.stem
            elif clean_line.startswith("theme-description:"):
                meta["description"] = clean_line.split(":", 1)[1].strip()
    except OSError:
        pass
    return meta


def list_theme_options() -> list[dict[str, str]]:
    if not THEME_DIR.exists():
        return []
    themes = [_parse_theme_meta(path) for path in sorted(THEME_DIR.glob("*.css"))]
    return sorted(themes, key=lambda item: (item["id"] != DEFAULT_THEME_ID, item["name"]))


def get_current_theme_id() -> str:
    if not THEME_CONFIG_PATH.exists():
        return DEFAULT_THEME_ID
    try:
        theme_id = json.loads(THEME_CONFIG_PATH.read_text(encoding="utf-8")).get("theme_id", DEFAULT_THEME_ID)
    except (OSError, json.JSONDecodeError, TypeError):
        return DEFAULT_THEME_ID
    theme_ids = {theme["id"] for theme in list_theme_options()}
    return theme_id if theme_id in theme_ids else DEFAULT_THEME_ID


def save_current_theme(theme_id: str) -> None:
    theme_ids = {theme["id"] for theme in list_theme_options()}
    if theme_id not in theme_ids:
        raise ValueError(f"未知主题：{theme_id}")
    THEME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    THEME_CONFIG_PATH.write_text(json.dumps({"theme_id": theme_id}, ensure_ascii=False, indent=2), encoding="utf-8")


def get_theme_css(theme_id: str | None = None) -> str:
    current_theme_id = theme_id or get_current_theme_id()
    try:
        return (THEME_DIR / f"{current_theme_id}.css").read_text(encoding="utf-8")
    except OSError:
        return ""


def _style_block() -> str:
    base_css = "\n".join(_read_css_file(file_name) for file_name in STYLE_FILES)
    theme_css = get_theme_css()
    return f"<style>\n{base_css}\n{theme_css}\n{THEME_CSS_HOOK}\n</style>"


def _topbar_html() -> str:
    links = "".join(f'<a href="{href}" target="_self">{label}</a>' for label, href in TOPBAR_LINKS)
    return f"""
<div class="vibe-topbar">
  <div class="vibe-brand"><div class="vibe-logo">⌁</div><span>简历智采助手 {APP_VERSION}</span></div>
  <div class="vibe-actions">{links}<div class="vibe-avatar">HR</div></div>
</div>
"""


def _sidebar_html(active: str) -> str:
    menu_html = "".join(
        f'<a class="vibe-side-item {"active" if label == active else ""}" href="{href}" target="_self"><span>{icon}</span><b>{label}</b></a>'
        for label, icon, href in MENU_ITEMS
    )
    return f'<aside class="vibe-sidebar">{menu_html}<div class="vibe-version">Resume AI Collector<br/>{APP_VERSION}</div></aside>'


def inject_vibe_style(active: str = "首页") -> None:
    st.markdown(_style_block() + _topbar_html() + _sidebar_html(active), unsafe_allow_html=True)


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

