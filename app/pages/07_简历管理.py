from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import delete, select

from components.layout import inject_vibe_style, page_header
from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.parsers.pdf_resume_parser import parse_resume_file
from recruitment_assistant.storage.db import create_session
from recruitment_assistant.storage.models import (
    Candidate,
    EducationExperience,
    PlatformCandidateRecord,
    ProjectExperience,
    RawResume,
    Resume,
    ResumeAttachment,
    ResumeScore,
    ResumeSkill,
    ResumeTag,
    WorkExperience,
)
from recruitment_assistant.utils.hash_utils import mask_email, mask_phone, text_hash

SUPPORTED_SUFFIXES = {".pdf", ".doc", ".docx"}
PLATFORM_OPTIONS = {"BOSS直聘": "boss", "智联招聘": "zhilian"}

st.set_page_config(page_title="简历管理", layout="wide", initial_sidebar_state="collapsed")
inject_vibe_style("简历管理")
page_header("简历管理", "按平台和日期加载已保存简历，批量解析 PDF/DOC/DOCX，规范入库并导出 Excel。")

settings = get_settings()
selected_platform_name = st.selectbox("简历来源平台", list(PLATFORM_OPTIONS.keys()), index=0, key="resume_manage_platform_name")
platform_code = PLATFORM_OPTIONS[selected_platform_name]
attachment_root = settings.attachment_dir / platform_code

if st.session_state.get("resume_manage_active_platform") != platform_code:
    st.session_state.resume_manage_active_platform = platform_code
    st.session_state.resume_manage_files = []
    st.session_state.resume_manage_rows = []
    st.session_state.resume_manage_failures = {}
    st.session_state.resume_manage_loaded_date = None

if "resume_manage_files" not in st.session_state:
    st.session_state.resume_manage_files = []
if "resume_manage_rows" not in st.session_state:
    st.session_state.resume_manage_rows = []
if "resume_manage_failures" not in st.session_state:
    st.session_state.resume_manage_failures = {}
if "resume_manage_loaded_date" not in st.session_state:
    st.session_state.resume_manage_loaded_date = None


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_text(value: object) -> str | None:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()
    return text or None


def clean_phone(value: object) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits if len(digits) >= 7 else None


def clean_email(value: object) -> str | None:
    email = clean_text(value)
    return email.lower() if email and "@" in email else None


def to_decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value)).quantize(Decimal("0.1"))
    except (InvalidOperation, ValueError):
        return None
    if number < 0 or number > Decimal("80.0"):
        return None
    return number


