"""Bridge between WebSocket events and Boss business logic."""

import asyncio
import json
import re
import shutil
import subprocess
import sys
import threading
from collections import Counter
from concurrent.futures import Future
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from loguru import logger

from recruitment_assistant.version import APP_VERSION
from recruitment_assistant.services.crawl_task_service import BossCandidateRecordService, CrawlTaskService
from recruitment_assistant.services.ws_server import BossWSServer
from recruitment_assistant.storage.db import create_session
from recruitment_assistant.storage.models import CrawlTask
from recruitment_assistant.utils.hash_utils import text_hash
from recruitment_assistant.utils.snapshot_utils import safe_filename

from recruitment_assistant.services.test_run_watchdog import WatchdogState
from recruitment_assistant.services.extension_contract import (
    EXPECTED_EXTENSION_VERSION as BOSS_EXTENSION_EXPECTED_VERSION,
    EXPECTED_CONTENT_SCRIPT_VERSION as BOSS_CONTENT_SCRIPT_EXPECTED_VERSION,
)


BOSS_BRIDGE_VERSION = "2.06.0"


class BossWSBridge:
    def __init__(self, ws_server: BossWSServer):
        self.ws_server = ws_server
        self.ws_server.on_event = self._handle_event
        self._event_seq = 0
        self._seen_candidate_records: set[str] = set()
        self._seen_skip_records: set[str] = set()
        self._recent_ui_log_keys: dict[str, float] = {}
        self._saved_resume_hash_signatures: dict[str, str] = {}
        self._talking_position_by_sig: dict[str, str] = {}
        self._collect_timer: threading.Timer | None = None
        self._watchdog: WatchdogState = WatchdogState()
        self._watchdog_task: "Future[None] | None" = None  # concurrent.futures.Future from run_coroutine_threadsafe
        self._watchdog_poll_interval: float = 5.0
        self._target_candidate_names: set[str] = set()

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
            "dedup_record_count_baseline": 0,
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
        self._talking_position_by_sig.clear()
        self._stop_watchdog_loop()
        self._watchdog.reset()
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
        # 初始化"本轮新增"基线为当前 DB 总数；UI 计算 added = 当前总数 - baseline
        self.runtime_state["dedup_record_count_baseline"] = self._load_boss_dedup_record_count()
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

    def discard_dedup_keys(self, keys: set[str]) -> None:
        self._seen_candidate_records -= keys

    def set_target_candidates(self, names: set[str]) -> None:
        self._target_candidate_names = set(names)
        if names:
            self._log("info", f"已设置目标候选人白名单（{len(names)}人）：{'、'.join(sorted(names))}；非目标候选人将被跳过")

    def clear_target_candidates(self) -> None:
        self._target_candidate_names.clear()

    def _is_candidate_in_target(self, candidate_sig: str, candidate_info: dict) -> bool:
        if not self._target_candidate_names:
            return True
        name = (candidate_info.get("name") or "").strip()
        if name and any(t in name or name in t for t in self._target_candidate_names):
            return True
        sig_name = (candidate_sig or "").split("/")[0].strip()
        if sig_name and any(t in sig_name or sig_name in t for t in self._target_candidate_names):
            return True
        return False

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

    def _log_task_initialization(self, config: dict, collect_mode: str, collect_minutes: int) -> None:
        """采集开始时输出任务初始化信息块，便于事后追溯任务上下文。"""
        try:
            now = datetime.now()
            task_id = self.runtime_state.get("task_id")
            run_id = self.runtime_state.get("run_id", "")
            max_resumes = int(config.get("max_resumes") or 0)
            interval_ms = config.get("interval_ms", config.get("scan_interval_ms", ""))
            request_resume = bool(config.get("request_resume_if_missing"))
            test_mode = config.get("test_mode")
            test_mode_label = test_mode if test_mode not in (None, "", False) else "正式"
            key_count = len(config.get("boss_candidate_keys") or [])
            signature_count = len(config.get("boss_candidate_signatures") or [])
            ext_version = self.runtime_state.get("extension_version") or "未连接"

            if collect_mode == "按时间采集":
                target_text = f"按时间采集 {collect_minutes} 分钟"
            else:
                target_text = f"{max_resumes} 份简历" if max_resumes else "未指定"

            interval_text = f"{interval_ms}ms" if interval_ms not in ("", None) else "默认"
            task_label = f"#{task_id}" if task_id else "未创建"

            self._log("highlight", f"━━━ 任务初始化 {task_label} ━━━")
            self._log("highlight", f"执行日期：{now.strftime('%Y-%m-%d %H:%M:%S')}")
            self._log("highlight", f"运行 ID：{run_id}")
            self._log("highlight", f"任务目标：{target_text}（test_mode={test_mode_label}）")
            self._log("highlight", f"配置：request_resume_if_missing={request_resume}；扫描间隔={interval_text}")
            self._log("highlight", f"去重基线：{key_count} 条 key / {signature_count} 条签名")
            self._log("highlight", f"版本：页面 {APP_VERSION} / 桥接 {BOSS_BRIDGE_VERSION} / 扩展 {BOSS_EXTENSION_EXPECTED_VERSION} / 脚本 {BOSS_CONTENT_SCRIPT_EXPECTED_VERSION}")
            self._log("highlight", f"当前会话：扩展已连接 {ext_version}")
            self._log("highlight", "━━━━━━━━━━━━━━━━━━━━")

            self._write_event_log("boss_task_initialization_logged", {
                "task_id": task_id,
                "run_id": run_id,
                "max_resumes": max_resumes,
                "collect_mode": collect_mode,
                "collect_minutes": collect_minutes,
                "interval_ms": interval_ms,
                "request_resume_if_missing": request_resume,
                "test_mode": test_mode_label,
                "key_count": key_count,
                "signature_count": signature_count,
                "extension_version": ext_version,
            })
        except Exception as exc:
            self._log("warning", f"输出任务初始化信息块失败：{exc}")

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
        self._stop_watchdog_loop()
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
                self._log("warning", f"⚠ Chrome 下载目录有 {len(missed_files)} 个未归档的 BOSS PDF：")
                shown = missed_files[:10]
                for idx, (path, size) in enumerate(shown, 1):
                    kb = size / 1024 if size else 0
                    branch = "└─" if idx == len(shown) and len(missed_files) <= 10 else "├─"
                    self._log("warning", f"  {branch} {path.name}（{kb:.1f} KB）")
                if len(missed_files) > 10:
                    self._log("warning", f"  └─ 另有 {len(missed_files) - 10} 个未列出，详见运行日志 jsonl")
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
            target = int(self.runtime_state.get("task_planned_count") or 0)
            achievement_pct = round(downloaded / target * 100, 1) if target else 0.0
            avg_per_download = (elapsed_sec / downloaded) if downloaded else 0.0
            skip_reason_counts = dict(self.runtime_state.get("skip_reason_counts", {}) or {})

            # 跳过分布按业务语义合并
            skip_groups_def: list[tuple[str, list[str]]] = [
                ("去重命中", ["boss_dedup_hit", "duplicate_in_run"]),
                ("待候选人上传", ["resume_request_already_sent", "resume_requested_by_user"]),
                ("索要未确认", ["resume_request_unconfirmed"]),
                ("无附件且未索要", ["no_resume_attachment"]),
                ("未识别", ["candidate_info_unrecognized"]),
            ]
            failure_keys = {"download_failed", "download_button_not_found", "resume_preview_not_found", "resume_attachment_click_guarded"}
            skip_groups: dict[str, int] = {}
            consumed: set[str] = set()
            for label, keys in skip_groups_def:
                total_in_group = sum(int(skip_reason_counts.get(k, 0)) for k in keys)
                if total_in_group:
                    skip_groups[label] = total_in_group
                consumed.update(keys)
            other_skip = sum(int(v) for k, v in skip_reason_counts.items() if k not in consumed and k not in failure_keys)
            if other_skip:
                skip_groups["其他"] = other_skip

            candidates = self.runtime_state.get("candidates", []) or []
            failure_counts: Counter[str] = Counter()
            for c in candidates:
                if c.get("status") in {"failed", "error", "download_failed"}:
                    failure_counts[str(c.get("reason") or c.get("status") or "unknown")] += 1
            # 把跳过维度里属于 failure 的项也并入失败分布
            for k in failure_keys:
                v = int(skip_reason_counts.get(k, 0))
                if v:
                    failure_counts[k] += v

            metrics = {
                "run_id": run_id,
                "status": status,
                "finish_reason": self.runtime_state.get("finish_reason", ""),
                "elapsed_sec": round(elapsed_sec, 1),
                "downloaded": downloaded,
                "target": target,
                "achievement_pct": achievement_pct,
                "skipped": skipped,
                "avg_per_download_sec": round(avg_per_download, 1),
                "skip_reason_counts": skip_reason_counts,
                "skip_groups": skip_groups,
                "failure_counts": dict(failure_counts),
                "chrome_download_missed": len(missed_files),
            }
            self._write_event_log("run_metrics_summary", metrics)

            self._log("highlight", "━━━ 本轮采集指标 ━━━")
            finish_reason = self.runtime_state.get("finish_reason", "")
            if finish_reason:
                self._log("highlight", f"结束原因：{finish_reason}")
            if target:
                self._log("highlight", f"总耗时={int(elapsed_sec)}s；下载={downloaded}/{target}（达成率 {achievement_pct}%；avg {avg_per_download:.1f}s/份）；跳过={skipped}")
            else:
                self._log("highlight", f"总耗时={int(elapsed_sec)}s；下载={downloaded} (avg {avg_per_download:.1f}s/份)；跳过={skipped}")
            if skip_groups:
                top = "；".join(f"{k}={v}" for k, v in sorted(skip_groups.items(), key=lambda kv: -kv[1]))
                self._log("highlight", f"跳过分布：{top}")
            if failure_counts:
                top = "；".join(f"{k}={v}" for k, v in failure_counts.most_common(5))
                self._log("highlight", f"失败分布：{top}")
            else:
                self._log("highlight", "失败分布：无")

            # 仅在未达目标时给出针对性提示
            if status == "partial" and skipped:
                dedup_share = skip_groups.get("去重命中", 0) / max(skipped, 1)
                waiting_share = skip_groups.get("待候选人上传", 0) / max(skipped, 1)
                if dedup_share >= 0.5:
                    self._log("warning", "提示：超半数跳过来自去重命中；当前列表的可用候选人已不足以覆盖目标，建议刷新聊天列表或扩大筛选范围")
                elif waiting_share >= 0.5:
                    self._log("warning", "提示：超半数跳过来自'已索要等上传'；建议等待若干小时后再来采集")
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
        # 任务开始时间保底：先用当前时间，后续 _create_crawl_task 成功后会覆盖为 DB 任务 started_at
        self.runtime_state["task_started_at"] = datetime.now().isoformat(timespec="seconds")
        self.runtime_state["task_planned_count"] = 0
        self.runtime_state["task_status"] = "pending"
        # 本轮新增基线 = 启动时 DB 总记录数；UI 计算 added = current - baseline
        self.runtime_state["dedup_record_count_baseline"] = self._load_boss_dedup_record_count()
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
        if self._target_candidate_names:
            config["target_candidate_names"] = sorted(self._target_candidate_names)
        self._create_crawl_task(config)
        self._log_task_initialization(config, collect_mode, collect_minutes)
        command = {"type": "start_collect", "config": config, "run_id": self.runtime_state.get("run_id", "")}
        self._start_watchdog_loop()
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
        self._stop_watchdog_loop()

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
        # 链路诊断保留进 JSONL，UI 不再打扰
        logger.debug(
            "持久化 ack 已下发: sig={} 状态={} request_id={}",
            candidate_sig, status, download_request_id or "(空)",
        )

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
            "boss_talking_position",
            "boss_talking_position_skip",
            "dom_text_download_url_scan_started",
            "dom_text_download_url_not_found",
            "boss_svg_download_icon_scan_started",
            "boss_svg_download_icon_not_found",
            "download_button_candidates_detailed",
            "boss_diag",
            "boss_cooldown_start",
            "boss_cooldown_end",
            "stale_pdf_preview_frame_removed",
            "pdf_iframe_resource_id_claimed",
            "persist_completion_credited",
        }
        if event_type not in noisy_events:
            self._write_event_log("extension_event", {"type": event_type, "data": data})

        candidate_id = str(data.get("candidate_id") or "")
        misroute_events = self._watchdog.on_event(event_type, candidate_id, data)
        for me in misroute_events:
            self._log("error", f"⚠️ 学习模式误进 [{me['kind']}]：{me.get('candidate_signature', '?')} — {me['note']}")
            self._write_event_log("learning_misroute_detected", me)

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
                key_hit = bool(data.get("key_hit"))
                signature_hit = bool(data.get("signature_hit"))
                elapsed = data.get("elapsed_ms")
                hit = key_hit or signature_hit
                # 命中走 _record_skip → "跳过: X (BOSS 去重命中...)" 已覆盖；未命中则候选人会进入下一步处理，自然有后续日志。
                # 这一行去重检查本身降为 debug，避免每位候选人额外占一行 UI。
                logger.debug("BOSS 下载前去重检查: sig={} 结果={} 耗时={}ms", sig, "命中" if hit else "未命中", elapsed)
            case "boss_resume_button_lookup_started":
                pass
            case "extension_connected":
                version = data.get("version", "")
                self.runtime_state["extension_connected"] = True
                self.runtime_state["extension_version"] = version
                self.runtime_state["last_disconnect_reason"] = ""
                last_state = self.runtime_state.get("_last_connection_log_state")
                if last_state != "connected":
                    self._log("info", f"扩展已连接 v{version or '?'}；期望版本={BOSS_EXTENSION_EXPECTED_VERSION}")
                    self.runtime_state["_last_connection_log_state"] = "connected"
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
                self.runtime_state["last_disconnect_reason"] = str(data.get("reason", "unknown") or "unknown")
                was_running = bool(self.runtime_state.get("running"))
                self.runtime_state["running"] = False
                self.runtime_state["paused"] = False
                if was_running:
                    self._finish_crawl_task("failed", error_message=f"扩展连接已断开: {data.get('reason', 'unknown')}")
                last_state = self.runtime_state.get("_last_connection_log_state")
                if last_state != "disconnected":
                    self._log("error", f"扩展连接已断开: {data.get('reason', 'unknown')}")
                    self.runtime_state["_last_connection_log_state"] = "disconnected"
            case "settings_precheck_failed":
                reason = data.get("reason", "unknown")
                hint = data.get("hint", "")
                popups_setting = data.get("popups_setting", "")
                self._log("error", f"采集前置检查失败：{reason}（popups={popups_setting}）。{hint}")
                self.runtime_state["running"] = False
                self.runtime_state["paused"] = False
                self._finish_crawl_task("failed", error_message=f"settings_precheck_failed: {reason}")
            case "download_prompt_suspected":
                hint = data.get("hint", "")
                waited = data.get("waited_ms", 0)
                self._log("error", f"疑似下载前询问保存位置（已等 {waited}ms 未落盘）。{hint}")

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
            case "boss_talking_position":
                sig = data.get("candidate_signature", "?")
                raw = (data.get("raw") or "").strip()
                simplified = (data.get("simplified") or "").strip()
                if sig and simplified:
                    self._talking_position_by_sig[sig] = simplified
                logger.debug("沟通职位: sig={} 原文={} 简化={}", sig, raw, simplified)
            case "boss_talking_position_skip":
                sig = data.get("candidate_signature", "?")
                reason = data.get("reason", "?")
                self._log("warning", f"沟通职位未提取到: {sig}；reason={reason}")
                logger.debug("沟通职位未找到: sig={} reason={}", sig, reason)
            case "resume_downloaded":
                self._save_resume(data)
            case "candidate_skipped":
                self._record_skip(data)
            case "candidate_processing_timeout":
                sig = data.get("candidate_signature", "未知")
                timeout_ms = data.get("timeout_ms", 60000)
                self._log("error", f"候选人处理超时（{timeout_ms/1000:.0f}s），已强制跳过: {sig}")
            case "candidate_processing_error":
                sig = data.get("candidate_signature", "未知")
                msg = data.get("message", "未知错误")
                self._log("error", f"候选人处理异常，已跳过: {sig}；错误={msg[:200]}")
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
            case "boss_works_detection_start":
                sig = data.get("candidate_signature", "未知")
                imm = data.get("btn_found_immediate", False)
                total = data.get("total_card_btns", 0)
                texts = data.get("card_btn_texts", [])
                self._log("info", f"作品集探测开始: {sig}；立即命中={imm}；card-btn数={total}；文本={texts}")
            case "boss_works_scroll_attempt":
                sig = data.get("candidate_signature", "未知")
                found = data.get("scroll_container_found", False)
                cls = data.get("scroll_class", "")[:60]
                sh = data.get("scroll_height", 0)
                ch = data.get("client_height", 0)
                self._log("info", f"作品集滚动查找: {sig}；容器找到={found}；class={cls}；scrollH={sh}；clientH={ch}")
            case "boss_attachment_works_button_found":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"探测到预览作品集按钮: {sig}")
            case "boss_attachment_works_found":
                sig = data.get("candidate_signature", "未知")
                fn = data.get("filename", "")
                self._log("info", f"作品集URL已提取: {sig}；文件名={fn}")
            case "boss_attachment_works_downloaded":
                sig = data.get("candidate_signature", "未知")
                fn = data.get("filename", "")
                self._log("success", f"作品集下载成功: {sig}；文件名={fn}")
            case "boss_attachment_works_skipped":
                sig = data.get("candidate_signature", "未知")
                reason = data.get("reason", "")
                self._log("warning", f"作品集跳过: {sig}；原因={reason}")
            case "boss_multi_attachment_scan":
                sig = data.get("candidate_signature", "未知")
                extracted = data.get("extracted", [])
                labels = [f"{e.get('filename', '?')}({'作品' if e.get('isWorks') else '简历' if e.get('isResume') else '附件'})" for e in extracted]
                self._log("highlight", f"多附件扫描: {sig}；发现 {len(extracted)} 个附件：{'、'.join(labels)}")
            case "boss_multi_attachment_downloading":
                sig = data.get("candidate_signature", "未知")
                fn = data.get("filename", "")
                label = data.get("typeLabel", "")
                self._log("info", f"多附件下载中: {sig}；{label}；文件名={fn}")
            case "boss_multi_attachment_downloaded":
                sig = data.get("candidate_signature", "未知")
                fn = data.get("filename", "")
                label = data.get("typeLabel", "")
                self._log("success", f"多附件下载成功: {sig}；{label}；文件名={fn}")
            case "boss_multi_attachment_failed":
                sig = data.get("candidate_signature", "未知")
                fn = data.get("filename", "")
                reason = data.get("reason", "")
                self._log("warning", f"多附件下载失败: {sig}；文件名={fn}；原因={reason}")
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
            case "resume_consent_found":
                sig = data.get("candidate_signature", "未知")
                consent_tag = data.get("consent_tag", "?")
                consent_cls = data.get("consent_class", "")
                consent_rect = data.get("consent_rect", {})
                consent_visible = data.get("consent_visible")
                consent_disabled = data.get("consent_disabled")
                click_method = data.get("click_method", "?")
                resume_before = data.get("resume_btn_before", {})
                self._log("highlight", f"检测到「同意」按钮: {sig}；tag={consent_tag}；class={consent_cls[:80]}；rect={consent_rect}；visible={consent_visible}；disabled={consent_disabled}；click={click_method}")
                self._log("info", f"同意前简历按钮状态: state={resume_before.get('state')}；text={resume_before.get('text')}；opacity_chain={resume_before.get('opacity_chain')}；descriptor={resume_before.get('descriptor', '')[:100]}")
            case "resume_consent_clicked":
                sig = data.get("candidate_signature", "未知")
                self._log("info", f"已点击「同意」按钮: {sig}，开始轮询等待简历按钮变亮...")
            case "resume_consent_vue_click_result":
                sig = data.get("candidate_signature", "未知")
                bg = data.get("bg_result", {})
                cx = data.get("click_x", "?")
                cy = data.get("click_y", "?")
                self._log("highlight", f"CDP真实点击结果: {sig}；ok={bg.get('ok')}；method={bg.get('method', '')}；error={bg.get('error', '')}；坐标=({cx},{cy})")
            case "resume_consent_cdp_fallback":
                sig = data.get("candidate_signature", "未知")
                bg = data.get("bg_result", {})
                cx = data.get("click_x", "?")
                cy = data.get("click_y", "?")
                self._log("highlight", f"同意按钮CDP兜底点击: {sig}；ok={bg.get('ok')}；error={bg.get('error', '')}；坐标=({cx},{cy})")
            case "resume_consent_force_click_done":
                sig = data.get("candidate_signature", "未知")
                cls_after = data.get("class_after_remove", "")
                still_in_dom = data.get("consent_still_in_dom")
                self._log("highlight", f"强制点击完成: {sig}；移除disabled后class={cls_after[:60]}；按钮仍在DOM={still_in_dom}")
            case "resume_consent_poll":
                sig = data.get("candidate_signature", "未知")
                idx = data.get("poll_index", "?")
                btn_found = data.get("resume_btn_found")
                btn_state = data.get("resume_btn_state")
                btn_text = data.get("resume_btn_text")
                opacity = data.get("resume_btn_opacity_chain", [])
                btn_disabled = data.get("resume_btn_disabled")
                btn_cls = data.get("resume_btn_class", "")
                consent_still = data.get("consent_btn_still_present")
                self._log("info", f"同意后轮询 #{idx}: btn_found={btn_found}；state={btn_state}；text={btn_text}；opacity={opacity}；disabled={btn_disabled}；class={btn_cls[:60]}；同意按钮仍在={consent_still}")
            case "resume_consent_accepted":
                sig = data.get("candidate_signature", "未知")
                self._log("success", f"「同意」按钮生效，简历按钮已激活: {sig}")
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
                preview_source = data.get("preview_source", "")
                self._log("info", f"开始尝试下载: {sig}；预览类型={preview_source}")
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
            case "dom_text_download_url_scan_started":
                sig = data.get("candidate_signature", "未知")
                logger.debug("扫描 dom_text 弹窗下载链接: sig={}", sig)
            case "dom_text_download_url_found":
                sig = data.get("candidate_signature", "未知")
                source = data.get("source", "")
                self._log("highlight", f"dom_text 弹窗发现下载链接: {sig}；来源={source}")
            case "dom_text_download_url_not_found":
                sig = data.get("candidate_signature", "未知")
                logger.debug("dom_text 弹窗未发现下载链接: sig={} 锚={} vue={}", sig, data.get("anchor_count", 0), data.get("vue_element_count", 0))
            case "dom_text_direct_download_failed":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"dom_text 直接下载失败: {sig}；原因={data.get('reason', '')}")
            case "all_download_strategies_exhausted":
                sig = data.get("candidate_signature", "未知")
                self._log("error", f"所有下载策略均未成功: {sig}；预览类型={data.get('preview_source', '')}")
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
                logger.debug("扫描 boss-svg 下载组件: sig={}", sig)
            case "boss_svg_download_icon_found":
                sig = data.get("candidate_signature", "未知")
                path = str(data.get("component_path", ""))[:160]
                self._log("highlight", f"命中 boss-svg 下载组件: {sig}；路径={path}")
            case "boss_svg_download_icon_not_found":
                sig = data.get("candidate_signature", "未知")
                logger.debug("boss-svg 下载组件未命中，尝试其他策略: sig={}", sig)
            case "boss_svg_download_icon_clicked":
                sig = data.get("candidate_signature", "未知")
                self._log("highlight", f"已点击 boss-svg 下载组件，等待 Chrome 下载事件: {sig}")
            case "download_button_candidates_detailed":
                sig = data.get("candidate_signature", "未知")
                candidates = data.get("candidates") or []
                if candidates:
                    top = candidates[0] or {}
                    logger.debug("下载按钮候选: sig={} 数量={} 首选score={} 描述={}", sig, len(candidates), top.get("score", ""), str(top.get("text", ""))[:100])
                else:
                    logger.debug("下载按钮候选: sig={} 数量=0", sig)
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
            case "boss_scroll_phase_enter":
                self._log("info", f"进入滚动加载阶段: 已完成={data.get('completed')}/{data.get('target')}，已处理={data.get('processed_count')}人，文本指纹={data.get('processed_texts_size')}条，state={data.get('state')}")
            case "boss_list_scroll_exhausted":
                total = data.get("total_items", "?")
                retries = data.get("retries", "?")
                self._log("warning", f"候选人列表滚动已耗尽: 可见元素={total}，重试={retries}次；已处理={data.get('processed_count', '?')}人，文本指纹={data.get('processed_texts_size', '?')}条")
            case "collect_finished":
                self.runtime_state["running"] = False
                self.runtime_state["paused"] = False
                total = self.runtime_state.get("downloaded_count", 0)
                target = int(self.runtime_state.get("task_planned_count") or 0)
                stopped = bool(data.get("stopped"))
                target_met = bool(target) and total >= target
                if stopped:
                    final_status = "cancelled"
                    reason_text = "用户手动停止"
                elif target_met or not target:
                    final_status = "success"
                    reason_text = "已达成目标"
                else:
                    final_status = "partial"
                    reason_text = "候选人列表已逛完，未达目标"
                self.runtime_state["finish_reason"] = reason_text
                self.runtime_state["finish_status"] = final_status
                self._finish_crawl_task(final_status)
                if data.get("learning_finished"):
                    self._log("highlight", "学习任务已完成，采集任务自动结束")
                else:
                    if target:
                        pct = round(total / target * 100, 1)
                        self._log("info", f"采集结束：{reason_text}（{total}/{target}，达成率 {pct}%）；本次新增去重 {self.runtime_state.get('dedup_record_count', 0)} 位")
                    else:
                        self._log("info", f"采集结束：{reason_text}；共下载 {total} 份简历；本次新增去重 {self.runtime_state.get('dedup_record_count', 0)} 位")
                self.get_run_summary()
                self._on_task_finished(final_status)
                self._spawn_analyze_test_run()
            case "error":
                diag = data.get("diag")
                diag_suffix = ""
                if diag:
                    containers = diag.get("containers", [])
                    candidates = diag.get("candidates", [])
                    diag_suffix = f"；诊断: 容器匹配={containers}，候选人匹配={candidates}，沟通中标签={diag.get('chattingTab')}"
                if self.runtime_state.get("running"):
                    self._finish_crawl_task("failed", error_message=str(data.get("message", "未知错误")))
                self.runtime_state["running"] = False
                self.runtime_state["paused"] = False
                self._log("error", f"扩展错误: {data.get('message', '未知错误')}{diag_suffix}")
                self._on_task_finished("failed")
            case "boss_diag":
                step = data.get("step", "")
                if step == "chatting_tab":
                    logger.debug("诊断: 沟通中标签点击结果={} URL={}", data.get("result"), data.get("url", ""))
                elif step == "retry_scan":
                    logger.debug("诊断: 重试扫描 #{} 找到候选人={}", data.get("retry"), data.get("found", 0))
                elif step == "dom_snapshot_on_fail":
                    snapshot = data.get("snapshot", {})
                    self._save_dom_snapshot(snapshot)
                    sel_counts = snapshot.get("selector_counts", {})
                    self._log("warning", f"DOM诊断快照已保存: 选择器命中={sel_counts}")
                elif step == "talking_position_fallback_hits":
                    hits = data.get("hits", [])
                    texts = [h.get("text", "")[:40] for h in hits[:3]]
                    self._log("warning", f"沟通职位关键词在扩大范围找到但未通过正则: {texts}")
                elif step == "talking_position_no_keyword":
                    self._log("warning", f"沟通职位关键词未在右侧面板找到; rightLeft={data.get('rightLeft')}")
                else:
                    logger.debug("诊断: {}", data)
            case "resume_persist_confirmed":
                sig = data.get("candidate_signature", "未知")
                strategy = data.get("strategy") or "?"
                file_name = data.get("file") or ""
                # 链路确认；"文件下载成功并保存归档"已在 success 级输出，UI 不再重复
                logger.debug("持久化确认: sig={} 策略={} 文件={}", sig, strategy, file_name)
            case "boss_cooldown_start":
                completed = data.get("completed", 0)
                wait_sec = data.get("wait_sec", "?")
                rng = data.get("range", "?")
                rd = data.get("round", 0)
                self._log("info", f"安全缓冲: 已下载 {completed} 份，第 {rd} 轮等待 {wait_sec}s（区间 {rng}）")
            case "boss_cooldown_end":
                completed = data.get("completed", 0)
                self._log("info", f"安全缓冲结束，继续采集（已完成 {completed} 份）")
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
                logger.debug("已强制移除旧 PDF iframe: 触发候选={} 资源ID={} 原归属={}", sig, rid, owner)
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
                logger.debug("已绑定 iframe 资源ID: {} → {}", sig, rid)
            case _:
                logger.debug("未处理的扩展事件: {}", event_type)

    def _normalize_resume_filename_part(self, value: str | None, fallback: str) -> str:
        text = "".join(str(value or "").split()).strip("-—_｜|/\\:：,，;；.。()（）[]【】")
        if not text or text == "待识别":
            text = fallback
        return safe_filename(text, max_length=24)

    @staticmethod
    def _simplify_talking_position(raw: str) -> str:
        if not raw:
            return ""
        s = re.sub(r"[（(][^）)]*[）)]", "", raw)
        s = re.split(r"[/／]", s, maxsplit=1)[0]
        s = s.strip()
        if len(s) > 12:
            s = s[:12]
        return s

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

    def _load_boss_dedup_record_count(self) -> int:
        try:
            with create_session() as session:
                return BossCandidateRecordService(session).count_records("boss")
        except Exception as exc:
            self._log("warning", f"读取 BOSS 去重记录总数失败: {exc}")
            return 0

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
        talking_position_raw = (
            candidate_info.get("talking_position")
            or self._talking_position_by_sig.get(candidate_sig)
            or ""
        ).strip()
        talking_position = self._simplify_talking_position(talking_position_raw) or None

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
                talking_position=talking_position,
                phone=phone,
                resume_file_name=file_name,
                source_url=source_url,
                content_hash=content_hash,
                raw_resume_id=raw_resume_id,
                task_id=task_id,
            )

    def _save_resume(self, data: dict) -> None:
        variant = data.get("variant") or ""
        if variant == "attachment_works":
            self._save_attachment_works(data)
            return
        skip_dedup = False
        if variant == "attachment_resume_extra":
            self._log("info", f"多附件补充下载（简历）: {data.get('candidate_signature', '未知')}")
            skip_dedup = True
        if variant == "attachment_extra":
            self._log("info", f"多附件补充下载（附件）: {data.get('candidate_signature', '未知')}")
            skip_dedup = True

        candidate_sig = data.get("candidate_signature", "未知")
        candidate_info = data.get("candidate_info", {})
        source_filename = data.get("filename", "")
        download_path = data.get("download_path", "")
        source_url = str(data.get("url", "") or data.get("direct_url", "") or "") or None
        candidate_key = self._build_boss_candidate_key(candidate_sig, candidate_info)
        download_request_id = str(data.get("download_request_id", "") or "")

        if not self._is_candidate_in_target(candidate_sig, candidate_info):
            self._log("info", f"非目标候选人，跳过: {candidate_sig}")
            if download_path:
                try:
                    Path(download_path).unlink(missing_ok=True)
                except OSError:
                    pass
            self._send_persist_ack(candidate_sig, candidate_info, "target_filter_skipped", download_request_id, "不在目标白名单中")
            self.runtime_state["skipped_count"] = int(self.runtime_state.get("skipped_count") or 0) + 1
            counts = self.runtime_state.setdefault("skip_reason_counts", {})
            counts["target_filter"] = int(counts.get("target_filter") or 0) + 1
            return

        if candidate_key and candidate_key in self._seen_candidate_records and not skip_dedup:
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
                talking_position_raw = (candidate_info.get("talking_position") or self._talking_position_by_sig.get(candidate_sig) or "").strip()
                simplified_position = self._simplify_talking_position(talking_position_raw)
                position_part = self._normalize_resume_filename_part(simplified_position, simplified_position) if simplified_position else ""
                seq = self.runtime_state["downloaded_count"] + 1
                stem_parts = [name, age, education]
                if position_part:
                    stem_parts.append(position_part)
                stem_parts.extend(["BOSS直聘", now.strftime("%Y%m%d"), now.strftime("%H%M%S"), f"{seq:03d}"])
                filename_stem = "-".join(stem_parts)
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
                self._log("success", f"文件下载成功并保存归档: {final_filename}")

                file_size_bytes = len(content)
                file_size_mb = file_size_bytes / (1024 * 1024)
                size_warning = file_size_mb > 10
                works_keywords = ("作品集", "作品", "portfolio", "works")
                combined_names = f"{source_filename} {download_path}".lower()
                filename_warning = any(kw in combined_names for kw in works_keywords)
                if filename_warning:
                    self._log("warning", f"疑似作品集被当作简历下载: {candidate_sig}；原始文件名含作品集关键词；file={source_filename}")
                elif size_warning:
                    self._log("warning", f"下载文件偏大（{file_size_mb:.1f} MB），可能是作品集而非简历: {candidate_sig}")
                if (size_warning or filename_warning) and candidate_key:
                    works_dedup_key = f"{candidate_key}::works"
                    self._seen_candidate_records.add(works_dedup_key)

                record = {
                    "signature": candidate_sig,
                    "info": candidate_info,
                    "file": final_filename,
                    "path": str(target),
                    "hash": file_hash,
                    "status": "downloaded",
                    "candidate_key": candidate_key,
                    "at": now.isoformat(),
                    "file_size_bytes": file_size_bytes,
                    "size_warning": size_warning,
                    "filename_warning": filename_warning,
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

    def _save_attachment_works(self, data: dict) -> None:
        candidate_sig = data.get("candidate_signature", "未知")
        candidate_info = data.get("candidate_info", {}) or {}
        source_filename = data.get("filename", "")
        download_path = data.get("download_path", "")
        download_request_id = str(data.get("download_request_id", "") or "")

        if not self._is_candidate_in_target(candidate_sig, candidate_info):
            self._log("info", f"非目标候选人作品，跳过: {candidate_sig}")
            if download_path:
                try:
                    Path(download_path).unlink(missing_ok=True)
                except OSError:
                    pass
            self._send_persist_ack(candidate_sig, candidate_info, "target_filter_skipped", download_request_id, "不在目标白名单中")
            return

        candidate_key = self._build_boss_candidate_key(candidate_sig, candidate_info)
        works_dedup_key = f"{candidate_key}::works" if candidate_key else ""

        if works_dedup_key and works_dedup_key in self._seen_candidate_records:
            self._log("info", f"附件作品去重命中，已存在记录: {candidate_sig}")
            self._send_persist_ack(candidate_sig, candidate_info, "works_duplicate_skipped", download_request_id, "works_duplicate_in_run")
            return

        if not download_path:
            self._log("warning", f"附件作品下载完成但未带文件路径，跳过: {candidate_sig}")
            self._send_persist_ack(candidate_sig, candidate_info, "works_archive_missing", download_request_id, "缺少 download_path")
            return

        source = Path(download_path)
        if not source.exists():
            self._log("warning", f"附件作品源文件未找到，跳过: {candidate_sig}；path={download_path}")
            self._send_persist_ack(candidate_sig, candidate_info, "works_archive_missing", download_request_id, "源文件未找到")
            return

        now = datetime.now()
        project_root = Path(__file__).resolve().parents[2]
        target_dir = project_root / "data" / "attachments" / "boss" / now.strftime("%Y%m%d")
        target_dir.mkdir(parents=True, exist_ok=True)

        content = source.read_bytes()
        file_hash = sha256(content).hexdigest()
        bound_signature = self._saved_resume_hash_signatures.get(file_hash)
        if bound_signature and bound_signature != candidate_sig:
            self._log(
                "warning",
                f"附件作品 hash 已归属 {bound_signature}，本次为 {candidate_sig}，已拦截",
            )
            self._send_persist_ack(
                candidate_sig,
                candidate_info,
                "works_hash_mismatch_blocked",
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
        talking_position_raw = (
            candidate_info.get("talking_position")
            or self._talking_position_by_sig.get(candidate_sig)
            or candidate_info.get("job_title")
            or ""
        ).strip()
        simplified_position = self._simplify_talking_position(talking_position_raw)
        position_part = self._normalize_resume_filename_part(simplified_position, simplified_position) if simplified_position else ""

        seq = self.runtime_state["downloaded_count"]
        stem_parts = [name, age, education]
        if position_part:
            stem_parts.append(position_part)
        stem_parts.extend(["BOSS直聘", "（附件作品）", now.strftime("%Y%m%d"), now.strftime("%H%M%S"), f"{seq:03d}"])
        filename_stem = "-".join(stem_parts)
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
                self._log("warning", f"附件作品归档后删除源文件失败: {source}；原因={exc}")

        if works_dedup_key:
            self._seen_candidate_records.add(works_dedup_key)

        self._log("success", f"附件作品已归档: {target.name}")
        self._write_event_log("attachment_works_saved", {
            "signature": candidate_sig,
            "candidate_key": candidate_key,
            "file": target.name,
            "path": str(target),
            "hash": file_hash,
            "status": "works_saved",
            "at": now.isoformat(),
        })
        self._send_persist_ack(
            candidate_sig,
            candidate_info,
            "works_saved",
            download_request_id,
            "",
            {"file": target.name, "hash": file_hash, "variant": "attachment_works"},
        )

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
            "download_failed_force_skip": "下载多次失败，已强行跳过",
            "resume_consent_disabled": "对方已撤回简历（同意按钮不可点击），已跳过",
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
            # "跳过: X (BOSS 去重命中, 已有简历记录)" 已由 _record_skip 输出，UI 无需再追打
            logger.debug("BOSS 下载前去重命中，跳过附件识别: {}", candidate_sig)
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

    def _save_dom_snapshot(self, snapshot: dict) -> None:
        """采集失败时保存 DOM 快照到诊断文件，用于分析选择器失效原因。"""
        try:
            from recruitment_assistant.config.settings import get_settings
            diag_dir = get_settings().attachment_dir / "boss" / "_diagnostics"
            diag_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = diag_dir / f"dom_snapshot_{ts}.json"
            path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("DOM 诊断快照已保存: {}", path)
        except Exception as exc:
            logger.warning("保存 DOM 诊断快照失败: {}", exc)

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

    def _start_watchdog_loop(self) -> None:
        """在 ws_server 的 asyncio loop 上启动看门狗巡检任务。"""
        loop = self.ws_server.event_loop
        if loop is None:
            self._log("warning", "看门狗未启动：ws_server 事件循环未就绪")
            return
        # 总是刷新全局起点：避免 start_collect 被重复调用时残留旧时间戳触发误判
        self._watchdog.global_last_event_at = datetime.now()
        if self._watchdog_task is not None and not self._watchdog_task.done():
            return

        bridge = self

        async def _watch():
            try:
                while bridge.runtime_state.get("running"):
                    await asyncio.sleep(bridge._watchdog_poll_interval)
                    bridge._poll_watchdog()
            except asyncio.CancelledError:
                pass

        try:
            self._watchdog_task = asyncio.run_coroutine_threadsafe(_watch(), loop)
            self._write_event_log("watchdog_started", {
                "candidate_timeout_s": self._watchdog.candidate_timeout,
                "global_timeout_s": self._watchdog.global_timeout,
            })
        except Exception as exc:
            self._log("warning", f"看门狗启动失败: {exc}")
            self._watchdog_task = None

    def _stop_watchdog_loop(self) -> None:
        task = self._watchdog_task
        if task is not None:
            try:
                task.cancel()
            except Exception:
                pass
        self._watchdog_task = None

    def _poll_watchdog(self) -> None:
        # 候选人级超时
        for to in self._watchdog.check_candidates():
            self._log(
                "highlight",
                f"⚠️ 看门狗：候选人 {to.candidate_id} 已 {to.elapsed_seconds:.0f}s 无事件"
                f"（最后事件 {to.last_event_type}），强制跳过",
            )
            self._write_event_log("watchdog_candidate_timeout", {
                "candidate_id": to.candidate_id,
                "last_event_type": to.last_event_type,
                "elapsed_seconds": round(to.elapsed_seconds, 1),
            })
            command = {
                "type": "skip_current_candidate",
                "data": {"candidate_id": to.candidate_id, "reason": "watchdog"},
                "run_id": self.runtime_state.get("run_id", ""),
            }
            try:
                self.ws_server.send_command(command)
            except Exception as exc:
                self._log("warning", f"看门狗 skip 指令下发失败: {exc}")
        # 全局级超时
        idle = self._watchdog.check_global()
        if idle is not None:
            self._log("error", f"⚠️ 看门狗：全局 {idle:.0f}s 无事件，已强制终止采集")
            self._write_event_log("watchdog_global_idle_timeout", {"elapsed_seconds": round(idle, 1)})
            try:
                self._finish_crawl_task("failed", error_message="global_idle_timeout")
            except Exception as exc:
                self._log("warning", f"看门狗终止采集失败: {exc}")
            self.runtime_state["running"] = False
            self._stop_watchdog_loop()

    def _spawn_analyze_test_run(self) -> None:
        log_file = self.runtime_state.get("log_file", "")
        if not log_file:
            return
        script_path = Path("scripts/analyze_test_run.py")
        if not script_path.exists():
            self._write_event_log("analyze_test_run_skipped", {"reason": "script_not_found", "expected_path": str(script_path)})
            return
        try:
            subprocess.Popen(
                [sys.executable, str(script_path), log_file],
                cwd=str(Path.cwd()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._write_event_log("analyze_test_run_spawned", {"log_file": log_file})
        except Exception as exc:
            self._log("warning", f"启动 analyze_test_run.py 失败: {exc}")
