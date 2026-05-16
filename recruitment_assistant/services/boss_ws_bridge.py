"""Bridge between WebSocket events and Boss business logic."""

import json
import shutil
import threading
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from loguru import logger

from app.components.layout import APP_VERSION
from recruitment_assistant.services.crawl_task_service import BossCandidateRecordService, CrawlTaskService
from recruitment_assistant.services.ws_server import BossWSServer
from recruitment_assistant.storage.db import create_session
from recruitment_assistant.storage.models import CrawlTask
from recruitment_assistant.utils.hash_utils import text_hash
from recruitment_assistant.utils.snapshot_utils import safe_filename


BOSS_BRIDGE_VERSION = "1.73.1"
BOSS_EXTENSION_EXPECTED_VERSION = "1.66.0"
BOSS_CONTENT_SCRIPT_EXPECTED_VERSION = "1.67.0"


class BossWSBridge:
    def __init__(self, ws_server: BossWSServer):
        self.ws_server = ws_server
        self.ws_server.on_event = self._handle_event
        self._event_seq = 0
        self._seen_candidate_records: set[str] = set()
        self._seen_skip_records: set[str] = set()
        self._recent_ui_log_keys: dict[str, float] = {}
        self._saved_resume_hash_signatures: dict[str, str] = {}
        self._collect_timer: threading.Timer | None = None

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
            "resume_request_count": 0,
            "current_index": 0,
            "scanned_count": 0,
            "run_id": "",
            "run_started_at": "",
            "log_file": "",
            "last_event_at": "",
            "last_heartbeat_at": "",
            "extension_version": "",
            "page_url": "",
            "skip_reason_counts": {},
            "task_id": None,
            "task_started_at": "",
            "task_planned_count": 0,
            "task_status": "pending",
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
        self._seen_skip_records.clear()
        self._recent_ui_log_keys.clear()
        self._saved_resume_hash_signatures.clear()
        self.runtime_state.update({
            "running": False,
            "paused": False,
            "logs": [],
            "candidates": [],
            "downloaded_count": 0,
            "skipped_count": 0,
            "dedup_record_count": 0,
            "resume_request_count": 0,
            "current_index": 0,
            "scanned_count": 0,
            "run_id": run_id,
            "run_started_at": now.isoformat(timespec="seconds"),
            "log_file": str(log_file),
            "skip_reason_counts": {},
            "task_id": None,
            "task_started_at": "",
            "task_planned_count": 0,
            "task_status": "pending",
        })
        self._seen_candidate_records.update(self._load_boss_candidate_keys())
        self._write_event_log("run_started", {"run_id": run_id})
        self._log("info", f"新测试轮次已创建: {run_id}")
        self._log(
            "info",
            "版本信息: "
            f"页面={APP_VERSION}；"
            f"BOSS后端桥接={BOSS_BRIDGE_VERSION}；"
            f"期望扩展={BOSS_EXTENSION_EXPECTED_VERSION}；"
            f"期望内容脚本={BOSS_CONTENT_SCRIPT_EXPECTED_VERSION}；"
            f"当前已连接扩展={self.runtime_state.get('extension_version') or '未连接'}",
        )
        self._write_event_log("module_versions", {
            "app_version": APP_VERSION,
            "boss_bridge_version": BOSS_BRIDGE_VERSION,
            "expected_extension_version": BOSS_EXTENSION_EXPECTED_VERSION,
            "expected_content_script_version": BOSS_CONTENT_SCRIPT_EXPECTED_VERSION,
            "connected_extension_version": self.runtime_state.get("extension_version") or "",
        })
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

    def _create_crawl_task(self, config: dict) -> None:
        planned_count = int(config.get("max_resumes") or 0) or None
        task_config = dict(config or {})
        task_config.pop("boss_candidate_keys", None)
        task_config.pop("boss_candidate_signatures", None)
        try:
            with create_session() as session:
                task = CrawlTaskService(session).create_task(
                    platform_code="boss",
                    task_name=f"BOSS采集-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    task_type="chat_attachment_resume",
                    query_params=task_config,
                    planned_count=planned_count,
                )
                task_id = task.id
                started_at = task.started_at.isoformat(timespec="seconds") if task.started_at else ""
            self.runtime_state["task_id"] = task_id
            self.runtime_state["task_started_at"] = started_at
            self.runtime_state["task_planned_count"] = planned_count or 0
            self.runtime_state["task_status"] = "running"
            self._write_event_log("boss_crawl_task_created", {"task_id": task_id, "planned_count": planned_count})
            self._log("info", f"BOSS 历史批次任务已创建: #{task_id}")
        except Exception as exc:
            self.runtime_state["task_id"] = None
            self.runtime_state["task_status"] = "create_failed"
            self._log("warning", f"BOSS 历史批次任务创建失败: {exc}")

    def _finish_crawl_task(self, status: str, error_message: str | None = None) -> None:
        task_id = self.runtime_state.get("task_id")
        if not task_id or self.runtime_state.get("task_status") not in {"running", "create_failed"}:
            return
        if self.runtime_state.get("task_status") == "create_failed":
            return
        success_count = int(self.runtime_state.get("downloaded_count", 0) or 0)
        failed_count = int(self.runtime_state.get("skipped_count", 0) or 0)
        try:
            with create_session() as session:
                task = session.get(CrawlTask, task_id)
                if not task:
                    self._log("warning", f"BOSS 历史批次任务不存在，无法完成: #{task_id}")
                    return
                CrawlTaskService(session).finish_task(
                    task,
                    status=status,
                    success_count=success_count,
                    failed_count=failed_count,
                    error_message=error_message,
                )
            self.runtime_state["task_status"] = status
            self._write_event_log("boss_crawl_task_finished", {
                "task_id": task_id,
                "status": status,
                "success_count": success_count,
                "failed_count": failed_count,
                "error_message": error_message,
            })
            self._log("info", f"BOSS 历史批次任务已更新: #{task_id}，状态={status}，获取={success_count}，跳过={failed_count}")
        except Exception as exc:
            self._log("warning", f"BOSS 历史批次任务更新失败: #{task_id}；原因={exc}")

    def _on_task_finished(self, status: str) -> None:
        """采集任务收尾钩子：关闭弹窗、对账 Chrome 下载目录、输出本轮指标。"""
        self._cancel_collect_timer()
        run_id = self.runtime_state.get("run_id", "")

        # 1) 通知扩展关闭所有简历预览弹窗（扩展端 1.67.0 暂不识别此指令，待后续放开扩展限制时落地）
        try:
            self.ws_server.send_command({"type": "close_all_resume_previews", "run_id": run_id})
            self._write_event_log("command_sent", {"type": "close_all_resume_previews", "run_id": run_id})
        except Exception as exc:
            logger.debug("close_all_resume_previews 下发失败: {}", exc)

        # 2) 与 Chrome 下载目录对账
        missed_files: list[tuple[Path, int]] = []
        try:
            from recruitment_assistant.config.settings import get_settings

            run_started_at = self.runtime_state.get("run_started_at", "")
            run_started_ts = 0.0
            if run_started_at:
                try:
                    run_started_ts = datetime.fromisoformat(run_started_at).timestamp()
                except ValueError:
                    run_started_ts = 0.0
            chrome_dir = Path.home() / "Downloads" / "Boss直聘"
            settings = get_settings()
            today_archive = (settings.attachment_dir / "boss" / datetime.now().strftime("%Y%m%d")).resolve()
            archived_stems = {p.stem for p in today_archive.glob("*.pdf")} if today_archive.exists() else set()
            if chrome_dir.exists():
                for pdf in chrome_dir.glob("*.pdf"):
                    try:
                        mtime = pdf.stat().st_mtime
                    except OSError:
                        continue
                    if run_started_ts and mtime < run_started_ts:
                        continue
                    # 名字前缀（候选人姓名-年龄-…）与归档 stem 比较
                    matched = False
                    for archived in archived_stems:
                        if archived and archived[:8] and archived[:8] in pdf.stem:
                            matched = True
                            break
                    if not matched:
                        try:
                            missed_files.append((pdf.resolve(), pdf.stat().st_size))
                        except OSError:
                            missed_files.append((pdf.resolve(), 0))
            if missed_files:
                self._log("highlight", f"⚠ Chrome 下载目录有 {len(missed_files)} 个未归档的 BOSS PDF：")
                for idx, (path, size) in enumerate(missed_files[:10], 1):
                    kb = size / 1024 if size else 0
                    self._log("highlight", f"  {idx}) {path} ({kb:.1f} KB)")
            else:
                self._log("info", "Chrome 下载目录已与归档目录对账完成（无遗漏）")
        except Exception as exc:
            self._log("warning", f"对账 Chrome 下载目录失败：{exc}")

        # 3) 计算并输出本轮采集指标
        try:
            task_started_at = self.runtime_state.get("task_started_at", "") or self.runtime_state.get("run_started_at", "")
            elapsed_sec = 0.0
            if task_started_at:
                try:
                    elapsed_sec = max(0.0, datetime.now().timestamp() - datetime.fromisoformat(task_started_at).timestamp())
                except ValueError:
                    elapsed_sec = 0.0
            downloaded = int(self.runtime_state.get("downloaded_count", 0) or 0)
            skipped = int(self.runtime_state.get("skipped_count", 0) or 0)
            avg_per_download = (elapsed_sec / downloaded) if downloaded else 0.0
            skip_reason_counts = dict(self.runtime_state.get("skip_reason_counts", {}) or {})
            candidates = self.runtime_state.get("candidates", []) or []
            failure_counts: Counter[str] = Counter()
            for c in candidates:
                if c.get("status") in {"failed", "error", "download_failed"}:
                    failure_counts[str(c.get("reason") or c.get("status") or "unknown")] += 1
            metrics = {
                "run_id": run_id,
                "status": status,
                "elapsed_sec": round(elapsed_sec, 1),
                "downloaded": downloaded,
                "skipped": skipped,
                "avg_per_download_sec": round(avg_per_download, 1),
                "skip_reason_counts": skip_reason_counts,
                "failure_counts": dict(failure_counts),
                "chrome_download_missed": len(missed_files),
            }
            self._write_event_log("run_metrics_summary", metrics)

            self._log("highlight", "━━━ 本轮采集指标 ━━━")
            self._log("highlight", f"总耗时={int(elapsed_sec)}s；下载={downloaded} (avg {avg_per_download:.1f}s/份)；跳过={skipped}")
            if skip_reason_counts:
                top = "；".join(f"{k}={v}" for k, v in sorted(skip_reason_counts.items(), key=lambda kv: -kv[1])[:5])
                self._log("highlight", f"跳过分布：{top}")
            if failure_counts:
                top = "；".join(f"{k}={v}" for k, v in failure_counts.most_common(5))
                self._log("highlight", f"失败分布：{top}")
            else:
                self._log("highlight", "失败分布：无")
        except Exception as exc:
            self._log("warning", f"生成本轮采集指标失败：{exc}")

    def start_collect(self, config: dict) -> None:
        if not self.ws_server.is_extension_connected:
            self._log("error", "扩展未连接，无法开始采集")
            self._write_event_log("command_rejected", {"command": "start_collect", "reason": "extension_not_connected"})
            return
        self._cancel_collect_timer()
        self.runtime_state["running"] = True
        self.runtime_state["paused"] = False
        self.runtime_state["downloaded_count"] = 0
        self.runtime_state["skipped_count"] = 0
        self.runtime_state["dedup_record_count"] = 0
        self.runtime_state["resume_request_count"] = 0
        self.runtime_state["current_index"] = 0
        self.runtime_state["scanned_count"] = 0
        self.runtime_state["candidates"] = []
        self.runtime_state["skip_reason_counts"] = {}
        self.runtime_state["task_id"] = None
        self.runtime_state["task_started_at"] = ""
        self.runtime_state["task_planned_count"] = 0
        self.runtime_state["task_status"] = "pending"
        boss_candidate_keys = self._load_boss_candidate_keys()
        boss_candidate_signatures = self._load_boss_candidate_signatures()
        self._seen_candidate_records.update(boss_candidate_keys)
        config = dict(config or {})

        collect_mode = str(config.get("collect_mode") or "按数量采集")
        collect_minutes = int(config.get("collect_minutes") or 0)
        if collect_mode == "按时间采集":
            # 扩展端按 max_resumes 计数，时间模式下设置一个大上限交由定时器收尾
            config["max_resumes"] = 9999
            self.runtime_state["task_planned_count"] = 0
            self._log("info", f"采集模式=按时间采集；将在 {collect_minutes} 分钟后自动停止")
        else:
            collect_minutes = 0

        config["boss_candidate_keys"] = sorted(self._seen_candidate_records)
        config["boss_candidate_signatures"] = sorted(boss_candidate_signatures)
        config["boss_pre_dedup_ready"] = True
        self._create_crawl_task(config)
        command = {"type": "start_collect", "config": config, "run_id": self.runtime_state.get("run_id", "")}
        self.ws_server.send_command(command)
        self._write_event_log("command_sent", command)
        self._log("info", f"BOSS 下载前去重数据已下发: key={len(config['boss_candidate_keys'])} 条；签名={len(config['boss_candidate_signatures'])} 条")
        if collect_mode == "按时间采集":
            self._log("info", f"采集指令已下发，采集时间={collect_minutes}分钟")
            self._start_collect_timer(collect_minutes)
        else:
            self._log("info", f"采集指令已下发，目标下载数={config.get('max_resumes', 5)}")

    def _start_collect_timer(self, minutes: int) -> None:
        seconds = max(1, int(minutes)) * 60
        run_id = self.runtime_state.get("run_id", "")
        timer = threading.Timer(seconds, self._on_collect_timer_fire, args=(run_id,))
        timer.daemon = True
        self._collect_timer = timer
        timer.start()
        self._write_event_log("collect_timer_started", {"run_id": run_id, "minutes": int(minutes)})

    def _on_collect_timer_fire(self, run_id: str) -> None:
        # 仅当仍在采集且 run_id 未切换时才触发停止，避免误杀新一轮任务
        if not self.runtime_state.get("running"):
            return
        if run_id and run_id != self.runtime_state.get("run_id", ""):
            return
        self._log("highlight", "采集时间已到，自动停止本轮采集")
        self._write_event_log("collect_timer_fired", {"run_id": run_id})
        try:
            self.stop_collect()
        except Exception as exc:
            self._log("warning", f"定时停止失败：{exc}")

    def _cancel_collect_timer(self) -> None:
        timer = self._collect_timer
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        self._collect_timer = None

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
        self._cancel_collect_timer()
        self.runtime_state["running"] = False
        self.runtime_state["paused"] = False
        self._finish_crawl_task("cancelled")
        command = {"type": "stop_collect", "run_id": self.runtime_state.get("run_id", "")}
        self.ws_server.send_command(command)
        self._write_event_log("command_sent", command)
        self._log("info", "停止指令已下发")

    def probe_page(self) -> None:
        command = {"type": "probe_page", "run_id": self.runtime_state.get("run_id", "")}
        self.ws_server.send_command(command)
        self._write_event_log("command_sent", command)
        self._log("info", "已请求扩展重新检测 Boss 页面")

    def _send_persist_ack(
        self,
        candidate_sig: str,
        candidate_info: dict | None,
        status: str,
        download_request_id: str = "",
        reason: str = "",
        extra: dict | None = None,
    ) -> None:
        """通知 content script 桥侧持久化结果，避免 results.completed 与实际归档错位。"""
        if not self.ws_server.is_extension_connected:
            return
        payload = {
            "type": "resume_persist_ack",
            "run_id": self.runtime_state.get("run_id", ""),
            "data": {
                "candidate_signature": candidate_sig,
                "candidate_info": candidate_info or {},
                "status": status,
                "download_request_id": download_request_id or "",
                "reason": reason or "",
            },
        }
        if extra:
            payload["data"].update(extra)
        self.ws_server.send_command(payload)
        self._write_event_log("resume_persist_ack_sent", payload["data"])

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
            "resume_request_count": self.runtime_state.get("resume_request_count", 0),
            "current_index": self.runtime_state.get("current_index", 0),
            "scanned_count": self.runtime_state.get("scanned_count", 0),
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
            case "boss_content_script_collect_started":
                version = data.get("content_script_version", "?")
                key_count = data.get("key_count", 0)
                signature_count = data.get("signature_count", 0)
                self._log("info", f"BOSS 内容脚本已启动采集 v{version}；去重 key={key_count} 条；签名={signature_count} 条")
                if version and version != BOSS_CONTENT_SCRIPT_EXPECTED_VERSION:
                    self._log("warning", f"BOSS 内容脚本版本不匹配: 当前={version}；期望={BOSS_CONTENT_SCRIPT_EXPECTED_VERSION}，请刷新 BOSS 页面")
            case "boss_pre_dedup_checked":
                sig = data.get("candidate_signature", "未知")
                key_count = data.get("key_count", 0)
                signature_count = data.get("signature_count", 0)
                key_hit = bool(data.get("key_hit"))
                signature_hit = bool(data.get("signature_hit"))
                elapsed = data.get("elapsed_ms")
                elapsed_text = f"；耗时={elapsed}ms" if elapsed is not None else ""
                hit = key_hit or signature_hit
                self._log("info", f"BOSS 下载前去重检查: {sig}；结果={'命中' if hit else '未命中'}{elapsed_text}")
            case "boss_resume_button_lookup_started":
                pass
            case "extension_connected":
                version = data.get("version", "")
                self.runtime_state["extension_connected"] = True
                self.runtime_state["extension_version"] = version
                self._log("info", f"扩展已连接 v{version or '?'}；期望版本={BOSS_EXTENSION_EXPECTED_VERSION}")
                if version and version != BOSS_EXTENSION_EXPECTED_VERSION:
                    self._log("warning", f"扩展版本不匹配: 当前={version}；期望={BOSS_EXTENSION_EXPECTED_VERSION}，请在 chrome://extensions/ 重新加载扩展")
            case "heartbeat":
                self.runtime_state["extension_connected"] = True
                self.runtime_state["last_heartbeat_at"] = datetime.now().isoformat(timespec="seconds")
                if data.get("version"):
                    self.runtime_state["extension_version"] = data.get("version", "")
            case "extension_disconnected":
                self.runtime_state["extension_connected"] = False
                self.runtime_state["page_ready"] = False
                was_running = bool(self.runtime_state.get("running"))
                self.runtime_state["running"] = False
                self.runtime_state["paused"] = False
                if was_running:
                    self._finish_crawl_task("failed", error_message=f"扩展连接已断开: {data.get('reason', 'unknown')}")
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
                elapsed = data.get("elapsed_ms")
                elapsed_text = f"；识别耗时={elapsed}ms" if elapsed is not None else ""
                self._log("info", f"点击候选人: {sig} (#{data.get('index', 0)}){elapsed_text}")
            case "resume_downloaded":
                self._save_resume(data)
            case "candidate_skipped":
                self._record_skip(data)
            case "candidate_list_scanned":
                pass
            case "resume_button_found":
                sig = data.get("candidate_signature", "未知")
                state = data.get("button_state", "")
                state_label = data.get("button_state_label") or ({"bright": "明亮", "dim": "暗淡"}.get(state, state or "未知"))
                elapsed = data.get("elapsed_ms")
                elapsed_text = f"；耗时={elapsed}ms" if elapsed is not None else ""
                if state == "bright":
                    # 明亮按钮紧跟点击下发，合并到下一条日志即可
                    logger.debug("附件简历按钮: sig={} 状态={}{}", sig, state_label, elapsed_text)
                else:
                    self._log("info", f"附件简历按钮: {sig}；状态={state_label}{elapsed_text}")
            case "resume_attachment_click_dispatched":
                sig = data.get("candidate_signature", "未知")
                ok = data.get("click_ok", False)
                tag = data.get("tag") or "?"
                el_id = data.get("id") or "(空)"
                cls = str(data.get("class_name", "")).strip() or "(空)"
                descriptor = str(data.get("descriptor", "")).strip()
                path = str(data.get("path", "")) or "(空)"
                x, y = data.get("x"), data.get("y")
                rect = data.get("rect") or {}
                size_text = ""
                if rect:
                    try:
                        size_text = f" {int(rect.get('width', 0))}×{int(rect.get('height', 0))}"
                    except (TypeError, ValueError):
                        size_text = ""
                state_label = data.get("button_state_label") or data.get("button_state") or "?"
                self._log("info", f"附件按钮点击: {sig}；按钮={state_label}；点击OK={ok}")
                logger.debug(
                    "附件按钮点击详情: sig={} tag={}({}) pos=({},{}){} class={} path={} descriptor={}",
                    sig, tag, el_id, x, y, size_text,
                    cls[:240], path[:320], descriptor[:280],
                )
            case "resume_attachment_debug":
                pass
            case "download_intent_registered":
                pass
            case "direct_download_request_received":
                pass
            case "direct_download_starting":
                pass
            case "download_created":
                sig = data.get("candidate_signature", "未知")
                source = "PDF iframe直接下载" if data.get("direct_url") else "Chrome点击下载"
                self._log("info", f"Chrome 下载已创建: {sig} #{data.get('download_id', '')}；来源={source}")
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
                if data.get("request_sent"):
                    self.runtime_state["resume_request_count"] = self.runtime_state.get("resume_request_count", 0) + 1
                    self._log("info", f"根据用户需求，将候选人{sig}索要了简历，并已检测到简历请求已发送")
                else:
                    self._log("warning", f"索要简历未确认成功，未计入索要人数: {sig}")
            case "resume_request_unconfirmed":
                sig = data.get("candidate_signature", "未知")
                confirmed = bool(data.get("confirmed"))
                if confirmed:
                    self._log("warning", f"已点击索要简历确认，但未检测到“简历请求已发送”: {sig}")
                else:
                    self._log("error", f"未找到索要简历确认按钮，未检测到“简历请求已发送”: {sig}")
            case "resume_request_confirm_not_found":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"未找到索要简历确认按钮: {sig}")
            case "resume_preview_not_found":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"未识别到简历弹出页面: {sig}")
            case "resume_preview_diagnostics":
                pass
            case "stale_resume_preview_ignored":
                sig = data.get("candidate_signature", "未知")
                if data.get("matched_current_candidate"):
                    self._log("highlight", f"旧预览指纹未变化但候选人信息匹配: {sig}")
            case "stale_resume_preview_detected":
                sig = data.get("candidate_signature", "未知")
                self._log("warning", f"检测到旧简历预览未刷新: {sig}；原因={data.get('reason', '')}；匹配当前候选人={data.get('matched_current_candidate', False)}")
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
                self._log("highlight", f"未发现 PDF iframe，已改用最大疑似弹窗继续下载识别: {sig}；类型={component_type}")
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
                logger.debug(
                    "扫描 PDF iframe 预览页: sig={} 可见frame={} 强匹配={}",
                    sig, data.get("total_frames", 0), data.get("strong_candidates", 0),
                )
            case "pdf_iframe_preview_detected":
                sig = data.get("candidate_signature", "未知")
                self._log("highlight", f"命中 PDF iframe 简历预览页: {sig}")
            case "resume_download_strategy_start":
                sig = data.get("candidate_signature", "未知")
                logger.debug(
                    "开始尝试捕获简历下载链接: sig={} PDF iframe={}",
                    sig, data.get("pdf_iframe", False),
                )
            case "direct_iframe_download_resolved":
                pass
            case "direct_iframe_download_start":
                pass
            case "direct_download_message_send":
                pass
            case "direct_download_message_response":
                pass
            case "direct_download_message_timeout":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"等待 Chrome 后台直接下载响应超时: {sig}；超时={data.get('timeout_ms', '')}ms")
            case "direct_iframe_download_skipped":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"跳过 iframe 直接下载: {sig}；原因={data.get('reason', '')}")
            case "direct_iframe_download_created":
                pass
            case "direct_iframe_download_link_captured":
                pass
            case "direct_iframe_download_failed":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"PDF iframe 下载链路失败: {sig}；原因={data.get('reason', '')}")
            case "direct_download_response_sent":
                pass
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
                logger.debug("开始扫描 boss-svg 下载组件: sig={}", sig)
            case "boss_svg_download_icon_found":
                sig = data.get("candidate_signature", "未知")
                path = str(data.get("component_path", ""))[:160]
                self._log("highlight", f"命中 boss-svg 下载组件: {sig}；路径={path}")
            case "boss_svg_download_icon_not_found":
                sig = data.get("candidate_signature", "未知")
                logger.debug("未命中 boss-svg 下载组件，尝试其他策略: sig={}", sig)
            case "boss_svg_download_icon_clicked":
                sig = data.get("candidate_signature", "未知")
                self._log("highlight", f"已点击 boss-svg 下载组件，等待 Chrome 下载事件: {sig}")
            case "download_button_candidates_detailed":
                sig = data.get("candidate_signature", "未知")
                candidates = data.get("candidates") or []
                if candidates:
                    top = candidates[0] or {}
                    logger.debug(
                        "下载按钮候选诊断: sig={} 数量={} 首选score={} 路径={}",
                        sig, len(candidates), top.get("score", ""), str(top.get("path", ""))[:140],
                    )
            case "download_click_post_diagnostics":
                sig = data.get("candidate_signature", "未知")
                diagnostics = data.get("diagnostics") or {}
                frames = diagnostics.get("frames") or []
                toasts = diagnostics.get("body_toast_sample") or []
                logger.debug(
                    "下载点击后诊断: sig={} 策略={} 可见frame={} 提示={}",
                    sig, data.get("click_strategy", ""), len(frames), str(toasts[:2])[:160],
                )
            case "stale_preview_close_diagnostics":
                sig = data.get("candidate_signature", "未知")
                logger.debug(
                    "旧简历预览关闭诊断: sig={} 关闭候选={}",
                    sig, data.get("close_candidate_count", 0),
                )
            case "stale_resume_preview_reused_for_current_candidate":
                sig = data.get("candidate_signature", "未知")
                source = data.get("preview_source", "")
                self._log("highlight", f"旧预览指纹未变化，但内容匹配当前候选人，继续下载: {sig}；来源={source}")
            case "boss_svg_download_link_captured":
                sig = data.get("candidate_signature", "未知")
                self._log("highlight", f"boss-svg 下载链路已捕获下载链接: {sig}")
            case "boss_svg_download_link_capture_failed":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"boss-svg 点击后未捕获下载链接: {sig}；原因={data.get('reason', '')}")
            case "resume_preview_candidate_confirm":
                sig = data.get("candidate_signature", "未知")
                component_type = str(data.get("component_preview_type", "") or "dom_text")
                self._log("highlight", f"疑似识别到弹窗: {sig}；类型={component_type}")
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
            case "manual_download_learning_required":
                sig = data.get("candidate_signature", "未知")
                self.runtime_state["paused"] = True
                self._log("highlight", "无法触发下载按钮，请你手动点击下载按钮供系统分析学习。")
            case "manual_download_recording_started":
                self._log("highlight", "正在记录你的操作……")
            case "manual_download_click_captured":
                sig = data.get("candidate_signature", "未知")
                tag = data.get("tag") or "?"
                descriptor = str(data.get("descriptor", "")).strip()
                x, y = data.get("x"), data.get("y")
                frame_src = str(data.get("frame_src") or "")
                where = "PDF iframe 内" if frame_src else "页面"
                self._log("highlight", f"已捕获{where}手动点击 - 候选人：{sig}；标签={tag}；位置=({x},{y})")
                if descriptor:
                    self._log("highlight", f"    descriptor：{descriptor[:240]}")
                if frame_src:
                    self._log("highlight", f"    iframe src：{frame_src[:240]}")
            case "manual_download_learning_success":
                sig = data.get("candidate_signature", "未知")
                x, y = data.get("x"), data.get("y")
                download_url = str(data.get("download_url") or "(未捕获)")
                self._log("highlight", f"━━━ 🟣 学习成功 - 候选人：{sig} ━━━")
                self._log("highlight", f"  坐标=({x},{y})；下载链接={download_url[:120]}")
                logger.debug(
                    "学习成功详情: sig={} tag={} id={} class={} aria={} title={} path={} descriptor={} frame_src={}",
                    sig,
                    data.get("tag") or "?",
                    data.get("id") or "(空)",
                    str(data.get("class_name", ""))[:240],
                    str(data.get("aria_label", ""))[:140],
                    str(data.get("title", ""))[:140],
                    str(data.get("path", ""))[:320],
                    str(data.get("descriptor", ""))[:300],
                    str(data.get("frame_src") or "")[:240],
                )
            case "manual_download_learning_failed":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"已捕获手动点击但未检测到下载完成: {sig}；原因={data.get('reason', '')}")
            case "manual_download_click_timeout":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"等待人工点击下载超时: {sig}")
            case "auto_download_click_used":
                sig = data.get("candidate_signature", "未知")
                path = str(data.get("path", ""))[:160]
                descriptor = str(data.get("descriptor", ""))[:120]
                self._log("info", f"自动点击简历下载按钮: {sig}；路径={path}；描述={descriptor}")
            case "learned_download_click_used":
                sig = data.get("candidate_signature", "未知")
                tag = data.get("tag") or "?"
                x, y = data.get("x"), data.get("y")
                self._log("info", f"复用学习记录: {sig}；标签={tag}；位置=({x},{y})")
                logger.debug(
                    "复用学习记录详情: sig={} id={} class={} path={} descriptor={} frame_src={}",
                    sig,
                    data.get("id") or "(空)",
                    str(data.get("class_name", ""))[:240],
                    str(data.get("path", ""))[:320],
                    str(data.get("descriptor", ""))[:300],
                    str(data.get("frame_src") or "")[:240],
                )
            case "learned_download_click_failed":
                sig = data.get("candidate_signature", "未知")
                reason = str(data.get("reason", "") or "未说明")
                self._log("error", f"已学习下载控件未找到: {sig}；原因={reason}")
            case "download_button_candidates":
                pass
            case "collect_progress":
                self.runtime_state["current_index"] = data.get("current_index", 0)
                self.runtime_state["scanned_count"] = data.get("scanned_count", self.runtime_state.get("scanned_count", 0))
            case "collect_finished":
                self.runtime_state["running"] = False
                self.runtime_state["paused"] = False
                total = self.runtime_state.get("downloaded_count", 0)
                final_status = "success" if not data.get("stopped") else "cancelled"
                self._finish_crawl_task(final_status)
                if data.get("learning_finished"):
                    self._log("highlight", "学习任务已完成，采集任务自动结束")
                else:
                    self._log("info", f"采集完成，共下载 {total} 份简历；本次新增去重 {self.runtime_state.get('dedup_record_count', 0)} 位")
                self.get_run_summary()
                self._on_task_finished(final_status)
            case "error":
                if self.runtime_state.get("running"):
                    self._finish_crawl_task("failed", error_message=str(data.get("message", "未知错误")))
                self.runtime_state["running"] = False
                self.runtime_state["paused"] = False
                self._log("error", f"扩展错误: {data.get('message', '未知错误')}")
                self._on_task_finished("failed")
            case "resume_persist_confirmed":
                sig = data.get("candidate_signature", "未知")
                strategy = data.get("strategy") or "?"
                file_name = data.get("file") or ""
                tail = f"；文件={file_name}" if file_name else ""
                self._log("info", f"持久化确认: {sig}；策略={strategy}{tail}")
            case "resume_persist_rejected":
                sig = data.get("candidate_signature", "未知")
                status = data.get("status") or "?"
                reason = data.get("reason") or ""
                bound = data.get("bound_signature") or ""
                strategy = data.get("strategy") or "?"
                bound_text = f"；冲突归属={bound}" if bound else ""
                self._log("warning", f"持久化未计入: {sig}；策略={strategy}；状态={status}{bound_text}；原因={reason}")
            case "stale_pdf_preview_frame_removed":
                sig = data.get("candidate_signature") or "?"
                owner = data.get("owner_signature") or "无归属"
                rid = data.get("resource_id") or "(无ID)"
                self._log("info", f"已强制移除旧 PDF iframe: 触发候选={sig}；资源ID={rid}；原归属={owner}")
            case "stale_pdf_preview_frame_remove_error":
                self._log("warning", f"强制移除旧 PDF iframe 失败: {data.get('error', '未知错误')}")
            case "pdf_iframe_preview_skipped_owned_by_other":
                sig = data.get("candidate_signature") or "?"
                owner = data.get("owner_signature") or "?"
                rid = data.get("resource_id") or "?"
                self._log("warning", f"跳过他人 iframe: 候选={sig}；资源ID={rid}；归属={owner}")
            case "pdf_iframe_resource_id_claimed":
                sig = data.get("candidate_signature") or "?"
                rid = data.get("resource_id") or "?"
                self._log("info", f"已绑定 iframe 资源ID: {sig} → {rid}")
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

    def _normalize_boss_candidate_signature(self, signature: str) -> str:
        parts = [part.strip() for part in str(signature or "").split("/")]
        while len(parts) < 3:
            parts.append("")
        name = self._normalize_resume_filename_part(parts[0], "待识别")
        age = self._normalize_resume_filename_part(parts[1], "待识别")
        education = self._normalize_resume_filename_part(parts[2], "待识别")
        return f"{name}/{age}/{education}"

    def _load_boss_candidate_signatures(self) -> set[str]:
        try:
            with create_session() as session:
                signatures = BossCandidateRecordService(session).list_candidate_signatures("boss")
            normalized_signatures = {self._normalize_boss_candidate_signature(signature) for signature in signatures if signature}
            return {signature for signature in normalized_signatures if signature and signature != "待识别/待识别/待识别"}
        except Exception as exc:
            self._log("warning", f"读取 BOSS 去重签名失败: {exc}")
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
        download_request_id = str(data.get("download_request_id", "") or "")

        if candidate_key and candidate_key in self._seen_candidate_records:
            self._log("info", f"BOSS 去重命中，已存在记录: {candidate_sig}")
            self._write_event_log("resume_saved_duplicate_skipped", {
                "signature": candidate_sig,
                "candidate_key": candidate_key,
                "info": candidate_info,
                "status": "duplicate_skipped",
                "at": datetime.now().isoformat(),
            })
            self._send_persist_ack(candidate_sig, candidate_info, "duplicate_skipped", download_request_id, "duplicate_in_run")
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
                bound_signature = self._saved_resume_hash_signatures.get(file_hash)
                if bound_signature and bound_signature != candidate_sig:
                    message = f"检测到疑似下载串档：相同简历内容已归属 {bound_signature}，本次却要保存为 {candidate_sig}，已拦截"
                    self._log("error", message)
                    self._write_event_log("resume_hash_mismatch_blocked", {
                        "signature": candidate_sig,
                        "bound_signature": bound_signature,
                        "candidate_key": candidate_key,
                        "download_path": download_path,
                        "filename": source_filename,
                        "hash": file_hash,
                        "status": "hash_mismatch_blocked",
                        "at": datetime.now().isoformat(),
                    })
                    self._send_persist_ack(
                        candidate_sig,
                        candidate_info,
                        "hash_mismatch_blocked",
                        download_request_id,
                        f"内容已归属 {bound_signature}",
                        {"bound_signature": bound_signature, "hash": file_hash},
                    )
                    return
                self._saved_resume_hash_signatures[file_hash] = candidate_sig
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
                    task_id=self.runtime_state.get("task_id"),
                )
                self._seen_candidate_records.add(candidate_key)
                if is_new_record:
                    self.runtime_state["dedup_record_count"] += 1
                    logger.debug("BOSS 去重记录已写入: {}", candidate_sig)
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
                logger.debug("简历已保存: {}", final_filename)
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
                self._send_persist_ack(
                    candidate_sig,
                    candidate_info,
                    "saved",
                    download_request_id,
                    "",
                    {"file": final_filename, "hash": file_hash},
                )
                return

        self._log("error", f"简历下载完成但未找到可归档文件，未计入下载: {candidate_sig}")
        self._write_event_log("resume_archive_missing", {
            "signature": candidate_sig,
            "candidate_key": candidate_key,
            "download_path": download_path,
            "filename": source_filename,
            "status": "archive_missing",
            "at": datetime.now().isoformat(),
        })
        self._send_persist_ack(candidate_sig, candidate_info, "archive_missing", download_request_id, "源文件未找到")


    def _translate_skip_reason(self, reason: str) -> str:
        if reason.startswith("button_disabled:"):
            state = reason.split(":", 1)[1] or "未知状态"
            return f"附件简历按钮不可用（{state}）"
        if reason.startswith("download_error:"):
            error = reason.split(":", 1)[1] or "未知错误"
            return f"下载失败（{error}）"
        mapping = {
            "no_resume_attachment": "无可下载附件简历",
            "need_request_resume": "需要索要简历，已按设置跳过",
            "boss_dedup_hit": "BOSS 去重命中，已有简历记录",
            "duplicate_in_run": "本轮已处理过相同候选人签名",
            "resume_requested_by_user": "已根据用户设置索要简历",
            "resume_request_unconfirmed": "已点击索要简历但未检测到请求发送成功",
            "resume_request_confirm_not_found": "未找到索要简历确认按钮，未检测到请求发送成功",
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
        record_key = str(data.get("candidate_key") or candidate_sig)
        is_duplicate_record = record_key in self._seen_skip_records

        self._log("info", f"跳过: {candidate_sig} ({reason_text})")
        if reason == "resume_request_already_sent":
            self._log("info", f"跳过已索要简历的候选人{candidate_sig}")
        elif reason == "no_resume_attachment":
            pass
        if reason == "boss_dedup_hit":
            self._log("info", f"BOSS 下载前去重命中，跳过附件识别: {candidate_sig}")
        self._write_event_log("candidate_skipped_seen", {
            "signature": candidate_sig,
            "reason": reason,
            "reason_text": reason_text,
            "duplicate_record": is_duplicate_record,
            "at": datetime.now().isoformat(),
        })

        if is_duplicate_record:
            return

        self._seen_skip_records.add(record_key)
        self.runtime_state["skipped_count"] += 1
        counts = dict(self.runtime_state.get("skip_reason_counts", {}))
        counts[reason] = counts.get(reason, 0) + 1
        self.runtime_state["skip_reason_counts"] = counts
        record = {
            "signature": candidate_sig,
            "status": "dedup_skipped" if reason == "boss_dedup_hit" else "skipped",
            "reason": reason,
            "reason_text": reason_text,
            "candidate_key": data.get("candidate_key", ""),
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
