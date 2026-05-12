"""Bridge between WebSocket events and Boss business logic."""

import json
import shutil
import time
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from loguru import logger

from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.services.ws_server import BossWSServer


class BossWSBridge:
    def __init__(self, ws_server: BossWSServer):
        self.ws_server = ws_server
        self.ws_server.on_event = self._handle_event
        self._event_seq = 0

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
            "last_event_at": "",
            "skip_reason_counts": {},
        })
        self._write_event_log("run_started", {"run_id": run_id})
        self._log("info", f"新测试轮次已创建: {run_id}")

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
            case "page_ready":
                self.runtime_state["page_ready"] = True
                self.runtime_state["page_url"] = data.get("url", "")
                self._log("info", f"Boss 沟通页已就绪: {data.get('url', '')}")
            case "candidate_clicked":
                sig = f"{data.get('name', '?')}/{data.get('age', '?')}/{data.get('education', '?')}"
                self._log("info", f"点击候选人: {sig} (#{data.get('index', 0)})")
            case "resume_downloaded":
                self._save_resume(data)
            case "candidate_skipped":
                self._record_skip(data)
            case "candidate_list_scanned":
                self._log("info", f"候选人列表扫描完成: {data.get('count', 0)} 个候选项")
            case "resume_button_found":
                sig = data.get("candidate_signature", "未知")
                state = data.get("button_state", "unknown")
                text = data.get("button_text", "")
                self._log("info", f"附件按钮: {sig} [{state}] {text[:60]}")
            case "download_intent_registered":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"下载意图已登记: {sig}")
            case "download_created":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"Chrome 下载已创建: {sig} #{data.get('download_id', '')}")
            case "collect_progress":
                self.runtime_state["skipped_count"] = data.get("skipped", 0)
                self.runtime_state["current_index"] = data.get("current_index", 0)
            case "collect_finished":
                self.runtime_state["running"] = False
                total = data.get("total_downloaded", 0)
                self._log("info", f"采集完成，共下载 {total} 份简历")
                self.get_run_summary()
            case "error":
                self._log("error", f"扩展错误: {data.get('message', '未知错误')}")
            case _:
                logger.debug("未处理的扩展事件: {}", event_type)

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

                name = candidate_info.get("name", "未知")
                age = candidate_info.get("age", "未知")
                education = candidate_info.get("education", "未知")
                seq = self.runtime_state["downloaded_count"] + 1
                filename = f"{name}-{age}-{education}-BOSS直聘-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}-{seq:03d}{suffix}"

                target = target_dir / filename
                shutil.move(str(source), str(target))

                self._log("info", f"简历已保存: {filename}")
                self.runtime_state["downloaded_count"] = seq
                record = {
                    "signature": candidate_sig,
                    "info": candidate_info,
                    "file": filename,
                    "path": str(target),
                    "hash": file_hash,
                    "status": "downloaded",
                    "at": now.isoformat(),
                }
                self.runtime_state["candidates"].append(record)
                self._write_event_log("resume_saved", record)
                return

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

    def _record_skip(self, data: dict) -> None:
        candidate_sig = data.get("candidate_signature", "未知")
        reason = data.get("reason", "") or "unknown"
        self.runtime_state["skipped_count"] += 1
        counts = dict(self.runtime_state.get("skip_reason_counts", {}))
        counts[reason] = counts.get(reason, 0) + 1
        self.runtime_state["skip_reason_counts"] = counts
        record = {
            "signature": candidate_sig,
            "status": "skipped",
            "reason": reason,
            "at": datetime.now().isoformat(),
        }
        self.runtime_state["candidates"].append(record)
        self._write_event_log("candidate_skipped_recorded", record)
        self._log("info", f"跳过: {candidate_sig} ({reason})")

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
