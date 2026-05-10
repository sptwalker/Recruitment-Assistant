from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.parsers.pdf_resume_parser import parse_resume_file
from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter
from recruitment_assistant.schemas.raw_resume import RawResumeCreate
from recruitment_assistant.services.raw_resume_service import RawResumeService
from recruitment_assistant.storage.db import create_session

st.set_page_config(page_title="简历智采", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("简历管理")
page_header("简历智采", "填写下载数量后开始下载；下载记录同步显示，完成后解析 PDF/DOC/DOCX 并导出 Excel。")

settings = get_settings()

if "download_records" not in st.session_state:
    st.session_state.download_records = []
if "parsed_records" not in st.session_state:
    st.session_state.parsed_records = []

st.markdown('<div class="vibe-card"><h3>采集参数</h3>', unsafe_allow_html=True)
account_name = st.text_input("账号标识", value="default")
col1, col2, col3 = st.columns(3)
with col1:
    max_resumes = st.number_input("要下载的简历数量", min_value=1, max_value=50, value=5, step=1)
with col2:
    wait_seconds = st.number_input("最长运行秒数", min_value=60, max_value=3600, value=900, step=60)
with col3:
    per_candidate_wait = st.number_input("每个候选人等待秒数", min_value=10, max_value=300, value=60, step=10)

target_url = st.text_input("起始 URL（可选）", value="")
st.markdown('</div>', unsafe_allow_html=True)

status_box = st.empty()
download_table_box = st.empty()
parsed_table_box = st.empty()


def build_download_record(row: dict) -> dict:
    raw_json = row.get("raw_json", {})
    attachment = raw_json.get("attachment", {})
    file_path = attachment.get("file_path") or ""
    return {
        "候选人": raw_json.get("candidate_signature") or "待识别",
        "简历文件名": attachment.get("file_name") or Path(file_path).name,
        "文件路径": file_path,
        "文件大小": attachment.get("file_size"),
        "下载时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def render_download_table() -> None:
    download_table_box.markdown('<div class="vibe-card"><h3>下载记录</h3>', unsafe_allow_html=True)
    download_table_box.dataframe(st.session_state.download_records, use_container_width=True)


def render_parsed_table() -> None:
    parsed_table_box.dataframe(st.session_state.parsed_records, use_container_width=True)


def save_raw_resume_rows(rows: list[dict]) -> None:
    if not rows:
        return
    with create_session() as session:
        service = RawResumeService(session)
        for row in rows:
            service.create_raw_resume(RawResumeCreate(**row))


def parse_download_records() -> list[dict]:
    parsed_records = []
    supported_suffixes = {".pdf", ".doc", ".docx"}
    for record in st.session_state.download_records:
        file_path = Path(record.get("文件路径") or "")
        if not file_path.exists():
            parsed_records.append({**record, "解析状态": "文件不存在"})
            continue
        suffix = file_path.suffix.lower()
        if suffix not in supported_suffixes:
            parsed_records.append({**record, "解析状态": f"暂不支持解析 {suffix} 文件"})
            continue
        data = file_path.read_bytes()
        if suffix == ".pdf":
            if not data.startswith(b"%PDF"):
                parsed_records.append({**record, "解析状态": "文件扩展名为 PDF，但实际内容不是 PDF"})
                continue
            if b"%%EOF" not in data[-2048:]:
                parsed_records.append({**record, "解析状态": "PDF 文件不完整，缺少 EOF 标记"})
                continue
        elif suffix == ".docx" and not data.startswith(b"PK\x03\x04"):
            parsed_records.append({**record, "解析状态": "文件扩展名为 DOCX，但实际内容不是 DOCX"})
            continue
        elif suffix == ".doc" and not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            parsed_records.append({**record, "解析状态": "文件扩展名为 DOC，但实际内容不是 DOC"})
            continue
        try:
            parsed = parse_resume_file(file_path, candidate_signature=record.get("候选人")).to_dict()
        except Exception as exc:
            parsed_records.append({**record, "解析状态": f"解析失败：{exc}"})
            continue
        parsed_records.append(
            {
                "候选人": parsed.get("name") or record.get("候选人"),
                "岗位名称": parsed.get("job_title"),
                "手机号": parsed.get("phone"),
                "邮箱": parsed.get("email"),
                "当前城市": parsed.get("current_city"),
                "最高学历": parsed.get("highest_degree"),
                "工作年限": parsed.get("years_of_experience"),
                "当前公司": parsed.get("current_company"),
                "当前职位": parsed.get("current_position"),
                "期望职位": parsed.get("expected_position"),
                "技能": ", ".join(parsed.get("skills") or []),
                "简历文件名": record.get("简历文件名"),
                "文件路径": record.get("文件路径"),
                "解析状态": "成功",
            }
        )
    return parsed_records


render_download_table()
render_parsed_table()

col_start, col_clear = st.columns([1, 1])
with col_start:
    start_download = st.button("开始下载", type="primary")
with col_clear:
    if st.button("清空当前表格"):
        st.session_state.download_records = []
        st.session_state.parsed_records = []
        render_download_table()
        render_parsed_table()
        st.success("已清空当前页面表格。")

if start_download:
    adapter = ZhilianAdapter(account_name=account_name or "default")
    if not adapter.state_path.exists():
        st.error("登录态不存在，请先进入 `平台登录` 完成登录。")
    else:
        st.session_state.download_records = []
        st.session_state.parsed_records = []
        render_download_table()
        render_parsed_table()

        saved_rows = []

        def on_resume_saved(row: dict) -> None:
            saved_rows.append(row)
            st.session_state.download_records.append(build_download_record(row))
            status_box.info(f"已下载 {len(st.session_state.download_records)} / {int(max_resumes)} 份简历")
            render_download_table()

        with st.spinner("正在打开智联并自动下载附件简历，请勿关闭浏览器窗口..."):
            rows = adapter.auto_click_chat_attachment_resumes(
                target_url=target_url.strip() or None,
                max_resumes=int(max_resumes),
                wait_seconds=int(wait_seconds),
                per_candidate_wait_seconds=int(per_candidate_wait),
                on_resume_saved=on_resume_saved,
            )
            if not saved_rows:
                for row in rows:
                    st.session_state.download_records.append(build_download_record(row))
                render_download_table()
            save_raw_resume_rows(rows)
        status_box.success(f"下载完成，共保存 {len(st.session_state.download_records)} 份简历。")

st.divider()

if st.button("解析简历", type="primary", disabled=not st.session_state.download_records):
    with st.spinner("正在解析已下载简历文件..."):
        st.session_state.parsed_records = parse_download_records()
    render_parsed_table()
    st.success("解析完成。")

if st.session_state.parsed_records:
    export_df = pd.DataFrame(st.session_state.parsed_records)
    output_path = settings.export_dir / f"智联简历解析结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    if st.button("生成 Excel 文件"):
        settings.export_dir.mkdir(parents=True, exist_ok=True)
        export_df.to_excel(output_path, index=False)
        st.success(f"Excel 已生成：{output_path}")

    excel_buffer = BytesIO()
    export_df.to_excel(excel_buffer, index=False, engine="openpyxl")
    excel_buffer.seek(0)
    st.download_button(
        "下载 Excel",
        data=excel_buffer.getvalue(),
        file_name=f"智联简历解析结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
