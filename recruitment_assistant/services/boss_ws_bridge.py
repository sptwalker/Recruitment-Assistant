"""Bridge between WebSocket events and Boss business logic."""

import shutil
import time
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from loguru import logger

from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.platforms.boss.adapter import BossAdapter
from recruitment_assistant.services.ws_server import BossWSServer


class BossWSBridge:
    def __init__(self, ws_server: BossWSServer, adapter: BossAdapter | None = None):
        self.ws_server = ws_server
        self.adapter = adapter or BossAdapter()
        self.ws_server.on_event = self._handle_event

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
        }

    @property
    def is_ready(self) -> bool:
        return self.ws_server.is_extension_connected and self.runtime_state.get("page_ready", False)

    def start_collect(self, config: dict) -> None:
        if not self.ws_server.is_extension_connected:
            self._log("error", "扩展未连接，无法开始采集")
            return
        self.runtime_state["running"] = True
        self.runtime_state["paused"] = False
        self.runtime_state["downloaded_count"] = 0
        self.runtime_state["skipped_count"] = 0
        self.runtime_state["current_index"] = 0
        self.runtime_state["candidates"] = []
        self.ws_server.send_command({"type": "start_collect", "config": config})
        self._log("info", f"采集指令已下发，最大数量={config.get('max_resumes', 5)}")

    def pause_collect(self) -> None:
        self.runtime_state["paused"] = True
        self.ws_server.send_command({"type": "pause_collect"})
        self._log("info", "暂停指令已下发")

    def resume_collect(self) -> None:
        self.runtime_state["paused"] = False
        self.ws_server.send_command({"type": "resume_collect"})
        self._log("info", "继续指令已下发")

    def stop_collect(self) -> None:
        self.runtime_state["running"] = False
        self.runtime_state["paused"] = False
        self.ws_server.send_command({"type": "stop_collect"})
        self._log("info", "停止指令已下发")

    def _handle_event(self, event: dict) -> None:
        event_type = event.get("type", "")
        data = event.get("data", {})

        match event_type:
            case "extension_connected":
                self.runtime_state["extension_connected"] = True
                self._log("info", f"扩展已连接 v{data.get('version', '?')}")
            case "page_ready":
                self.runtime_state["page_ready"] = True
                self._log("info", f"Boss 沟通页已就绪: {data.get('url', '')}")
            case "candidate_clicked":
                sig = f"{data.get('name', '?')}/{data.get('age', '?')}/{data.get('education', '?')}"
                self._log("info", f"点击候选人: {sig} (#{data.get('index', 0)})")
            case "resume_downloaded":
                self._save_resume(data)
            case "candidate_skipped":
                self._record_skip(data)
            case "collect_progress":
                self.runtime_state["downloaded_count"] = data.get("downloaded", 0)
                self.runtime_state["skipped_count"] = data.get("skipped", 0)
                self.runtime_state["current_index"] = data.get("current_index", 0)
            case "collect_finished":
                self.runtime_state["running"] = False
                total = data.get("total_downloaded", 0)
                self._log("info", f"采集完成，共下载 {total} 份简历")
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
                self.runtime_state["candidates"].append({
                    "signature": candidate_sig,
                    "info": candidate_info,
                    "file": filename,
                    "path": str(target),
                    "hash": file_hash,
                    "status": "downloaded",
                    "at": now.isoformat(),
                })
                return

        self.runtime_state["downloaded_count"] += 1
        self.runtime_state["candidates"].append({
            "signature": candidate_sig,
            "info": candidate_info,
            "file": source_filename,
            "status": "downloaded_external",
            "at": datetime.now().isoformat(),
        })
        self._log("info", f"简历已下载: {candidate_sig} -> {source_filename}")

    def _record_skip(self, data: dict) -> None:
        candidate_sig = data.get("candidate_signature", "未知")
        reason = data.get("reason", "")
        self.runtime_state["skipped_count"] += 1
        self.runtime_state["candidates"].append({
            "signature": candidate_sig,
            "status": "skipped",
            "reason": reason,
            "at": datetime.now().isoformat(),
        })
        self._log("info", f"跳过: {candidate_sig} ({reason})")

    def _log(self, level: str, message: str) -> None:
        entry = {
            "level": level,
            "message": message,
            "at": datetime.now().strftime("%H:%M:%S"),
        }
        logs = self.runtime_state["logs"]
        logs.append(entry)
        if len(logs) > 500:
            self.runtime_state["logs"] = logs[-400:]
        getattr(logger, level, logger.info)(message)
