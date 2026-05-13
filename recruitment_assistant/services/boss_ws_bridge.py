"""Bridge between WebSocket events and Boss business logic."""

import json
import shutil
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from loguru import logger

from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.services.ws_server import BossWSServer
from recruitment_assistant.utils.snapshot_utils import safe_filename



class BossWSBridge:
    def __init__(self, ws_server: BossWSServer):
        self.ws_server = ws_server
        self.ws_server.on_event = self._handle_event
        self._event_seq = 0
        self._seen_candidate_records: set[str] = set()

        self.runtime_state: dict[str, Any] = {
            "running": False,
            "paused": False,
            "extension_connected": False,
            "page_ready": False,
            "logs": [],
            "candidates": [],
            "downloaded_count": 0,
            "skipped_count": 0,
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
        self.runtime_state.update({
            "running": False,
            "paused": False,
            "logs": [],
            "candidates": [],
            "downloaded_count": 0,
            "skipped_count": 0,
            "current_index": 0,
            "run_id": run_id,
            "run_started_at": now.isoformat(timespec="seconds"),
            "log_file": str(log_file),
            "skip_reason_counts": {},
        })
        self._write_event_log("run_started", {"run_id": run_id})
        self._log("info", f"新测试轮次已创建: {run_id}")
        if self.ws_server.is_extension_connected:
            command = {"type": "reset_content_script", "run_id": self.runtime_state.get("run_id", "")}
            self.ws_server.send_command(command)
            self._write_event_log("command_sent", command)
            self._log("info", "已请求扩展重新加载 Boss 页面脚本")
            self.probe_page()

    def start_collect(self, config: dict) -> None:
        if not self.ws_server.is_extension_connected:
            self._log("error", "扩展未连接，无法开始采集")
            self._write_event_log("command_rejected", {"command": "start_collect", "reason": "extension_not_connected"})
            return
        self.runtime_state["running"] = True
        self.runtime_state["paused"] = False
        self.runtime_state["downloaded_count"] = 0
        self.runtime_state["skipped_count"] = 0
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
                self._log("info", f"Boss 沟通页已就绪: {data.get('url', '')}")
            case "page_detected":
                self.runtime_state["page_ready"] = False
                self.runtime_state["page_url"] = data.get("url", "")
                self._log("info", f"已检测到 Boss 页面但未确认登录态: {data.get('url', '')}")
            case "boss_tabs_scanned":
                urls = data.get("urls") or []
                self._log("info", f"扩展扫描到 Boss 标签页 {data.get('count', 0)} 个: {' | '.join(urls[:3])}")
            case "content_script_inject_failed":
                self._log("error", f"注入 Boss 页面脚本失败: {data.get('url', '')}")
            case "content_script_message_failed":
                self._log("error", f"Boss 页面脚本通信失败: {data.get('error', '')} {data.get('url', '')}")
            case "candidate_clicked":
                sig = f"{data.get('name', '?')}/{data.get('age', '?')}/{data.get('education', '?')}"
                self._log("info", f"点击候选人: {sig} (#{data.get('index', 0)})")
            case "resume_downloaded":
                self._save_resume(data)
            case "candidate_skipped":
                self._record_skip(data)
            case "candidate_list_scanned":
                samples = data.get("samples") or []
                sample_text = " | ".join(str(x) for x in samples[:3])
                suffix = f"；样例: {sample_text}" if sample_text else ""
                self._log("info", f"候选人列表扫描完成: {data.get('count', 0)} 个候选项{suffix}")
            case "resume_button_found":
                sig = data.get("candidate_signature", "未知")
                state = str(data.get("button_state", "unknown"))
                text = str(data.get("button_text", ""))
                message = f"附件按钮: {sig} [{state}] {text[:60]}"
                if state in {"view", "unknown_resume"} or "附件简历" in text:
                    self._log("highlight", message)
                else:
                    self._log("info", message)
            case "download_intent_registered":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"下载意图已登记: {sig}")
            case "download_created":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"Chrome 下载已创建: {sig} #{data.get('download_id', '')}")
            case "candidate_list_scroll_reset":
                self._log("info", f"候选人列表已回到顶部: {data.get('reset', False)}")
            case "resume_request_confirm_clicked":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"已点击索要简历确认: {sig}")
            case "resume_attachment_clicked":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"已点击附件简历入口: {sig} [{data.get('button_state', '')}] {str(data.get('button_text', ''))[:60]}")
                self._log("highlight", "开始识别弹出页面")
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
                sig = data.get("candidate_signature", "未知")
                overlays = data.get("overlays") or []
                frames = data.get("frames") or []
                large_blocks = data.get("large_blocks") or []
                reason = data.get("reason", "")
                self._log("highlight", f"弹出页识别诊断: {sig}；阶段={reason}")
                if "李子志" in str(sig) or "陈柱荣" in str(sig):
                    self._log("highlight", f"{str(sig).split('/')[0]}确认有附件简历，本次未发现弹出页面，判定为识别失败。")
                self._log("info", f"诊断URL: {data.get('url', '')}")
                self._log("info", f"候选弹层={len(overlays)} 个，iframe/object/embed={len(frames)} 个，大块DOM={len(large_blocks)} 个")
                for index, item in enumerate(overlays[:5], start=1):
                    rect = item.get("rect") or {}
                    text = str(item.get("text", ""))[:120]
                    class_name = str(item.get("class_name", ""))[:80]
                    self._log("info", f"弹层候选#{index}: {item.get('tag', '')} class={class_name} rect=({rect.get('left')},{rect.get('top')},{rect.get('width')}x{rect.get('height')}) text={text}")
                for index, item in enumerate(frames[:5], start=1):
                    rect = item.get("rect") or {}
                    src = str(item.get("src", ""))[:120]
                    self._log("info", f"内嵌页候选#{index}: {item.get('tag', '')} rect=({rect.get('left')},{rect.get('top')},{rect.get('width')}x{rect.get('height')}) src={src}")
                for index, item in enumerate(large_blocks[:5], start=1):
                    rect = item.get("rect") or {}
                    text = str(item.get("text", ""))[:120]
                    class_name = str(item.get("class_name", ""))[:80]
                    self._log("info", f"大块DOM#{index}: {item.get('tag', '')} class={class_name} rect=({rect.get('left')},{rect.get('top')},{rect.get('width')}x{rect.get('height')}) text={text}")
            case "boss_ui_stage":
                message = str(data.get("message", ""))
                if message:
                    self._log("highlight", message)
            case "resume_preview_recognition_started":
                sig = data.get("candidate_signature", "未知")
                stage = data.get("stage", "")
                self._log("highlight", f"开始识别弹出页面: {sig}；真实等待入口={stage}")
            case "resume_preview_wait_result":
                sig = data.get("candidate_signature", "未知")
                if data.get("found"):
                    self._log("highlight", f"弹出页面识别等待完成: {sig}；结果=已发现")
                else:
                    suffix = "；发现弱候选但未接受" if data.get("weak_candidate") else ""
                    self._log("error", f"弹出页面识别等待完成: {sig}；结果=未发现{suffix}")
            case "resume_preview_weak_candidate_used":
                sig = data.get("candidate_signature", "未知")
                descriptor = str(data.get("descriptor", ""))[:120]
                self._log("highlight", f"疑似发现弹出页面: {sig}；候选区域={descriptor}")
            case "resume_preview_info_extract_start":
                self._log("info", "开始尝试获取弹出页面中的信息……")
            case "resume_preview_detected":
                self._log("highlight", "发现弹出页面")
            case "resume_preview_info_extract_success":
                name = data.get("name", "未识别")
                phone = data.get("phone", "未识别")
                email = data.get("email", "未识别")
                self._log("highlight", f"成功获取以下信息：候选人名字“{name}”，电话“{phone}”，邮箱“{email}”")
            case "collect_paused_for_resume_preview_confirm":
                self.runtime_state["paused"] = True
                self._log("info", "采集任务已暂停，请确认弹出页面识别正确后点击“继续”")
            case "manual_download_recording_started":
                self._log("highlight", "正在记录你的操作……")
            case "manual_download_click_captured":
                descriptor = str(data.get("descriptor", ""))[:160]
                self._log("info", f"已捕获点击操作: {descriptor}，位置=({data.get('x')},{data.get('y')})")
            case "manual_download_learning_success":
                component = str(data.get("descriptor", "") or data.get("tag", "未知组件"))[:120]
                position = f"({data.get('x')},{data.get('y')})"
                url = data.get("download_url", "未捕获到下载链接")
                self._log("highlight", f"成功记录到你的点击操作，你点击了弹出页面上的{component}组件，位置在{position}，点击后捕获到以下下载连接：{url}，学习任务成功！")
            case "manual_download_click_timeout":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"等待人工点击下载超时: {sig}")
            case "learned_download_click_used":
                sig = data.get("candidate_signature", "未知")
                descriptor = str(data.get("descriptor", ""))[:120]
                self._log("info", f"使用已学习下载控件尝试点击: {sig}；{descriptor}")
            case "learned_download_click_failed":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"已学习下载控件未找到: {sig}")
            case "download_button_candidates":
                sig = data.get("candidate_signature", "未知")
                samples = str(data.get("samples", ""))[:120]
                self._log("error", f"未找到下载图标: {sig}；候选控件: {samples}")
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

    def _save_resume(self, data: dict) -> None:
        settings = get_settings()
        candidate_sig = data.get("candidate_signature", "未知")
        candidate_info = data.get("candidate_info", {})
        source_filename = data.get("filename", "")
        download_path = data.get("download_path", "")

        if download_path:
            source = Path(download_path)
            if source.exists():
                now = datetime.now()
                target_dir = settings.attachment_dir / "boss" / now.strftime("%Y%m%d")
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
                shutil.move(str(source), str(target))
                record_key = str(candidate_sig)
                if record_key in self._seen_candidate_records:
                    return
                self._seen_candidate_records.add(record_key)

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
                    "at": now.isoformat(),
                }
                self.runtime_state["candidates"].append(record)
                self._write_event_log("resume_saved", record)
                return

        record_key = str(candidate_sig)
        if record_key in self._seen_candidate_records:
            return
        self._seen_candidate_records.add(record_key)
        self.runtime_state["downloaded_count"] += 1
        record = {
            "signature": candidate_sig,
            "info": candidate_info,
            "file": source_filename,
            "download_id": data.get("download_id", ""),
            "url": data.get("url", ""),
            "status": "downloaded_external",
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
            "resume_requested": "已成功索要简历，等待候选人上传",
            "resume_request_clicked": "已点击索要简历，等待确认结果",
            "resume_request_already_sent": "简历请求已发送，等待候选人上传",
            "resume_already_requested": "此前已索要简历，等待候选人上传",
            "resume_preview_not_found": "未识别到简历弹出页面",
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
        record_key = str(candidate_sig)
        if record_key in self._seen_candidate_records:
            return
        self._seen_candidate_records.add(record_key)
        self.runtime_state["skipped_count"] += 1
        counts = dict(self.runtime_state.get("skip_reason_counts", {}))
        counts[reason] = counts.get(reason, 0) + 1
        self.runtime_state["skip_reason_counts"] = counts
        reason_text = self._translate_skip_reason(reason)
        record = {
            "signature": candidate_sig,
            "status": "skipped",
            "reason": reason,
            "reason_text": reason_text,
            "at": datetime.now().isoformat(),
        }
        self.runtime_state["candidates"].append(record)
        self._write_event_log("candidate_skipped_recorded", record)
        self._log("info", f"跳过: {candidate_sig} ({reason_text})")

    def _log(self, level: str, message: str) -> None:
        now = datetime.now()
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
