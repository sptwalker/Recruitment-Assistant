import time

import streamlit as st
import streamlit.components.v1 as components

from components.layout import (
    get_current_theme_id,
    get_theme_css,
    inject_vibe_style,
    list_theme_options,
    page_header,
    save_current_theme,
)
from recruitment_assistant.config.settings import get_settings


st.set_page_config(page_title="系统设置", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("系统设置")
page_header("系统设置", "配置 AI 大模型与界面主题风格。")


@st.dialog("API Key测试")
def open_ai_api_key_test_dialog():
    config = st.session_state.get("ai_api_test_config") or {}
    api_key = (config.get("api_key") or "").strip()
    base_url = (config.get("base_url") or "").strip()
    model = (config.get("model") or "").strip()

    st.markdown("### AI API 接口检测")
    st.write(f"API Base URL：`{base_url or '-'}`")
    st.write(f"模型名称：`{model or '-'}`")

    if not api_key:
        st.error("API Key 为空，请先输入 API Key。")
        return
    if not base_url:
        st.error("API Base URL 为空，请先填写接口地址。")
        return
    if not model:
        st.error("模型名称为空，请先填写模型名称。")
        return

    try:
        from openai import OpenAI

        started_at = time.perf_counter()
        with st.spinner("正在调用 AI API 进行连通性检测..."):
            client = OpenAI(api_key=api_key, base_url=base_url)
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是接口连通性检测助手，只回复 OK。"},
                    {"role": "user", "content": "请回复 OK，用于测试 API Key 是否可用。"},
                ],
                temperature=0,
                max_tokens=8,
                timeout=20,
            )
        elapsed = time.perf_counter() - started_at
        content = (resp.choices[0].message.content or "").strip()
        st.success("AI API 接口正常，API Key 可用。")
        st.write(f"响应耗时：`{elapsed:.2f}s`")
        st.write(f"接口返回：`{content or '空响应'}`")
    except Exception as exc:
        st.error("AI API 接口检测失败，当前 API Key 或接口配置不可用。")
        st.code(str(exc))


tabs = st.tabs(["AI模型", "主题风格"])


with tabs[0]:
    st.markdown('<div class="vibe-card"><h3>AI 大模型配置</h3>', unsafe_allow_html=True)
    st.caption("用于简历结构化解析、岗位匹配等 AI 功能。支持 DeepSeek / 通义千问 / OpenAI 等兼容 OpenAI 格式的 API。")
    from pathlib import Path
    env_path = Path(".env")
    env_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    def _get_env_value(key: str) -> str:
        for line in env_lines:
            if line.strip().startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
        return ""
    current_key = _get_env_value("AI_API_KEY")
    current_url = _get_env_value("AI_BASE_URL") or "https://api.deepseek.com/v1"
    current_model = _get_env_value("AI_MODEL") or "deepseek-chat"

    api_key_col, api_test_col = st.columns([0.78, 0.22])
    with api_key_col:
        ai_api_key = st.text_input("API Key", value=current_key, type="password", key="ai_api_key_input",
                                   help="DeepSeek / 通义千问 / OpenAI 的 API Key")
    api_test_col.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
    test_ai_api_clicked = api_test_col.button("API检测", key="test_ai_api_key", use_container_width=True)
    presets = {
        "DeepSeek": ("https://api.deepseek.com/v1", "deepseek-chat"),
        "通义千问": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
        "OpenAI": ("https://api.openai.com/v1", "gpt-4o-mini"),
        "自定义": (current_url, current_model),
    }
    preset_choice = st.selectbox("预设模型", list(presets.keys()), key="ai_preset",
                                 index=0 if "deepseek" in current_url else (1 if "dashscope" in current_url else (2 if "openai" in current_url else 3)))
    preset_url, preset_model = presets[preset_choice]
    ai_base_url = st.text_input("API Base URL", value=preset_url, key="ai_base_url_input")
    ai_model = st.text_input("模型名称", value=preset_model, key="ai_model_input")
    if test_ai_api_clicked:
        st.session_state["ai_api_test_config"] = {
            "api_key": ai_api_key,
            "base_url": ai_base_url,
            "model": ai_model,
        }
        open_ai_api_key_test_dialog()

    if st.button("💾 保存 AI 配置", key="save_ai_config"):
        new_env = {"AI_API_KEY": ai_api_key, "AI_BASE_URL": ai_base_url, "AI_MODEL": ai_model}
        existing = {}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()
        existing.update(new_env)
        env_path.write_text("\n".join(f"{k}={v}" for k, v in existing.items()) + "\n", encoding="utf-8")
        get_settings.cache_clear()
        st.success("AI 配置已保存到 .env 文件，配置缓存已刷新；请重新进入简历管理后再执行自动解析入库。")
    st.markdown('</div>', unsafe_allow_html=True)

