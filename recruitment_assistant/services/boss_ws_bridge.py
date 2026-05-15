"""Bridge between WebSocket events and Boss business logic."""

import json
import shutil
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from loguru import logger

from recruitment_assistant.services.crawl_task_service import BossCandidateRecordService
from recruitment_assistant.services.ws_server import BossWSServer
from recruitment_assistant.storage.db import create_session
from recruitment_assistant.utils.hash_utils import text_hash
from recruitment_assistant.utils.snapshot_utils import safe_filename



class BossWSBridge:
    def __init__(self, ws_server: BossWSServer):
        self.ws_server = ws_server
        self.ws_server.on_event = self._handle_event
        self._event_seq = 0
        self._seen_candidate_records: set[str] = set()
        self._recent_ui_log_keys: dict[str, float] = {}

        self.runtime_state: dict[str, Any] = {
            "running": False,
            "paused": False,
            "extension_connected": False,
            "page_ready": False,
            "logs": [],
            "candidates": [],
            "downloaded_count": 0,
            "skipped_count": 0,
            "dedup_record_count": 0,
            "current_index": 0,
            "run_id": "",
            "run_started_at": "",
            "log_file": "",
            "last_event_at": "",
            "last_heartbeat_at": "",
            "extension_version": "",
            "page_url": "",
            "skip_reason_counts": {},
        }
        self.reset_run()

    @property
    def is_ready(self) -> bool:
        return self.ws_server.is_extension_connected and self.runtime_state.get("page_ready", False)

    def reset_run(self) -> None:
        now = datetime.now()
        run_id = now.strftime("%Y%m%d_%H%M%S")
        log_dir = Path("logs") / "boss_extension" / now.strftime("%Y%m%d")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"run_{run_id}.jsonl"

        self._event_seq = 0
        self._seen_candidate_records.clear()
        self._recent_ui_log_keys.clear()
        self.runtime_state.update({
            "running": False,
            "paused": False,
            "logs": [],
            "candidates": [],
            "downloaded_count": 0,
            "skipped_count": 0,
            "dedup_record_count": 0,
            "current_index": 0,
            "run_id": run_id,
            "run_started_at": now.isoformat(timespec="seconds"),
            "log_file": str(log_file),
            "skip_reason_counts": {},
        })
        self._seen_candidate_records.update(self._load_boss_candidate_keys())
        self._write_event_log("run_started", {"run_id": run_id})
        self._log("info", f"新测试轮次已创建: {run_id}")
        if self.ws_server.is_extension_connected:
            command = {"type": "reset_content_script", "run_id": self.runtime_state.get("run_id", "")}
            self.ws_server.send_command(command)
            self._write_event_log("command_sent", command)
            self._log("info", "已请求扩展重新加载 Boss 页面脚本")
            self.probe_page()

    def clear_boss_dedup_records(self) -> int:
        with create_session() as session:
            deleted_count = BossCandidateRecordService(session).clear_records("boss")
        self._seen_candidate_records.clear()
        self.runtime_state["dedup_record_count"] = 0
        self._log("info", f"已清除 BOSS 去重数据库: {deleted_count} 条")
        self._write_event_log("boss_dedup_records_cleared", {"deleted_count": deleted_count})
        return deleted_count

    def start_collect(self, config: dict) -> None:
        if not self.ws_server.is_extension_connected:
            self._log("error", "扩展未连接，无法开始采集")
            self._write_event_log("command_rejected", {"command": "start_collect", "reason": "extension_not_connected"})
            return
        self.runtime_state["running"] = True
        self.runtime_state["paused"] = False
        self.runtime_state["downloaded_count"] = 0
        self.runtime_state["skipped_count"] = 0
        self.runtime_state["dedup_record_count"] = 0
        self.runtime_state["current_index"] = 0
        self.runtime_state["candidates"] = []
        self.runtime_state["skip_reason_counts"] = {}
        command = {"type": "start_collect", "config": config, "run_id": self.runtime_state.get("run_id", "")}
        self.ws_server.send_command(command)
        self._write_event_log("command_sent", command)
        self._log("info", f"采集指令已下发，最大数量={config.get('max_resumes', 5)}")

    def pause_collect(self) -> None:
        self.runtime_state["paused"] = True
        command = {"type": "pause_collect", "run_id": self.runtime_state.get("run_id", "")}
        self.ws_server.send_command(command)
        self._write_event_log("command_sent", command)
        self._log("info", "暂停指令已下发")

    def resume_collect(self) -> None:
        self.runtime_state["paused"] = False
        command = {"type": "resume_collect", "run_id": self.runtime_state.get("run_id", "")}
        self.ws_server.send_command(command)
        self._write_event_log("command_sent", command)
        self._log("info", "继续指令已下发")

    def stop_collect(self) -> None:
        self.runtime_state["running"] = False
        self.runtime_state["paused"] = False
        command = {"type": "stop_collect", "run_id": self.runtime_state.get("run_id", "")}
        self.ws_server.send_command(command)
        self._write_event_log("command_sent", command)
        self._log("info", "停止指令已下发")

    def probe_page(self) -> None:
        command = {"type": "probe_page", "run_id": self.runtime_state.get("run_id", "")}
        self.ws_server.send_command(command)
        self._write_event_log("command_sent", command)
        self._log("info", "已请求扩展重新检测 Boss 页面")

    def get_run_summary(self) -> dict[str, Any]:
        candidates = self.runtime_state.get("candidates", [])
        statuses = Counter(c.get("status", "unknown") for c in candidates)
        summary = {
            "run_id": self.runtime_state.get("run_id", ""),
            "run_started_at": self.runtime_state.get("run_started_at", ""),
            "log_file": self.runtime_state.get("log_file", ""),
            "downloaded_count": self.runtime_state.get("downloaded_count", 0),
            "skipped_count": self.runtime_state.get("skipped_count", 0),
            "dedup_record_count": self.runtime_state.get("dedup_record_count", 0),
            "current_index": self.runtime_state.get("current_index", 0),
            "status_counts": dict(statuses),
            "skip_reason_counts": self.runtime_state.get("skip_reason_counts", {}),
            "last_event_at": self.runtime_state.get("last_event_at", ""),
        }
        self._write_event_log("run_summary", summary)
        return summary

    def _handle_event(self, event: dict) -> None:
        event_type = event.get("type", "")
        data = event.get("data", {})
        noisy_events = {
            "resume_attachment_debug",
            "resume_preview_diagnostics",
            "pdf_iframe_preview_scan_started",
            "resume_preview_recognition_started",
            "resume_preview_wait_result",
            "resume_preview_weak_candidate_used",
            "resume_preview_info_extract_start",
            "download_button_candidates",
            "candidate_list_scroll_reset",
            "download_intent_registered",
            "boss_tabs_scanned",
            "candidate_list_scanned",
            "page_ready",
            "resume_attachment_clicked",
            "resume_preview_detected",
            "resume_preview_info_extract_success",
        }
        if event_type not in noisy_events:
            self._write_event_log("extension_event", {"type": event_type, "data": data})

        match event_type:
            case "extension_connected":
                self.runtime_state["extension_connected"] = True
                self.runtime_state["extension_version"] = data.get("version", "")
                self._log("info", f"扩展已连接 v{data.get('version', '?')}")
            case "heartbeat":
                self.runtime_state["extension_connected"] = True
                self.runtime_state["last_heartbeat_at"] = datetime.now().isoformat(timespec="seconds")
                if data.get("version"):
                    self.runtime_state["extension_version"] = data.get("version", "")
            case "extension_disconnected":
                self.runtime_state["extension_connected"] = False
                self.runtime_state["page_ready"] = False
                self.runtime_state["running"] = False
                self.runtime_state["paused"] = False
                self._log("error", f"扩展连接已断开: {data.get('reason', 'unknown')}")

            case "page_ready":
                self.runtime_state["page_ready"] = True
                self.runtime_state["page_url"] = data.get("url", "")
            case "page_detected":
                self.runtime_state["page_ready"] = False
                self.runtime_state["page_url"] = data.get("url", "")
            case "boss_tabs_scanned":
                pass
            case "content_script_inject_failed":
                self._log("error", f"注入 Boss 页面脚本失败: {data.get('url', '')}")
            case "content_script_message_failed":
                self._log("error", f"Boss 页面脚本通信失败: {data.get('error', '')} {data.get('url', '')}")
            case "candidate_clicked":
                sig = f"{data.get('name', '?')}/{data.get('age', '?')}/{data.get('education', '?')}"
                source = data.get("contact_source", "未知")
                note = data.get("contact_source_note", "")
                rect = data.get("contact_source_rect") or {}
                path = str(data.get("contact_source_path", ""))[:120]
                sample = str(data.get("contact_source_text_sample", ""))[:120]
                position = f"rect=({rect.get('left')},{rect.get('top')},{rect.get('width')}x{rect.get('height')})" if rect else "rect=无"
                self._log("info", f"点击候选人: {sig} (#{data.get('index', 0)})；姓名识别位置={source}；{note}；{position}；path={path}；文本={sample}")
            case "resume_downloaded":
                self._save_resume(data)
            case "candidate_skipped":
                self._record_skip(data)
            case "candidate_list_scanned":
                pass
            case "resume_button_found":
                pass
            case "resume_attachment_debug":
                pass
            case "download_intent_registered":
                pass
            case "direct_download_request_received":
                sig = data.get("candidate_signature", "未知")
                url = str(data.get("direct_url", "") or data.get("url", ""))[:180]
                self._log("info", f"Chrome 后台收到直接下载请求: {sig}；URL有效={data.get('url_valid', False)}；url={url}")
            case "direct_download_starting":
                sig = data.get("candidate_signature", "未知")
                url = str(data.get("direct_url", "") or data.get("url", ""))[:180]
                self._log("info", f"Chrome 后台准备启动直接下载: {sig}；url={url}")
            case "download_created":
                sig = data.get("candidate_signature", "未知")
                source = "PDF iframe直接下载" if data.get("direct_url") else "Chrome点击下载"
                filename = str(data.get("filename", ""))[:180]
                self._log("info", f"Chrome 下载已创建: {sig} #{data.get('download_id', '')}；来源={source}；文件/链接={filename}")
            case "candidate_list_scroll_reset":
                pass
            case "resume_request_confirm_clicked":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"已点击索要简历确认: {sig}")
            case "resume_attachment_clicked":
                pass
            case "unknown_resume_preview_probe_started":
                sig = data.get("candidate_signature", "未知")
                confirmed = data.get("confirmed", False)
                self._log("highlight", f"附件简历状态不明确，未检测到索要成功，继续尝试识别弹出页面: {sig}；确认弹窗={confirmed}")
            case "resume_request_success":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"成功索要简历: {sig}")
            case "resume_request_confirm_not_found":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"未找到索要简历确认按钮: {sig}")
            case "resume_preview_not_found":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"未识别到简历弹出页面: {sig}")
            case "resume_preview_diagnostics":
                pass
            case "boss_ui_stage":
                message = str(data.get("message", ""))
                if message:
                    self._log("highlight", message)
            case "resume_preview_recognition_started":
                pass
            case "resume_preview_wait_result":
                pass
            case "resume_preview_weak_candidate_used":
                sig = data.get("candidate_signature", "未知")
                component_type = str(data.get("component_preview_type", "") or "dom_text")
                component = str(data.get("component_class", "") or data.get("component_tag", "未知组件"))[:120]
                rect = data.get("component_rect") or {}
                self._log("highlight", f"未发现 PDF iframe，已改用最大疑似弹窗继续下载识别: {sig}；类型={component_type}；组件={component}；rect=({rect.get('left')},{rect.get('top')},{rect.get('width')}x{rect.get('height')})")
            case "resume_preview_info_extract_start":
                pass
            case "resume_preview_detected":
                pass
            case "resume_preview_info_extract_success":
                sig = data.get("candidate_signature", "未知")
                source = data.get("preview_source", "")
                name = data.get("name", "未识别")
                self._log("highlight", f"已识别简历预览页: {sig}；来源={source}；姓名={name}")
            case "pdf_iframe_preview_scan_started":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"正在扫描 PDF iframe 预览页: {sig}；可见frame={data.get('total_frames', 0)}；强匹配={data.get('strong_candidates', 0)}；本候选人仅记录首次扫描")
            case "pdf_iframe_preview_detected":
                sig = data.get("candidate_signature", "未知")
                src = str(data.get("iframe_src", "") or data.get("component_src", ""))[:180]
                self._log("highlight", f"命中 PDF iframe 简历预览页: {sig}；src={src}")
            case "resume_download_strategy_start":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"开始尝试捕获简历下载链接: {sig}；PDF iframe={data.get('pdf_iframe', False)}")
            case "direct_iframe_download_resolved":
                sig = data.get("candidate_signature", "未知")
                raw = str(data.get("raw_src", ""))[:120]
                extracted = str(data.get("extracted_src", ""))[:160]
                url = str(data.get("download_url", ""))[:180]
                extra = f"；提取资源={extracted}" if extracted else ""
                self._log("highlight", f"已解析 PDF iframe 下载地址: {sig}；原始={raw}{extra}；最终={url}")
            case "direct_iframe_download_start":
                sig = data.get("candidate_signature", "未知")
                url = str(data.get("url", ""))[:180]
                self._log("highlight", f"尝试使用 PDF iframe 地址直接下载: {sig}；url={url}")
            case "direct_download_message_send":
                sig = data.get("candidate_signature", "未知")
                url = str(data.get("url", ""))[:180]
                self._log("info", f"已向 Chrome 后台发送直接下载请求: {sig}；超时={data.get('timeout_ms', '')}ms；url={url}")
            case "direct_download_message_response":
                sig = data.get("candidate_signature", "未知")
                ok = data.get("ok", False)
                reason = data.get("reason", "")
                download_id = data.get("download_id", "")
                self._log("highlight" if ok else "error", f"Chrome 后台直接下载响应: {sig}；ok={ok}；download_id={download_id}；原因={reason}")
            case "direct_download_message_timeout":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"等待 Chrome 后台直接下载响应超时: {sig}；超时={data.get('timeout_ms', '')}ms")
            case "direct_iframe_download_skipped":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"跳过 iframe 直接下载: {sig}；原因={data.get('reason', '')}")
            case "direct_iframe_download_created":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"PDF iframe 直接下载已创建: {sig} #{data.get('download_id', '')}")
            case "direct_iframe_download_link_captured":
                sig = data.get("candidate_signature", "未知")
                url = str(data.get("download_url", ""))[:180]
                self._log("highlight", f"已通过 PDF iframe 捕获下载链接: {sig}；url={url}")
            case "direct_iframe_download_failed":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"PDF iframe 下载链路失败: {sig}；原因={data.get('reason', '')}")
            case "direct_download_response_sent":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"Chrome 后台已返回直接下载响应: {sig}；ok={data.get('ok', False)}；原因={data.get('reason', '')}")
            case "direct_download_callback_timeout":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"Chrome downloads.download 回调超时: {sig}；原因={data.get('reason', '')}")
            case "direct_download_response_error":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"Chrome 后台发送直接下载响应失败: {sig}；原因={data.get('reason', '')}")
            case "direct_download_failed":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"Chrome 后台直接下载启动失败: {sig}；原因={data.get('reason', '')}")
            case "boss_svg_download_icon_scan_started":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"开始扫描 boss-svg svg-icon [object SVGAnimatedString] 下载组件: {sig}")
            case "boss_svg_download_icon_found":
                sig = data.get("candidate_signature", "未知")
                descriptor = str(data.get("component_descriptor", ""))[:160]
                self._log("highlight", f"命中 boss-svg 下载组件: {sig}；组件={descriptor}")
            case "boss_svg_download_icon_not_found":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"未命中 boss-svg 下载组件，继续尝试其他策略: {sig}")
            case "boss_svg_download_icon_clicked":
                sig = data.get("candidate_signature", "未知")
                self._log("highlight", f"已点击 boss-svg 下载组件，等待 Chrome 下载事件: {sig}")
            case "boss_svg_download_link_captured":
                sig = data.get("candidate_signature", "未知")
                url = str(data.get("download_url", ""))[:180]
                self._log("highlight", f"boss-svg 下载链路已捕获下载链接: {sig}；url={url}")
            case "boss_svg_download_link_capture_failed":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"boss-svg 点击后未捕获下载链接: {sig}；原因={data.get('reason', '')}")
            case "resume_preview_candidate_confirm":
                sig = data.get("candidate_signature", "未知")
                component_type = str(data.get("component_preview_type", "") or "dom_text")
                component_src = str(data.get("component_src", "") or data.get("iframe_src", ""))[:180]
                component = str(data.get("component_class", "") or data.get("component_tag", "未知组件"))[:120]
                rect = data.get("component_rect") or {}
                src_suffix = f"；src={component_src}" if component_src else ""
                self._log("highlight", f"疑似识别到弹窗: {sig}；类型={component_type}；组件={component}；rect=({rect.get('left')},{rect.get('top')},{rect.get('width')}x{rect.get('height')}){src_suffix}")
                self._log(
                    "highlight",
                    "该弹窗上有候选人姓名：{} 性别：{} 年龄：{} 籍贯：{} 电话：{} 邮箱：{}".format(
                        data.get("name", "未识别"),
                        data.get("gender", "未识别"),
                        data.get("age", "未识别"),
                        data.get("native_place", "未识别"),
                        data.get("phone", "未识别"),
                        data.get("email", "未识别"),
                    ),
                )
                self._log("highlight", "请确认是否是正确的简历弹窗。如正确，请点击停止，我们下一步再确认下载图标。")
            case "collect_paused_for_resume_preview_confirm":
                self.runtime_state["paused"] = True
                self._log("info", "采集任务已暂停，请在页面中确认识别出的简历弹窗是否正确")
            case "manual_download_recording_started":
                self._log("highlight", "正在记录你的操作……")
            case "manual_download_click_captured":
                descriptor = str(data.get("descriptor", ""))[:160]
                frame_src = str(data.get("frame_src", ""))[:180]
                if frame_src:
                    self._log("highlight", f"已捕获你在 PDF iframe 内的点击操作: {descriptor}，位置=({data.get('x')},{data.get('y')})，iframe相对位置=({data.get('frame_relative_x')},{data.get('frame_relative_y')})，src={frame_src}")
                else:
                    self._log("info", f"已捕获点击操作: {descriptor}，位置=({data.get('x')},{data.get('y')})")
            case "manual_download_learning_success":
                component = str(data.get("descriptor", "") or data.get("tag", "未知组件"))[:120]
                position = f"({data.get('x')},{data.get('y')})"
                url = data.get("download_url", "未捕获到下载链接")
                self._log("highlight", f"成功记录到你的点击操作，你点击了弹出页面上的{component}组件，位置在{position}，点击后捕获到以下下载连接：{url}，学习任务成功！")
            case "manual_download_click_timeout":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"等待人工点击下载超时: {sig}")
            case "auto_download_click_used":
                sig = data.get("candidate_signature", "未知")
                descriptor = str(data.get("descriptor", "") or data.get("tag", ""))[:120]
                path = str(data.get("path", ""))[:120]
                rect = data.get("rect") or {}
                self._log("info", f"自动点击简历下载按钮: {sig}；组件={descriptor}；path={path}；rect=({rect.get('left')},{rect.get('top')},{rect.get('width')}x{rect.get('height')})")
            case "learned_download_click_used":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"自动复用已学习下载按钮: {sig}")
            case "learned_download_click_failed":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"已学习下载控件未找到: {sig}")
            case "download_button_candidates":
                pass
            case "collect_progress":
                self.runtime_state["skipped_count"] = data.get("skipped", 0)
                self.runtime_state["current_index"] = data.get("current_index", 0)
            case "collect_finished":
                self.runtime_state["running"] = False
                self.runtime_state["paused"] = False
                total = data.get("total_downloaded", 0)
                if data.get("learning_finished"):
                    self._log("highlight", "学习任务已完成，采集任务自动结束")
                else:
                    self._log("info", f"采集完成，共下载 {total} 份简历")
                self.get_run_summary()
            case "error":
                self._log("error", f"扩展错误: {data.get('message', '未知错误')}")
            case _:
                logger.debug("未处理的扩展事件: {}", event_type)

    def _normalize_resume_filename_part(self, value: str | None, fallback: str) -> str:
        text = "".join(str(value or "").split()).strip("-—_｜|/\\:：,，;；.。()（）[]【】")
        if not text or text == "待识别":
            text = fallback
        return safe_filename(text, max_length=24)

    def _build_boss_candidate_key(self, candidate_sig: str, candidate_info: dict[str, Any]) -> str:
        raw_name = candidate_info.get("name") or ""
        raw_age = candidate_info.get("age") or ""
        raw_education = candidate_info.get("education") or ""
        signature_parts = [part.strip() for part in str(candidate_sig or "").split("/")]
        while len(signature_parts) < 3:
            signature_parts.append("")
        name = self._normalize_resume_filename_part(raw_name or signature_parts[0], "待识别")
        age = self._normalize_resume_filename_part(raw_age or signature_parts[1], "待识别")
        education = self._normalize_resume_filename_part(raw_education or signature_parts[2], "待识别")
        key = text_hash("|".join(["boss", "profile_name_age_education", name, age, education]))
        if key:
            return key
        return text_hash(f"boss|candidate_signature|{candidate_sig or ''}") or ""

    def _load_boss_candidate_keys(self) -> set[str]:
        try:
            with create_session() as session:
                return BossCandidateRecordService(session).list_candidate_keys("boss")
        except Exception as exc:
            self._log("warning", f"读取 BOSS 去重记录失败: {exc}")
            return set()

    def _upsert_boss_candidate_record(
        self,
        *,
        candidate_sig: str,
        candidate_info: dict[str, Any],
        file_name: str | None,
        source_url: str | None,
        content_hash: str | None,
        raw_resume_id: int | None = None,
        task_id: int | None = None,
    ) -> bool:
        candidate_key = self._build_boss_candidate_key(candidate_sig, candidate_info)
        if not candidate_key:
            return False

        name = self._normalize_resume_filename_part(candidate_info.get("name"), "待识别")
        gender = candidate_info.get("gender") if candidate_info.get("gender") not in {"", "待识别"} else None
        job_title = candidate_info.get("job_title") if candidate_info.get("job_title") not in {"", "待识别"} else None
        phone = candidate_info.get("phone") if candidate_info.get("phone") not in {"", "待识别"} else None

        with create_session() as session:
            service = BossCandidateRecordService(session)
            return service.upsert_candidate_record(
                platform_code="boss",
                target_site="BOSS直聘",
                candidate_key=candidate_key,
                candidate_signature=candidate_sig,
                name=name if name != "待识别" else None,
                gender=gender,
                job_title=job_title,
                phone=phone,
                resume_file_name=file_name,
                source_url=source_url,
                content_hash=content_hash,
                raw_resume_id=raw_resume_id,
                task_id=task_id,
            )

    def _save_resume(self, data: dict) -> None:
        candidate_sig = data.get("candidate_signature", "未知")
        candidate_info = data.get("candidate_info", {})
        source_filename = data.get("filename", "")
        download_path = data.get("download_path", "")
        source_url = str(data.get("url", "") or data.get("direct_url", "") or "") or None
        candidate_key = self._build_boss_candidate_key(candidate_sig, candidate_info)

        if candidate_key and candidate_key in self._seen_candidate_records:
            self._log("info", f"BOSS 去重命中，已存在记录: {candidate_sig}")
            self._write_event_log("resume_saved_duplicate_skipped", {
                "signature": candidate_sig,
                "candidate_key": candidate_key,
                "info": candidate_info,
                "status": "duplicate_skipped",
                "at": datetime.now().isoformat(),
            })
            return

        if download_path:
            source = Path(download_path)
            if source.exists():
                now = datetime.now()
                project_root = Path(__file__).resolve().parents[2]
                target_dir = project_root / "data" / "attachments" / "boss" / now.strftime("%Y%m%d")
                target_dir.mkdir(parents=True, exist_ok=True)

                content = source.read_bytes()
                file_hash = sha256(content).hexdigest()
                suffix = source.suffix.lower() or ".pdf"

                name = self._normalize_resume_filename_part(candidate_info.get("name"), "未知姓名")
                age = self._normalize_resume_filename_part(candidate_info.get("age"), "未知年龄")
                education = self._normalize_resume_filename_part(candidate_info.get("education"), "未知学历")
                seq = self.runtime_state["downloaded_count"] + 1
                filename_stem = f"{name}-{age}-{education}-BOSS直聘-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}-{seq:03d}"
                filename = f"{filename_stem}{suffix}"

                target = target_dir / filename
                duplicate_index = 1
                while target.exists() and target != source:
                    target = target_dir / f"{filename_stem}-{duplicate_index}{suffix}"
                    duplicate_index += 1
                if source.resolve() != target.resolve():
                    shutil.copy2(str(source), str(target))
                    try:
                        source.unlink()
                    except OSError as exc:
                        self._log("warning", f"简历归档后删除源文件失败: {source}；原因={exc}")
                is_new_record = self._upsert_boss_candidate_record(
                    candidate_sig=candidate_sig,
                    candidate_info=candidate_info,
                    file_name=target.name,
                    source_url=source_url,
                    content_hash=file_hash,
                )
                self._seen_candidate_records.add(candidate_key)
                if is_new_record:
                    self.runtime_state["dedup_record_count"] += 1
                    self._log("info", f"BOSS 去重记录已写入: {candidate_sig}")
                    self._write_event_log("resume_saved_dedup_record_created", {
                        "signature": candidate_sig,
                        "candidate_key": candidate_key,
                        "file": target.name,
                        "path": str(target),
                        "hash": file_hash,
                        "status": "created",
                        "at": now.isoformat(),
                    })
                else:
                    self._log("info", f"BOSS 去重记录已存在，未新增: {candidate_sig}")
                    self._write_event_log("resume_saved_duplicate_record", {
                        "signature": candidate_sig,
                        "candidate_key": candidate_key,
                        "file": target.name,
                        "path": str(target),
                        "hash": file_hash,
                        "status": "duplicate_record",
                        "at": now.isoformat(),
                    })

                final_filename = target.name
                self._log("info", f"简历已保存: {final_filename}")
                self.runtime_state["downloaded_count"] = seq
                record = {
                    "signature": candidate_sig,
                    "info": candidate_info,
                    "file": final_filename,
                    "path": str(target),
                    "hash": file_hash,
                    "status": "downloaded",
                    "candidate_key": candidate_key,
                    "at": now.isoformat(),
                }
                self.runtime_state["candidates"].append(record)
                self._write_event_log("resume_saved", record)
                return

        if candidate_key and candidate_key in self._seen_candidate_records:
            return

        is_new = self._upsert_boss_candidate_record(
            candidate_sig=candidate_sig,
            candidate_info=candidate_info,
            file_name=source_filename or None,
            source_url=source_url,
            content_hash=data.get("hash") or None,
        )
        if candidate_key:
            self._seen_candidate_records.add(candidate_key)
        if is_new:
            self.runtime_state["dedup_record_count"] += 1
        else:
            self._log("info", f"BOSS 去重记录已存在，未新增: {candidate_sig}")

        self.runtime_state["downloaded_count"] += 1
        record = {
            "signature": candidate_sig,
            "info": candidate_info,
            "file": source_filename,
            "download_id": data.get("download_id", ""),
            "url": data.get("url", ""),
            "status": "downloaded_external",
            "candidate_key": candidate_key,
            "at": datetime.now().isoformat(),
        }
        self.runtime_state["candidates"].append(record)
        self._write_event_log("resume_downloaded_external", record)
        self._log("info", f"简历已下载: {candidate_sig} -> {source_filename}")


    def _translate_skip_reason(self, reason: str) -> str:
        if reason.startswith("button_disabled:"):
            state = reason.split(":", 1)[1] or "未知状态"
            return f"附件简历按钮不可用（{state}）"
        if reason.startswith("download_error:"):
            error = reason.split(":", 1)[1] or "未知错误"
            return f"下载失败（{error}）"
        mapping = {
            "need_request_resume": "需要索要简历，已按设置跳过",
            "resume_requested": "已成功索要简历，等待候选人上传",
            "resume_request_clicked": "已点击索要简历，等待确认结果",
            "resume_request_already_sent": "简历请求已发送，等待候选人上传",
            "resume_already_requested": "此前已索要简历，等待候选人上传",
            "resume_attachment_click_guarded": "附件简历入口重复点击已拦截",
            "resume_preview_detected_wait_confirm": "已识别简历页面，等待确认后进入人工点击学习",
            "manual_download_click_timeout": "等待人工点击下载超时",
            "download_button_not_found": "未找到简历下载按钮",
            "download_timeout": "下载等待超时",
            "download_failed": "下载失败",
            "no_resume_button": "未找到附件简历按钮",
            "candidate_info_unrecognized": "候选人信息未识别",
            "click_failed": "点击候选人失败",
            "new_collect_started": "新采集任务已开始",
            "collect_stopped": "采集已停止",
            "unknown": "未知原因",
        }
        return mapping.get(reason, reason)

    def _record_skip(self, data: dict) -> None:
        candidate_sig = data.get("candidate_signature", "未知")
        reason = data.get("reason", "") or "unknown"
        reason_text = self._translate_skip_reason(reason)
        record_key = str(candidate_sig)
        is_duplicate_record = record_key in self._seen_candidate_records

        self._log("info", f"跳过: {candidate_sig} ({reason_text})")
        self._write_event_log("candidate_skipped_seen", {
            "signature": candidate_sig,
            "reason": reason,
            "reason_text": reason_text,
            "duplicate_record": is_duplicate_record,
            "at": datetime.now().isoformat(),
        })

        if is_duplicate_record:
            return

        self._seen_candidate_records.add(record_key)
        self.runtime_state["skipped_count"] += 1
        counts = dict(self.runtime_state.get("skip_reason_counts", {}))
        counts[reason] = counts.get(reason, 0) + 1
        self.runtime_state["skip_reason_counts"] = counts
        record = {
            "signature": candidate_sig,
            "status": "skipped",
            "reason": reason,
            "reason_text": reason_text,
            "at": datetime.now().isoformat(),
        }
        self.runtime_state["candidates"].append(record)
        self._write_event_log("candidate_skipped_recorded", record)

    def _log(self, level: str, message: str) -> None:
        now = datetime.now()
        dedupe_key = f"{level}|{message}"
        dedupe_until = self._recent_ui_log_keys.get(dedupe_key)
        if dedupe_until and now.timestamp() - dedupe_until < 2.5:
            return
        self._recent_ui_log_keys[dedupe_key] = now.timestamp()
        if len(self._recent_ui_log_keys) > 300:
            cutoff = now.timestamp() - 10
            self._recent_ui_log_keys = {
                key: value for key, value in self._recent_ui_log_keys.items() if value >= cutoff
            }
        entry = {
            "level": level,
            "message": message,
            "at": now.strftime("%H:%M:%S"),
        }
        self.runtime_state["last_event_at"] = now.isoformat(timespec="seconds")
        logs = self.runtime_state["logs"]
        logs.append(entry)
        if len(logs) > 500:
            self.runtime_state["logs"] = logs[-400:]
        self._write_event_log("ui_log", entry)
        getattr(logger, level, logger.info)(message)

    def _write_event_log(self, event_name: str, payload: dict[str, Any]) -> None:
        log_file = self.runtime_state.get("log_file")
        if not log_file:
            return
        self._event_seq += 1
        now = datetime.now()
        row = {
            "seq": self._event_seq,
            "at": now.isoformat(timespec="milliseconds"),
            "run_id": self.runtime_state.get("run_id", ""),
            "event": event_name,
            "payload": payload,
        }
        self.runtime_state["last_event_at"] = now.isoformat(timespec="seconds")
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            with Path(log_file).open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            logger.warning("写入 Boss Extension 测试日志失败: {}", exc)
