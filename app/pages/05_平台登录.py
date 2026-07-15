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
from recruitment_assistant.config.ai_model_manager import (
    PRESETS as AI_PRESETS,
    load_profiles,
    add_profile,
    update_profile,
    delete_profile,
    set_active_profile,
)


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

    data = load_profiles()
    profiles = data.get("profiles", [])
    active_id = data.get("active", "")

    # ---- 当前使用模型 切换 ----
    if profiles:
        profile_names = [p["name"] for p in profiles]
        active_idx = next((i for i, p in enumerate(profiles) if p["id"] == active_id), 0)
        chosen_idx = st.selectbox(
            "当前使用模型",
            range(len(profiles)),
            index=active_idx,
            format_func=lambda i: f"✅ {profiles[i]['name']}（{profiles[i]['model']}）" if profiles[i]["id"] == active_id else f"{profiles[i]['name']}（{profiles[i]['model']}）",
            key="ai_active_select",
        )
        if profiles[chosen_idx]["id"] != active_id:
            set_active_profile(profiles[chosen_idx]["id"])
            st.toast(f"已切换到 **{profiles[chosen_idx]['name']}**", icon="✅")
            st.rerun()

    st.divider()

    # ---- 模型列表 ----
    st.markdown("**已配置模型**")
    if not profiles:
        st.info("暂无模型配置，请在下方添加。")

    for idx, prof in enumerate(profiles):
        is_active = prof["id"] == active_id
        label = f"{'✅ ' if is_active else ''}{prof['name']}（{prof['model']}）"
        with st.expander(label, expanded=False):
            p_name = st.text_input("名称", value=prof["name"], key=f"pname_{prof['id']}")
            key_col, test_col = st.columns([0.78, 0.22])
            with key_col:
                p_key = st.text_input("API Key", value=prof["api_key"], type="password", key=f"pkey_{prof['id']}")
            test_col.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            test_clicked = test_col.button("API检测", key=f"ptest_{prof['id']}", use_container_width=True)
            p_url = st.text_input("API Base URL", value=prof["base_url"], key=f"purl_{prof['id']}")
            p_model = st.text_input("模型名称", value=prof["model"], key=f"pmodel_{prof['id']}")

            if test_clicked:
                st.session_state["ai_api_test_config"] = {"api_key": p_key, "base_url": p_url, "model": p_model}
                open_ai_api_key_test_dialog()

            btn_cols = st.columns(3)
            if btn_cols[0].button("💾 保存修改", key=f"psave_{prof['id']}", use_container_width=True):
                update_profile(prof["id"], name=p_name, api_key=p_key, base_url=p_url, model=p_model)
                st.toast(f"**{p_name}** 配置已保存", icon="✅")
                st.rerun()
            if not is_active:
                if btn_cols[1].button("✅ 设为当前", key=f"pactivate_{prof['id']}", use_container_width=True):
                    set_active_profile(prof["id"])
                    st.session_state["ai_active_select"] = idx
                    st.toast(f"已切换到 **{prof['name']}**", icon="✅")
                    st.rerun()
            else:
                btn_cols[1].button("当前使用中", key=f"pactivate_{prof['id']}", use_container_width=True, disabled=True)
            if btn_cols[2].button("🗑️ 删除", key=f"pdel_{prof['id']}", use_container_width=True):
                if is_active and len(profiles) <= 1:
                    st.warning("至少保留一个模型配置")
                else:
                    delete_profile(prof["id"])
                    st.toast(f"已删除 **{prof['name']}**")
                    st.rerun()

    st.divider()

    # ---- 添加新模型 ----
    with st.expander("➕ 添加新模型", expanded=not profiles):
        preset_options = list(AI_PRESETS.keys()) + ["自定义"]
        new_preset = st.selectbox("预设模型", preset_options, key="new_ai_preset")

        if new_preset != st.session_state.get("_prev_ai_preset"):
            st.session_state["_prev_ai_preset"] = new_preset
            if new_preset in AI_PRESETS:
                st.session_state["new_prof_name"] = new_preset
                st.session_state["new_prof_url"] = AI_PRESETS[new_preset][0]
                st.session_state["new_prof_model"] = AI_PRESETS[new_preset][1]
            else:
                st.session_state["new_prof_name"] = ""
                st.session_state["new_prof_url"] = ""
                st.session_state["new_prof_model"] = ""
            st.rerun()

        if new_preset in AI_PRESETS:
            default_url, default_model = AI_PRESETS[new_preset]
            default_name = new_preset
        else:
            default_url, default_model = "", ""
            default_name = ""
        new_name = st.text_input("模型名称（自定义标签）", value=default_name, key="new_prof_name")
        new_key = st.text_input("API Key", type="password", key="new_prof_key")
        new_url = st.text_input("API Base URL", value=default_url, key="new_prof_url")
        new_model = st.text_input("模型名称（model）", value=default_model, key="new_prof_model")

        if st.button("➕ 添加模型", type="primary", key="add_new_profile"):
            if not new_name.strip():
                st.warning("请填写模型名称")
            elif not new_key.strip():
                st.warning("请填写 API Key")
            elif not new_url.strip():
                st.warning("请填写 API Base URL")
            elif not new_model.strip():
                st.warning("请填写模型名称")
            else:
                prof = add_profile(new_name.strip(), new_key.strip(), new_url.strip(), new_model.strip())
                if len(load_profiles()["profiles"]) == 1:
                    set_active_profile(prof["id"])
                st.toast(f"已添加 **{new_name}**", icon="✅")
                st.rerun()


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