def json_safe(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    return value


def list_date_dirs() -> list[Path]:
    if not attachment_root.exists():
        return []
    return sorted(
        [path for path in attachment_root.iterdir() if path.is_dir() and path.name.isdigit() and len(path.name) == 8],
        key=lambda item: item.name,
        reverse=True,
    )


def date_label(path: Path) -> str:
    try:
        return datetime.strptime(path.name, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return path.name


def list_resume_files(date_dir: Path) -> list[Path]:
    if not date_dir.exists():
        return []
    return sorted(
        [path for path in date_dir.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def existing_file_statuses(files: list[Path]) -> dict[str, str]:
    hashes = [file_hash(path) for path in files if path.exists()]
    if not hashes:
        return {}
    with create_session() as session:
        rows = session.execute(
            select(ResumeAttachment.file_hash).where(
                ResumeAttachment.platform_code == platform_code,
                ResumeAttachment.file_hash.in_(hashes),
            )
        ).scalars().all()
    return {str(item): "已解析" for item in rows if item}


def build_file_rows(files: list[Path]) -> list[dict]:
    parsed_hashes = existing_file_statuses(files)
    rows = []
    for index, path in enumerate(files, start=1):
        current_hash = file_hash(path)
        failure = st.session_state.resume_manage_failures.get(str(path))
        if current_hash in parsed_hashes:
            status = "已解析"
        elif failure:
            status = "失败"
        else:
            status = "待解析"
        rows.append(
            {
                "序号": index,
                "文件名": path.name,
                "格式": path.suffix.lower(),
                "文件大小KB": round(path.stat().st_size / 1024, 2),
                "修改时间": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "解析状态": status,
                "失败原因": failure or "",
                "原文链接": path.resolve().as_uri(),
                "文件路径": str(path),
                "file_hash": current_hash,
            }
        )
    return rows


def render_overview(rows: list[dict]) -> None:
    total = len(rows)
    parsed = sum(1 for row in rows if row.get("解析状态") == "已解析")
    failed = sum(1 for row in rows if row.get("解析状态") == "失败")
    pending = total - parsed - failed
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总数", total)
    col2.metric("已解析", parsed)
    col3.metric("待解析", pending)
    col4.metric("失败数", failed)


def validate_file(path: Path) -> None:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".pdf" and not data.startswith(b"%PDF"):
        raise ValueError("文件扩展名为 PDF，但实际内容不是 PDF")
    if suffix == ".docx" and not data.startswith(b"PK\x03\x04"):
        raise ValueError("文件扩展名为 DOCX，但实际内容不是 DOCX")
    if suffix == ".doc" and not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ValueError("文件扩展名为 DOC，但实际内容不是 DOC")


def parse_with_retry(path: Path, retries: int = 1) -> tuple[dict | None, str | None]:
    last_error = None
    for attempt in range(retries + 1):
        try:
            validate_file(path)
            return parse_resume_file(path, candidate_signature=path.stem).to_dict(), None
        except Exception as exc:
            last_error = str(exc)
            if attempt >= retries:
                break
    return None, last_error or "解析失败"


def normalize_resume_data(parsed: dict, path: Path, current_hash: str) -> dict:
    name = clean_text(parsed.get("name")) or path.stem[:40]
    phone = clean_phone(parsed.get("phone"))
    email = clean_email(parsed.get("email"))
    skills = parsed.get("skills") or []
    if not isinstance(skills, list):
        skills = [clean_text(skills)] if clean_text(skills) else []
    return {
        "name": name,
        "job_title": clean_text(parsed.get("job_title")),
        "phone": phone,
        "email": email,
        "current_city": clean_text(parsed.get("current_city")),
        "highest_degree": clean_text(parsed.get("highest_degree")),
        "years_of_experience": to_decimal(parsed.get("years_of_experience")),
        "current_company": clean_text(parsed.get("current_company")),
        "current_position": clean_text(parsed.get("current_position")),
        "expected_position": clean_text(parsed.get("expected_position")),
        "skills": [item for item in [clean_text(skill) for skill in skills] if item],
        "text_preview": clean_text(parsed.get("text_preview")),
        "source_file": str(path),
        "file_name": path.name,
        "file_path": str(path),
        "file_ext": path.suffix.lower(),
        "file_size": path.stat().st_size,
        "file_hash": current_hash,
    }


def find_or_create_raw_resume(session, normalized: dict) -> RawResume:
    raw_resume = session.scalar(
        select(RawResume)
        .where(RawResume.platform_code == platform_code, RawResume.content_hash == normalized["file_hash"])
        .limit(1)
    )
    raw_json = {
        "source": "resume_management",
        "parsed_resume": json_safe(normalized),
        "attachment": {
            "file_name": normalized["file_name"],
            "file_path": normalized["file_path"],
            "file_size": normalized["file_size"],
            "file_hash": normalized["file_hash"],
        },
    }
    if raw_resume:
        raw_resume.raw_json = raw_json
        raw_resume.parsed_status = "parsed"
        raw_resume.parsed_at = datetime.now()
        return raw_resume
    raw_resume = RawResume(
        platform_code=platform_code,
        source_resume_id=normalized["file_hash"][:32],
        source_url=Path(normalized["file_path"]).resolve().as_uri(),
        raw_json=raw_json,
        raw_html_path=None,
        content_hash=normalized["file_hash"],
        parsed_status="parsed",
        parsed_at=datetime.now(),
    )
    session.add(raw_resume)
    session.flush()
    return raw_resume


def find_or_create_candidate(session, normalized: dict) -> tuple[Candidate, bool]:
    phone_hash = text_hash(normalized.get("phone"))
    email_hash = text_hash(normalized.get("email"))
    dedup_source = normalized.get("phone") or normalized.get("email") or "|".join(
        [normalized.get("name") or "", normalized.get("highest_degree") or "", normalized.get("current_company") or ""]
    )
    dedup_key = text_hash(dedup_source)
    candidate = None
    if phone_hash:
        candidate = session.scalar(select(Candidate).where(Candidate.phone_hash == phone_hash, Candidate.deleted_at.is_(None)).limit(1))
    if not candidate and email_hash:
        candidate = session.scalar(select(Candidate).where(Candidate.email_hash == email_hash, Candidate.deleted_at.is_(None)).limit(1))
    if not candidate and dedup_key:
        candidate = session.scalar(select(Candidate).where(Candidate.dedup_key == dedup_key, Candidate.deleted_at.is_(None)).limit(1))
    if candidate:
        candidate.current_city = normalized.get("current_city") or candidate.current_city
        candidate.highest_degree = normalized.get("highest_degree") or candidate.highest_degree
        candidate.years_of_experience = normalized.get("years_of_experience") or candidate.years_of_experience
        candidate.current_company = normalized.get("current_company") or candidate.current_company
        candidate.current_position = normalized.get("current_position") or candidate.current_position
        candidate.phone_plain = normalized.get("phone") or candidate.phone_plain
        candidate.phone_hash = phone_hash or candidate.phone_hash
        candidate.phone_masked = mask_phone(normalized.get("phone")) or candidate.phone_masked
        candidate.email_plain = normalized.get("email") or candidate.email_plain
        candidate.email_hash = email_hash or candidate.email_hash
        candidate.email_masked = mask_email(normalized.get("email")) or candidate.email_masked
        return candidate, False
    candidate = Candidate(
        name=normalized["name"],
        phone_plain=normalized.get("phone"),
        phone_hash=phone_hash,
        phone_masked=mask_phone(normalized.get("phone")),
        email_plain=normalized.get("email"),
        email_hash=email_hash,
        email_masked=mask_email(normalized.get("email")),
        current_city=normalized.get("current_city"),
        highest_degree=normalized.get("highest_degree"),
        years_of_experience=normalized.get("years_of_experience"),
        current_company=normalized.get("current_company"),
        current_position=normalized.get("current_position"),
        dedup_key=dedup_key,
    )
    session.add(candidate)
    session.flush()
    return candidate, True


def save_normalized_resume(normalized: dict) -> tuple[str, int, int]:
    with create_session() as session:
        existing_attachment = session.scalar(
            select(ResumeAttachment)
            .where(ResumeAttachment.platform_code == platform_code, ResumeAttachment.file_hash == normalized["file_hash"])
            .limit(1)
        )
        if existing_attachment:
            return "重复跳过", existing_attachment.resume_id, existing_attachment.raw_resume_id or 0

        raw_resume = find_or_create_raw_resume(session, normalized)
        candidate, created_candidate = find_or_create_candidate(session, normalized)
        resume = Resume(
            candidate_id=candidate.id,
            raw_resume_id=raw_resume.id,
            platform_code=platform_code,
            resume_title=normalized.get("job_title") or normalized.get("expected_position") or normalized["file_name"],
            summary=normalized.get("text_preview"),
            expected_position=normalized.get("expected_position") or normalized.get("job_title"),
            resume_status="parsed",
        )
        session.add(resume)
        session.flush()
        session.add(
            ResumeAttachment(
                resume_id=resume.id,
                raw_resume_id=raw_resume.id,
                platform_code=platform_code,
                file_name=normalized["file_name"],
                file_path=normalized["file_path"],
                file_ext=normalized["file_ext"],
                file_size=normalized["file_size"],
                file_hash=normalized["file_hash"],
            )
        )
        for skill in normalized.get("skills") or []:
            session.add(ResumeSkill(resume_id=resume.id, skill_name=skill, source="parser"))
        session.commit()
        return ("新增候选人" if created_candidate else "更新候选人"), resume.id, raw_resume.id


def process_files(rows: list[dict]) -> list[dict]:
    results = []
    progress = st.progress(0)
    status = st.empty()
    candidates = [row for row in rows if row.get("解析状态") != "已解析"]
    total = len(candidates)
    for index, row in enumerate(candidates, start=1):
        path = Path(row["文件路径"])
        status.info(f"正在解析并入库：{path.name}（{index}/{total}）")
        parsed, error = parse_with_retry(path, retries=1)
        if error or not parsed:
            st.session_state.resume_manage_failures[str(path)] = error or "解析失败"
            results.append({**row, "解析状态": "失败", "失败原因": error or "解析失败"})
        else:
            normalized = normalize_resume_data(parsed, path, row["file_hash"])
            action, resume_id, raw_resume_id = save_normalized_resume(normalized)
            st.session_state.resume_manage_failures.pop(str(path), None)
            results.append(
                {
                    **row,
                    "姓名": normalized.get("name"),
                    "岗位名称": normalized.get("job_title"),
                    "手机号": normalized.get("phone"),
                    "邮箱": normalized.get("email"),
                    "当前城市": normalized.get("current_city"),
                    "最高学历": normalized.get("highest_degree"),
                    "工作年限": str(normalized.get("years_of_experience") or ""),
                    "当前公司": normalized.get("current_company"),
                    "当前职位": normalized.get("current_position"),
                    "期望职位": normalized.get("expected_position"),
                    "技能": ", ".join(normalized.get("skills") or []),
                    "解析状态": "已解析",
                    "入库动作": action,
                    "resume_id": resume_id,
                    "raw_resume_id": raw_resume_id,
                    "失败原因": "",
                }
            )
        progress.progress(index / total if total else 1.0)
    status.empty()
    progress.empty()
    return results


def export_rows(rows: list[dict]) -> bytes:
    export_columns = [
        "序号", "解析状态", "入库动作", "姓名", "岗位名称", "手机号", "邮箱", "当前城市", "最高学历", "工作年限",
        "当前公司", "当前职位", "期望职位", "技能", "文件名", "格式", "文件大小KB", "修改时间", "原文链接", "文件路径", "失败原因",
    ]
    dataframe = pd.DataFrame(rows)
    for column in export_columns:
        if column not in dataframe.columns:
            dataframe[column] = ""
    buffer = BytesIO()
    dataframe[export_columns].to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)
    return buffer.getvalue()


def clear_current_parse_library() -> dict[str, int]:
    with create_session() as session:
        resume_rows = session.execute(
            select(Resume.id, Resume.candidate_id, Resume.raw_resume_id).where(Resume.platform_code == platform_code)
        ).all()
        resume_ids = [row.id for row in resume_rows]
        candidate_ids = [row.candidate_id for row in resume_rows if row.candidate_id]
        raw_resume_ids = [row.raw_resume_id for row in resume_rows if row.raw_resume_id]
        attachment_raw_ids = session.execute(
            select(ResumeAttachment.raw_resume_id).where(ResumeAttachment.platform_code == platform_code)
        ).scalars().all()
        raw_resume_ids.extend([item for item in attachment_raw_ids if item])

        deleted = {
            "platform_records": 0,
            "scores": 0,
            "tags": 0,
            "skills": 0,
            "projects": 0,
            "educations": 0,
            "works": 0,
            "attachments": 0,
            "resumes": 0,
            "raw_resumes": 0,
            "candidates": 0,
        }
        if resume_ids:
            deleted["scores"] = session.execute(delete(ResumeScore).where(ResumeScore.resume_id.in_(resume_ids))).rowcount or 0
            deleted["tags"] = session.execute(delete(ResumeTag).where(ResumeTag.resume_id.in_(resume_ids))).rowcount or 0
            deleted["skills"] = session.execute(delete(ResumeSkill).where(ResumeSkill.resume_id.in_(resume_ids))).rowcount or 0
            deleted["projects"] = session.execute(delete(ProjectExperience).where(ProjectExperience.resume_id.in_(resume_ids))).rowcount or 0
            deleted["educations"] = session.execute(delete(EducationExperience).where(EducationExperience.resume_id.in_(resume_ids))).rowcount or 0
            deleted["works"] = session.execute(delete(WorkExperience).where(WorkExperience.resume_id.in_(resume_ids))).rowcount or 0
            deleted["attachments"] = session.execute(delete(ResumeAttachment).where(ResumeAttachment.resume_id.in_(resume_ids))).rowcount or 0
            deleted["resumes"] = session.execute(delete(Resume).where(Resume.id.in_(resume_ids))).rowcount or 0
            session.flush()
        if raw_resume_ids:
            raw_resume_id_set = set(raw_resume_ids)
            deleted["platform_records"] = session.execute(
                delete(PlatformCandidateRecord).where(PlatformCandidateRecord.raw_resume_id.in_(raw_resume_id_set))
            ).rowcount or 0
            deleted["raw_resumes"] = session.execute(delete(RawResume).where(RawResume.id.in_(raw_resume_id_set))).rowcount or 0
        if candidate_ids:
            remaining_candidate_ids = set(session.execute(select(Resume.candidate_id).where(Resume.candidate_id.in_(candidate_ids))).scalars().all())
            orphan_candidate_ids = [item for item in set(candidate_ids) if item not in remaining_candidate_ids]
            if orphan_candidate_ids:
                deleted["candidates"] = session.execute(delete(Candidate).where(Candidate.id.in_(orphan_candidate_ids))).rowcount or 0
        session.commit()
        return deleted


def reset_resume_manage_state() -> None:
    st.session_state.resume_manage_failures = {}
    if st.session_state.resume_manage_files:
        files = [Path(path) for path in st.session_state.resume_manage_files]
        st.session_state.resume_manage_rows = build_file_rows(files)


date_dirs = list_date_dirs()
st.markdown('<div class="vibe-card"><h3>日期选择与加载</h3>', unsafe_allow_html=True)
if not date_dirs:
    st.warning(f"未找到已保存简历日期目录：{attachment_root}")
else:
    labels = [date_label(path) for path in date_dirs]
    selected_label = st.selectbox("选择简历日期", labels, index=0)
    selected_dir = date_dirs[labels.index(selected_label)]
    col_load, col_path = st.columns([1, 4])
    with col_load:
        load_clicked = st.button("加载该日简历", type="primary")
    with col_path:
        st.caption(f"读取目录：`{selected_dir}`")
    if load_clicked:
        files = list_resume_files(selected_dir)
        st.session_state.resume_manage_files = [str(path) for path in files]
        st.session_state.resume_manage_rows = build_file_rows(files)
        st.session_state.resume_manage_loaded_date = selected_label
        st.success(f"已加载 {selected_label} 的 {len(files)} 份简历。")
st.markdown('</div>', unsafe_allow_html=True)

rows = st.session_state.resume_manage_rows
if rows:
    st.markdown('<div class="vibe-card"><h3>加载概览</h3>', unsafe_allow_html=True)
    render_overview(rows)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="vibe-card"><h3>批量解析与入库</h3>', unsafe_allow_html=True)
    pending_count = sum(1 for row in rows if row.get("解析状态") != "已解析")
    col_parse, col_refresh, col_clear = st.columns([1, 1, 1])
    with col_parse:
        start_parse = st.button("开始解析并入库", type="primary", disabled=pending_count == 0)
    with col_refresh:
        refresh = st.button("刷新状态")
    with col_clear:
        clear_parse_library = st.button("调试：清除平台解析库", type="secondary")
    if clear_parse_library:
        with st.spinner("正在清除平台解析库..."):
            deleted = clear_current_parse_library()
            reset_resume_manage_state()
        st.warning(
            "已清除平台解析库："
            f"简历 {deleted['resumes']} 条，候选人 {deleted['candidates']} 条，"
            f"原始简历 {deleted['raw_resumes']} 条，附件 {deleted['attachments']} 条。"
        )
        st.rerun()
    if refresh and st.session_state.resume_manage_files:
        files = [Path(path) for path in st.session_state.resume_manage_files]
        st.session_state.resume_manage_rows = build_file_rows(files)
        st.rerun()
    if start_parse:
        with st.spinner("正在批量解析、去重、清洗并写入数据库..."):
            processed = process_files(rows)
            by_path = {row["文件路径"]: row for row in rows}
            for item in processed:
                by_path[item["文件路径"]] = item
            st.session_state.resume_manage_rows = list(by_path.values())
        st.success("批量解析并入库完成。")
        st.rerun()
    st.caption("解析失败会自动重试 1 次；重复文件按文件哈希自动跳过。")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="vibe-card"><h3>原始简历列表</h3>', unsafe_allow_html=True)
    display_rows = [{key: value for key, value in row.items() if key != "file_hash"} for row in st.session_state.resume_manage_rows]
    st.dataframe(
        display_rows,
        use_container_width=True,
        hide_index=True,
        column_config={"原文链接": st.column_config.LinkColumn("原文链接")},
    )
    excel_bytes = export_rows(st.session_state.resume_manage_rows)
    st.download_button(
        "导出 Excel",
        data=excel_bytes,
        file_name=f"简历管理_{(st.session_state.resume_manage_loaded_date or 'resume').replace('-', '')}_{datetime.now().strftime('%H%M%S')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    st.markdown('</div>', unsafe_allow_html=True)
else:
    st.info("请选择日期并点击「加载该日简历」。")