with tabs[1]:
    st.markdown('<div class="vibe-card"><h3>主题风格</h3>', unsafe_allow_html=True)
    themes = list_theme_options()
    if not themes:
        st.warning("未检测到主题样式文件，请检查 app/styles/themes 目录。")
    else:
        current_theme_id = get_current_theme_id()
        theme_ids = [theme["id"] for theme in themes]
        theme_names = {theme["id"]: theme["name"] for theme in themes}
        theme_descriptions = {theme["id"]: theme.get("description", "") for theme in themes}
        select_col, action_col, save_col, spacer_col = st.columns([0.28, 0.16, 0.16, 0.40])
        selected_theme_id = select_col.selectbox(
            "预设主题风格",
            theme_ids,
            index=theme_ids.index(current_theme_id) if current_theme_id in theme_ids else 0,
            format_func=lambda theme_id: theme_names.get(theme_id, theme_id),
            key="theme_style_select",
        )
        action_col.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
        save_col.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
        if action_col.button("应用主题", type="primary", key="apply_theme_style"):
            save_current_theme(selected_theme_id)
            st.success(f"已应用主题：{theme_names.get(selected_theme_id, selected_theme_id)}")
            st.rerun()
        if save_col.button("统一保存设置", key="save_all_settings_theme_tab"):
            st.success("设置已保存。")
        st.caption(theme_descriptions.get(selected_theme_id) or "选择主题后可在下方快速预览标题、正文、按钮（主/次/禁用）、输入框、下拉框、+/- 数字框、Banner 与进度条。")
        preview_css = get_theme_css(selected_theme_id)
        components.html(
            f"""
<style>
{preview_css}
body {{ margin: 0; font-family: var(--font-family-base, sans-serif); background: transparent; color: var(--color-text); }}
.theme-preview {{ background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius-xl); padding: 22px; box-shadow: var(--shadow-md); }}
.theme-preview-banner {{ padding: 24px; border-radius: var(--radius-lg); background: linear-gradient(135deg, var(--color-primary), var(--color-secondary)); color: #fff; margin-bottom: 18px; }}
.theme-preview-banner h2 {{ margin: 0 0 8px; font-size: 24px; }}
.theme-preview-banner p {{ margin: 0; opacity: .9; }}
.theme-preview-grid {{ display: grid; grid-template-columns: 1.2fr .8fr; gap: 16px; }}
.theme-preview-card {{ background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-lg); padding: 18px; box-shadow: var(--shadow-sm); }}
.theme-preview-title {{ margin: 0 0 8px; font-size: 20px; color: var(--color-text); }}
.theme-preview-text {{ margin: 0 0 16px; line-height: 1.7; color: var(--color-text-secondary); }}
.theme-preview-actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0 18px; }}
.theme-preview-btn {{ border: 0; border-radius: var(--radius-md); padding: 10px 16px; background: var(--color-primary); color: #fff; font-weight: 700; box-shadow: var(--shadow-xs); cursor: pointer; }}
.theme-preview-btn.secondary {{ background: var(--color-primary-soft); color: var(--color-primary); border: 1px solid var(--color-border); }}
.theme-preview-btn:disabled, .theme-preview-btn.disabled {{ background: var(--color-surface-muted, var(--color-bg-soft)); color: var(--color-text-muted); border: 1px solid var(--color-border); box-shadow: none; cursor: not-allowed; opacity: .7; }}
.theme-preview-input, .theme-preview-select {{ width: 100%; box-sizing: border-box; border: 1px solid var(--color-border); border-radius: var(--radius-md); padding: 11px 12px; margin-bottom: 12px; background: var(--color-surface); color: var(--color-text); outline: none; }}
.theme-preview-stepper-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }}
.theme-preview-stepper-label {{ color: var(--color-text-secondary); font-size: 13px; font-weight: 600; min-width: 56px; }}
.theme-preview-stepper {{ display: inline-flex; align-items: stretch; border: 1px solid var(--color-border); border-radius: var(--radius-md); overflow: hidden; background: var(--color-surface); }}
.theme-preview-stepper button {{ border: 0; background: var(--color-primary-soft); color: var(--color-primary); width: 36px; font-size: 18px; font-weight: 800; cursor: pointer; transition: background .15s ease, color .15s ease; }}
.theme-preview-stepper button:hover {{ background: var(--color-primary); color: #fff; }}
.theme-preview-stepper input {{ width: 64px; text-align: center; border: 0; border-left: 1px solid var(--color-border); border-right: 1px solid var(--color-border); background: var(--color-surface); color: var(--color-text); outline: none; padding: 8px 0; font-weight: 700; font-size: 14px; }}
.theme-preview-progress {{ height: 12px; background: var(--color-primary-soft); border-radius: 999px; overflow: hidden; margin-top: 8px; }}
.theme-preview-progress span {{ display: block; width: 68%; height: 100%; background: linear-gradient(90deg, var(--color-primary), var(--color-accent)); }}
.theme-preview-tags {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.theme-preview-tag {{ padding: 7px 10px; border-radius: 999px; background: var(--color-primary-soft); color: var(--color-primary); font-size: 12px; font-weight: 700; }}
@media (max-width: 720px) {{ .theme-preview-grid {{ grid-template-columns: 1fr; }} }}
</style>
<div class="theme-preview">
  <div class="theme-preview-banner">
    <h2>{theme_names.get(selected_theme_id, selected_theme_id)}</h2>
    <p>{theme_descriptions.get(selected_theme_id, "主题预览")}</p>
  </div>
  <div class="theme-preview-grid">
    <div class="theme-preview-card">
      <h3 class="theme-preview-title">招聘数据工作台</h3>
      <p class="theme-preview-text">统一管理候选人采集、简历解析、面试跟进与数据导出，快速感受当前主题在真实业务元素中的展示效果。</p>
      <div class="theme-preview-actions">
        <button class="theme-preview-btn">主按钮</button>
        <button class="theme-preview-btn secondary">次按钮</button>
        <button class="theme-preview-btn" disabled>禁用按钮</button>
      </div>
      <input class="theme-preview-input" value="候选人搜索输入框" />
      <select class="theme-preview-select"><option>下拉选择：全部岗位</option></select>
      <div class="theme-preview-stepper-row">
        <span class="theme-preview-stepper-label">每页数量</span>
        <div class="theme-preview-stepper">
          <button type="button">−</button>
          <input value="20" readonly />
          <button type="button">+</button>
        </div>
      </div>
    </div>
    <div class="theme-preview-card">
      <h3 class="theme-preview-title">任务进度</h3>
      <p class="theme-preview-text">当前采集任务完成度 68%</p>
      <div class="theme-preview-progress"><span></span></div>
      <div style="height:14px"></div>
      <div class="theme-preview-tags"><span class="theme-preview-tag">成功</span><span class="theme-preview-tag">待处理</span><span class="theme-preview-tag">高优先级</span></div>
    </div>
  </div>
</div>
""",
            height=490,
        )
    st.markdown('</div>', unsafe_allow_html=True)



