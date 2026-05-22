import time
from pathlib import Path

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
from components.theme_preview import (
    COMPONENT_LABELS,
    COMPONENT_VARIABLE_MAP,
    build_preview_html,
    parse_theme_variables,
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


@st.dialog("保存自定义主题")
def open_save_theme_dialog():
    overrides = st.session_state.get("theme_overrides", {})
    if not overrides:
        st.info("未修改任何配色变量，无需保存。")
        return

    themes = list_theme_options()
    theme_names = {t["id"]: t["name"] for t in themes}
    current_id = st.session_state.get("theme_style_select", get_current_theme_id())
    current_name = theme_names.get(current_id, current_id)

    save_mode = st.radio("保存方式", ["覆盖当前主题", "另存为新主题"], key="save_mode_radio")

    if save_mode == "另存为新主题":
        new_name = st.text_input("新主题名称", value=f"{current_name} (自定义)", key="new_theme_name")
        new_id = st.text_input("主题ID (英文小写)", value=f"custom_{int(time.time())}", key="new_theme_id")
    else:
        new_name = current_name
        new_id = current_id

    if st.button("确认保存", type="primary", key="confirm_save_theme"):
        base_css_path = Path(f"app/styles/themes/{current_id}.css")
        if base_css_path.exists():
            base_vars = parse_theme_variables(base_css_path.read_text(encoding="utf-8"))
        else:
            base_vars = {}
        merged = {**base_vars, **overrides}
        lines = [
            "/*",
            f"theme-id: {new_id}",
            f"theme-name: {new_name}",
            f"theme-description: 基于 {current_name} 自定义的主题。",
            "*/",
            ":root {",
        ]
        for var, val in merged.items():
            lines.append(f"  {var}: {val};")
        lines.append("}")
        out_path = Path(f"app/styles/themes/{new_id}.css")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        save_current_theme(new_id)
        st.session_state.pop("theme_overrides", None)
        st.success(f"主题「{new_name}」已保存并应用。")
        time.sleep(0.8)
        st.rerun()


tabs = st.tabs(["AI模型", "主题风格"])


with tabs[0]:
    st.caption("用于简历结构化解析、岗位匹配等 AI 功能。支持 DeepSeek / 通义千问 / OpenAI 等兼容 OpenAI 格式的 API。")
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
        ai_api_key = st.text_input(
            "API Key", value=current_key, type="password", key="ai_api_key_input",
            help="DeepSeek / 通义千问 / OpenAI 的 API Key",
        )
    api_test_col.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    test_ai_api_clicked = api_test_col.button("API检测", key="test_ai_api_key", use_container_width=True)
    presets = {
        "DeepSeek": ("https://api.deepseek.com/v1", "deepseek-chat"),
        "通义千问": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
        "OpenAI": ("https://api.openai.com/v1", "gpt-4o-mini"),
        "自定义": (current_url, current_model),
    }
    preset_choice = st.selectbox(
        "预设模型", list(presets.keys()), key="ai_preset",
        index=0 if "deepseek" in current_url else (1 if "dashscope" in current_url else (2 if "openai" in current_url else 3)),
    )
    preset_url, preset_model = presets[preset_choice]
    ai_base_url = st.text_input("API Base URL", value=preset_url, key="ai_base_url_input")
    ai_model = st.text_input("模型名称", value=preset_model, key="ai_model_input")
    if test_ai_api_clicked:
        st.session_state["ai_api_test_config"] = {"api_key": ai_api_key, "base_url": ai_base_url, "model": ai_model}
        open_ai_api_key_test_dialog()

    if st.button("保存 AI 配置", key="save_ai_config"):
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
        st.success("AI 配置已保存，配置缓存已刷新。")


with tabs[1]:
    themes = list_theme_options()
    if not themes:
        st.warning("未检测到主题样式文件，请检查 app/styles/themes 目录。")
    else:
        current_theme_id = get_current_theme_id()
        theme_ids = [t["id"] for t in themes]
        theme_names = {t["id"]: t["name"] for t in themes}
        theme_descriptions = {t["id"]: t.get("description", "") for t in themes}

        select_col, action_col, save_col = st.columns(3)
        selected_theme_id = select_col.selectbox(
            "预设主题风格", theme_ids,
            index=theme_ids.index(current_theme_id) if current_theme_id in theme_ids else 0,
            format_func=lambda tid: theme_names.get(tid, tid),
            key="theme_style_select",
        )
        action_col.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        save_col.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if action_col.button("应用主题", type="primary", key="apply_theme_style", use_container_width=True):
            save_current_theme(selected_theme_id)
            st.success(f"已应用主题：{theme_names.get(selected_theme_id, selected_theme_id)}")
            st.rerun()
        if save_col.button("保存自定义主题", key="save_custom_theme", use_container_width=True):
            open_save_theme_dialog()

        st.caption(theme_descriptions.get(selected_theme_id) or "选择主题后可在下方预览效果，点击组件可编辑配色。")

        overrides = st.session_state.get("theme_overrides", {})
        preview_css = get_theme_css(selected_theme_id)
        preview_html = build_preview_html(
            preview_css,
            theme_names.get(selected_theme_id, selected_theme_id),
            theme_descriptions.get(selected_theme_id, "主题预览"),
            overrides=overrides or None,
        )
        components.html(preview_html, height=720)

        st.divider()
        selected_comp = st.selectbox(
            "编辑组件配色", list(COMPONENT_LABELS.keys()),
            format_func=lambda k: COMPONENT_LABELS[k],
            key="edit_component_select",
        )

        variables = COMPONENT_VARIABLE_MAP[selected_comp]
        current_vars = parse_theme_variables(preview_css)
        if "theme_overrides" not in st.session_state:
            st.session_state["theme_overrides"] = {}

        cols = st.columns(len(variables))
        for i, (var_name, label) in enumerate(variables):
            with cols[i]:
                current_val = st.session_state["theme_overrides"].get(var_name, current_vars.get(var_name, "#000000"))
                if current_val.startswith("#") or current_val.startswith("rgb"):
                    hex_val = current_val if current_val.startswith("#") else "#000000"
                    if len(hex_val) == 4:
                        hex_val = f"#{hex_val[1]*2}{hex_val[2]*2}{hex_val[3]*2}"
                    new_val = st.color_picker(label, value=hex_val, key=f"cp_{selected_comp}_{var_name}")
                    if new_val != hex_val:
                        st.session_state["theme_overrides"][var_name] = new_val
                else:
                    new_val = st.text_input(label, value=current_val, key=f"ti_{selected_comp}_{var_name}")
                    if new_val != current_val:
                        st.session_state["theme_overrides"][var_name] = new_val

        if st.session_state.get("theme_overrides"):
            st.caption(f"已修改 {len(st.session_state['theme_overrides'])} 个变量，预览已实时更新。点击「保存自定义主题」持久化。")
