"""扩展上报事件 → 入库适配器（M3 BOSS 持久化路径）。

`crawl_hub.on_event` 的实现：把 BOSS 扩展上报的事件字典翻译成对 `CrawlTaskService` /
`BossCandidateRecordService` 的调用。调用方（routers/crawl.py 的 WS 事件循环）已在
`tenancy.tenant_scope(org_id, user_id)` 内同步直调本函数，故写库时 `_stamp_tenant`
自动盖 tenant_id——这里**绝不**再自开 tenant_scope（会覆盖外层上下文）。

只搬「持久化路径」三类事件：
- `boss_content_script_collect_started` → 建 CrawlTask（任务生命周期起点）
- `resume_downloaded` → upsert 候选人去重记录（端到端闭环核心）
- `collect_finished` / `error` / `extension_disconnected` → 收尾 CrawlTask

其余 ~120 个进度/诊断事件（原 boss_ws_bridge 为本机 Streamlit UI 设计）在服务端无
消费方，一律 debug 丢弃。

与本机 bridge 的本质差异：扩展把简历下载到**用户本机磁盘**，服务端读不到
`data["download_path"]`，故这里**不做文件归档/哈希**，只落元数据（姓名/电话/URL/扩展
给的 hash 等）。归档若要上服务器需扩展改为上传文件字节，属后续工作。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from recruitment_assistant.services.crawl_task_service import (
    BossCandidateRecordService,
    CrawlTaskService,
)
from recruitment_assistant.storage.db import create_session
from recruitment_assistant.storage.models import CrawlTask
from recruitment_assistant.utils.hash_utils import text_hash
from recruitment_assistant.utils.snapshot_utils import safe_filename


@dataclass
class _RunState:
    task_id: int
    downloaded: int = 0
    seen_keys: set[str] = field(default_factory=set)


# ponytail: 进程内内存表（user_id → 当前采集任务状态），单 worker 足够——与 crawl_hub
# 同前提（扩展断线自动重连）。要多 worker 扇出再上 Redis/DB 会话表。
_runs: dict[int, _RunState] = {}


def _normalize_part(value: str | None, fallback: str) -> str:
    """照抄 boss_ws_bridge._normalize_resume_filename_part：去空白+去标点，空则兜底。"""
    text = "".join(str(value or "").split()).strip("-—_｜|/\\:：,，;；.。()（）[]【】")
    if not text or text == "待识别":
        text = fallback
    return safe_filename(text, max_length=24)


def _build_candidate_key(candidate_sig: str, candidate_info: dict) -> str:
    """照抄 boss_ws_bridge._build_boss_candidate_key：按 姓名|年龄|学历 归一后哈希。"""
    parts = [p.strip() for p in str(candidate_sig or "").split("/")]
    while len(parts) < 3:
        parts.append("")
    name = _normalize_part(candidate_info.get("name") or parts[0], "待识别")
    age = _normalize_part(candidate_info.get("age") or parts[1], "待识别")
    education = _normalize_part(candidate_info.get("education") or parts[2], "待识别")
    key = text_hash("|".join(["boss", "profile_name_age_education", name, age, education]))
    return key or text_hash(f"boss|candidate_signature|{candidate_sig or ''}") or ""


def _simplify_position(raw: str | None) -> str | None:
    """照抄 boss_ws_bridge._simplify_talking_position：去括号、取斜杠前段、截断 12 字。"""
    if not raw:
        return None
    s = re.sub(r"[（(][^）)]*[）)]", "", raw)
    s = re.split(r"[/／]", s, maxsplit=1)[0].strip()
    return (s[:12] or None)


def _blank_to_none(value: str | None) -> str | None:
    return value if value not in {"", "待识别", None} else None


def _handle_resume_downloaded(user_id: int, data: dict) -> None:
    # 附件作品（portfolio）不是简历本体，跳过——去重维度不同，端到端闭环不需要。
    # ponytail: 需要作品集入库再单独接，属后续。
    if (data.get("variant") or "") == "attachment_works":
        logger.debug("跳过附件作品事件（非简历本体）user={}", user_id)
        return

    candidate_sig = str(data.get("candidate_signature") or "")
    candidate_info = data.get("candidate_info") or {}
    candidate_key = _build_candidate_key(candidate_sig, candidate_info)
    if not candidate_key:
        logger.debug("resume_downloaded 无法生成 candidate_key，跳过 user={}", user_id)
        return

    name = _normalize_part(candidate_info.get("name"), "待识别")
    talking_position = _simplify_position(
        candidate_info.get("talking_position") or candidate_info.get("job_title")
    )
    run = _runs.get(user_id)
    with create_session() as session:
        is_new = BossCandidateRecordService(session).upsert_candidate_record(
            platform_code="boss",
            target_site="BOSS直聘",
            candidate_key=candidate_key,
            candidate_signature=candidate_sig or None,
            name=name if name != "待识别" else None,
            gender=_blank_to_none(candidate_info.get("gender")),
            job_title=_blank_to_none(candidate_info.get("job_title")),
            talking_position=talking_position,
            phone=_blank_to_none(candidate_info.get("phone")),
            # 服务端拿不到本机下载文件，只落扩展给的文件名/URL/hash（无则 NULL）。
            resume_file_name=data.get("filename") or None,
            source_url=str(data.get("url") or data.get("direct_url") or "") or None,
            content_hash=data.get("content_hash") or data.get("hash") or None,
            task_id=run.task_id if run else None,
        )
    if run and is_new and candidate_key not in run.seen_keys:
        run.seen_keys.add(candidate_key)
        run.downloaded += 1
    logger.info("BOSS 候选人入库 user={} sig={} 新增={}", user_id, candidate_sig or "?", is_new)


def _handle_collect_started(user_id: int, data: dict) -> None:
    query_params = data.get("config") if isinstance(data.get("config"), dict) else None
    with create_session() as session:
        task = CrawlTaskService(session).create_task(
            platform_code="boss",
            task_name=f"BOSS采集-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            task_type="chat_attachment_resume",
            query_params=query_params,
        )
        task_id = task.id
    # ponytail: 扩展硬断线（WebSocketDisconnect）时 crawl.py 只 unregister，不会补发
    # 收尾事件，故此 task 会滞留 status="running"，_runs 也留残条。这里覆盖旧条即接受
    # 该残留——启动时 reap_stale_running_tasks 会把孤儿 running 收尾为 cancelled，够用。
    _runs[user_id] = _RunState(task_id=task_id)
    logger.info("BOSS 采集任务已创建 user={} task=#{}", user_id, task_id)


def _handle_collect_finished(user_id: int, status: str, data: dict) -> None:
    run = _runs.pop(user_id, None)
    if run is None:
        return
    error_message = str(data.get("message") or "") or None if status == "failed" else None
    with create_session() as session:
        task = session.get(CrawlTask, run.task_id)
        if task is not None:
            CrawlTaskService(session).finish_task(
                task,
                status=status,
                success_count=run.downloaded,
                error_message=error_message,
            )
    logger.info("BOSS 采集任务收尾 user={} task=#{} 状态={} 下载={}",
                user_id, run.task_id, status, run.downloaded)


def handle_boss_event(user_id: int, org_id: int | None, event: dict) -> None:
    """crawl_hub.on_event 实现。已在调用方 tenant_scope 内，写库自动盖 tenant_id。"""
    # receive_json 可能是数组/字符串/null 等非字典帧——挡在最前，否则 .get 抛错会逃出
    # 下面的 try 冒到 WS 循环（只 catch WebSocketDisconnect），一个坏帧就掀掉整条连接。
    if not isinstance(event, dict):
        logger.warning("非字典事件已丢弃 user={} type={}", user_id, type(event).__name__)
        return
    event_type = event.get("type", "")
    data = event.get("data", {}) or {}
    try:
        match event_type:
            case "boss_content_script_collect_started":
                _handle_collect_started(user_id, data)
            case "resume_downloaded":
                _handle_resume_downloaded(user_id, data)
            case "collect_finished":
                # 用户手动停止→cancelled；否则视为成功（是否达标属 UI 语义，服务端不判）。
                _handle_collect_finished(
                    user_id, "cancelled" if data.get("stopped") else "success", data
                )
            case "error":
                _handle_collect_finished(user_id, "failed", data)
            case "extension_disconnected":
                _handle_collect_finished(user_id, "failed", {"message": "扩展连接已断开"})
            case _:
                logger.debug("未持久化的扩展事件 user={} type={}", user_id, event_type)
    except Exception as exc:  # 单条事件入库失败不应打断整条 WS 事件循环
        logger.warning("处理扩展事件失败 user={} type={}: {}", user_id, event_type, exc)
