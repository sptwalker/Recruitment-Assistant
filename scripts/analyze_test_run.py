"""analyze_test_run.py — Ingest a JSONL test-run log and produce a markdown summary.

Usage:
    python scripts/analyze_test_run.py <jsonl_path>

<jsonl_path> is a relative path like logs/zhilian_extension/20260522/run_abc123.jsonl.
Resolved from cwd (or base_dir when called programmatically).
"""

import argparse
import json
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Platform inference
# ---------------------------------------------------------------------------

_PLATFORM_RE = re.compile(r"logs[/\\]([a-z0-9]+)_extension[/\\]\d{8}[/\\]")


def _infer_platform(jsonl_path: Path) -> str:
    """Extract platform from path like logs/<platform>_extension/YYYYMMDD/run_*.jsonl."""
    m = _PLATFORM_RE.search(jsonl_path.as_posix())
    if m:
        return m.group(1)
    # Try with backslashes too
    m = _PLATFORM_RE.search(str(jsonl_path))
    if m:
        return m.group(1)
    return "unknown"


# ---------------------------------------------------------------------------
# JSONL loading
# ---------------------------------------------------------------------------

def _load_events(jsonl_path: Path) -> list[dict]:
    events = []
    with jsonl_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


# ---------------------------------------------------------------------------
# Atomic JSON file helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return default


def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with open(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        Path(tmp_name).replace(path)
    except Exception:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Round counter
# ---------------------------------------------------------------------------

def _increment_round(counter_path: Path, platform: str) -> int:
    counter = _read_json(counter_path, {})
    counter[platform] = counter.get(platform, 0) + 1
    _write_json_atomic(counter_path, counter)
    return counter[platform]


# ---------------------------------------------------------------------------
# History update
# ---------------------------------------------------------------------------

def _update_history(history_path: Path, platform: str, round_num: int, events: list[dict]) -> dict:
    history = _read_json(history_path, {})
    platform_hist = history.setdefault(platform, {})

    for ev in events:
        if ev.get("event") != "resume_persist_confirmed":
            continue
        payload = ev.get("payload", {})
        sig = payload.get("candidate_signature", "")
        if not sig:
            continue
        at_str = ev.get("at", datetime.now().isoformat())
        if sig in platform_hist:
            entry = platform_hist[sig]
            entry["last_success_round"] = round_num
            entry["last_success_at"] = at_str
            entry["total_success"] = entry.get("total_success", 0) + 1
        else:
            platform_hist[sig] = {
                "first_success_round": round_num,
                "first_success_at": at_str,
                "last_success_round": round_num,
                "last_success_at": at_str,
                "total_success": 1,
            }

    _write_json_atomic(history_path, history)
    return history


# ---------------------------------------------------------------------------
# Regression detection
# ---------------------------------------------------------------------------

def _find_regressions(history: dict, platform: str, events: list[dict]) -> list[tuple[str, dict]]:
    """Return list of (sig, history_entry) for manual_download_learning_required events
    whose signature already appears in history."""
    platform_hist = history.get(platform, {})
    regressions = []
    seen = set()
    for ev in events:
        if ev.get("event") != "manual_download_learning_required":
            continue
        payload = ev.get("payload", {})
        sig = payload.get("candidate_signature", "")
        if not sig or sig in seen:
            continue
        seen.add(sig)
        if sig in platform_hist:
            regressions.append((sig, platform_hist[sig]))
    return regressions


# ---------------------------------------------------------------------------
# Duration formatting
# ---------------------------------------------------------------------------

def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _format_duration(start_at: str, end_at: str) -> str:
    s = _parse_dt(start_at)
    e = _parse_dt(end_at)
    if s is None or e is None:
        return "未知"
    delta = int((e - s).total_seconds())
    if delta < 0:
        return "未知"
    m, sec = divmod(delta, 60)
    return f"{m}m{sec}s"


# ---------------------------------------------------------------------------
# Previous round comparison helpers
# ---------------------------------------------------------------------------

_BUTTON_EVENTS = [
    "auto_download_click_used",
    "learned_download_click_used",
    "manual_download_learning_success",
    "manual_download_learning_failed",
]


def _parse_prev_summary(md_text: str) -> dict[str, int] | None:
    """Extract button counts, misroute count, and watchdog candidate count from a previous summary.
    Returns None if parsing fails."""
    try:
        metrics: dict[str, int] = {}
        for btn in _BUTTON_EVENTS:
            m = re.search(rf"\|\s*{re.escape(btn)}\s*\|\s*(\d+)\s*\|", md_text)
            metrics[btn] = int(m.group(1)) if m else 0

        # misroute count: count lines starting with "- A2:" or "- A3:" etc.
        misroute_lines = re.findall(r"^\s*-\s+A[23]:", md_text, re.MULTILINE)
        metrics["learning_misroute_detected"] = len(misroute_lines)

        # watchdog candidate count: look for "候选人级触发：N 次"
        m = re.search(r"候选人级触发[：:]\s*(\d+)\s*次", md_text)
        metrics["watchdog_candidate"] = int(m.group(1)) if m else 0

        return metrics
    except Exception:
        return None


def _find_prev_summary(test_runs_dir: Path, platform: str, prev_round: int) -> Path | None:
    pattern = f"round_{prev_round}_{platform}_*.md"
    candidates = sorted(test_runs_dir.glob(pattern))
    return candidates[-1] if candidates else None


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------

def _build_summary(
    round_num: int,
    platform: str,
    run_id: str,
    jsonl_path_str: str,
    events: list[dict],
    regressions: list[tuple[str, dict]],
    test_runs_dir: Path,
) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Collect key events
    collect_started = next((e for e in events if e.get("event") == "collect_started"), None)
    collect_finished = next((e for e in events if e.get("event") == "collect_finished"), None)

    planned = 0
    if collect_started:
        planned = collect_started.get("payload", {}).get("expected_total", 0)
    if not planned:
        planned = sum(1 for e in events if e.get("event") == "candidate_clicked")

    actual_success = sum(1 for e in events if e.get("event") == "resume_persist_confirmed")
    skipped = sum(1 for e in events if e.get("event") == "candidate_skipped")

    start_at = collect_started.get("at", "") if collect_started else ""
    end_at = collect_finished.get("at", "") if collect_finished else ""
    duration = _format_duration(start_at, end_at)

    # Watchdog
    watchdog_candidates = [e for e in events if e.get("event") == "watchdog_candidate_timeout"]
    watchdog_global = [e for e in events if e.get("event") == "watchdog_global_idle_timeout"]

    # Button hits
    button_counts: dict[str, int] = {btn: 0 for btn in _BUTTON_EVENTS}
    for e in events:
        ev_name = e.get("event", "")
        if ev_name in button_counts:
            button_counts[ev_name] += 1

    # Misroutes
    misroutes = [e for e in events if e.get("event") == "learning_misroute_detected"]
    misroutes_by_kind: dict[str, list[dict]] = defaultdict(list)
    for e in misroutes:
        p = e.get("payload", {})
        kind = p.get("kind", "?")
        misroutes_by_kind[kind].append(p)

    # Unknown events
    known_events = {
        "collect_started", "collect_finished", "candidate_clicked", "candidate_skipped",
        "resume_persist_confirmed", "manual_download_learning_required",
        "learning_misroute_detected", "watchdog_candidate_timeout",
        "watchdog_global_idle_timeout", "run_started", "run_summary",
        "module_versions", "ui_log", "extension_event",
        "analyze_test_run_skipped", "analyze_test_run_spawned",
    } | set(_BUTTON_EVENTS)

    unknown_counts: dict[str, list[str]] = defaultdict(list)
    for e in events:
        ev_name = e.get("event", "")
        if ev_name not in known_events:
            p = e.get("payload", {})
            sig = p.get("candidate_signature") or p.get("candidate_id") or ""
            unknown_counts[ev_name].append(sig)

    lines = []
    lines.append(f"# Round {round_num} — {platform} — {ts_str}")
    lines.append("")
    lines.append(f"**本轮 run_id**：{run_id}")
    lines.append(f"**JSONL**：{jsonl_path_str}")
    lines.append(f"**计划/实际**：{planned} / {actual_success} 成功，{skipped} 跳过")
    lines.append(f"**用时**：{duration}")
    lines.append("")

    # Watchdog section
    lines.append("## 看门狗")
    lines.append(f"- 候选人级触发：{len(watchdog_candidates)} 次")
    for e in watchdog_candidates:
        p = e.get("payload", {})
        sig = p.get("candidate_signature") or p.get("candidate_id") or "?"
        elapsed = p.get("elapsed_seconds", "?")
        last_ev = p.get("last_event_type", "?")
        lines.append(f"  - {sig}：等待 {elapsed}s 后 watchdog 跳过（最后事件 {last_ev}）")
    lines.append(f"- 全局级触发：{len(watchdog_global)} 次")
    for e in watchdog_global:
        p = e.get("payload", {})
        elapsed = p.get("elapsed_seconds", "?")
        lines.append(f"  - 等待 {elapsed}s 后强制终止")
    lines.append("")

    # Button hit distribution
    lines.append("## 按钮命中分布")
    lines.append("| 路径 | 次数 |")
    lines.append("|---|---|")
    for btn in _BUTTON_EVENTS:
        lines.append(f"| {btn} | {button_counts[btn]} |")
    lines.append("")

    # Misroute section
    lines.append("## ⚠️ 误进学习流程（misroute）")
    if not misroutes:
        lines.append("- 无")
    else:
        for kind in sorted(misroutes_by_kind.keys()):
            for p in misroutes_by_kind[kind]:
                sig = p.get("candidate_signature") or p.get("candidate_id") or "?"
                note = p.get("note", "")
                lines.append(f"- {kind}: {sig} —— {note}")
    lines.append("")

    # History regression section
    lines.append("## ⚠️ 历史回归（已下载又进学习）")
    if not regressions:
        lines.append("- 无")
    else:
        for sig, entry in regressions:
            lines.append(
                f"- {sig}（首次下载: round {entry['first_success_round']} @ {entry['first_success_at']}; "
                f"已成功 {entry['total_success']} 次）"
            )
    lines.append("")

    # Unknown events section
    lines.append("## 未识别事件")
    lines.append("| 事件 | 次数 | 候选人样本 |")
    lines.append("|---|---|---|")
    if unknown_counts:
        for ev_name, sigs in sorted(unknown_counts.items()):
            sample = sigs[0] if sigs[0] else "—"
            lines.append(f"| {ev_name} | {len(sigs)} | {sample} |")
    lines.append("")

    # Previous round comparison
    if round_num > 1:
        prev_file = _find_prev_summary(test_runs_dir, platform, round_num - 1)
        if prev_file:
            prev_text = prev_file.read_text(encoding="utf-8")
            prev_metrics = _parse_prev_summary(prev_text)
            if prev_metrics is not None:
                lines.append(f"## 与 Round {round_num - 1} 对比")
                curr_misroute = len(misroutes)
                curr_watchdog_cand = len(watchdog_candidates)
                compare_metrics = [
                    ("auto_download_click_used", button_counts["auto_download_click_used"]),
                    ("learned_download_click_used", button_counts["learned_download_click_used"]),
                    ("manual_download_learning_success", button_counts["manual_download_learning_success"]),
                    ("manual_download_learning_failed", button_counts["manual_download_learning_failed"]),
                    ("learning_misroute_detected", curr_misroute),
                    ("watchdog_candidate", curr_watchdog_cand),
                ]
                for metric, curr_val in compare_metrics:
                    prev_val = prev_metrics.get(metric, 0)
                    delta = curr_val - prev_val
                    delta_str = f"+{delta}" if delta >= 0 else str(delta)
                    lines.append(f"- {metric}: {prev_val} → {curr_val} ({delta_str})")
                lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Signature sanity warning
# ---------------------------------------------------------------------------

def _check_signature_sanity(history: dict, platform: str, events: list[dict], round_num: int) -> None:
    confirmed_sigs = [
        e.get("payload", {}).get("candidate_signature", "")
        for e in events
        if e.get("event") == "resume_persist_confirmed"
    ]
    confirmed_sigs = [s for s in confirmed_sigs if s]
    if len(confirmed_sigs) < 3:
        return
    platform_hist = history.get(platform, {})
    # Count sigs that don't appear in history (excluding the ones just added this round)
    # We check against history BEFORE this round's additions — but since we already updated,
    # we check if they appear with total_success > 1 (meaning they existed before) or
    # if they appear in other platforms. Simpler: check if sig was in history before this run.
    # Since we already updated history, we approximate: sigs with total_success == 1 are new.
    new_count = 0
    for sig in confirmed_sigs:
        entry = platform_hist.get(sig)
        if entry is None or entry.get("total_success", 1) == 1:
            new_count += 1
    ratio = new_count / len(confirmed_sigs)
    if ratio > 0.8:
        print(
            f"WARNING: signature 规则可能改了 — 本轮 {len(confirmed_sigs)} 个签名中 "
            f"{new_count} 个未在历史中出现（>80%），请人工核查",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None, base_dir=None) -> int:
    parser = argparse.ArgumentParser(description="Analyze a JSONL test-run log.")
    parser.add_argument("jsonl_path", help="Relative path to the JSONL file")
    args = parser.parse_args(argv)

    base = Path(base_dir) if base_dir else Path.cwd()

    # Resolve JSONL path
    raw_path = Path(args.jsonl_path)
    if raw_path.is_absolute():
        jsonl_path = raw_path
    else:
        jsonl_path = base / raw_path

    if not jsonl_path.exists():
        print(f"ERROR: JSONL file not found: {jsonl_path}", file=sys.stderr)
        return 1

    if jsonl_path.stat().st_size == 0:
        print(f"ERROR: JSONL file is empty: {jsonl_path}", file=sys.stderr)
        return 1

    # Load events
    events = _load_events(jsonl_path)
    if not events:
        print(f"ERROR: No valid JSON lines in: {jsonl_path}", file=sys.stderr)
        return 1

    # Infer platform
    platform = _infer_platform(jsonl_path)

    # Output directory
    test_runs_dir = base / "logs" / "test_runs"
    test_runs_dir.mkdir(parents=True, exist_ok=True)

    counter_path = test_runs_dir / "_round_counter.json"
    history_path = test_runs_dir / "_persist_history.json"

    # Increment round counter
    round_num = _increment_round(counter_path, platform)

    # Update history (before regression check so we can detect new-this-round)
    history = _update_history(history_path, platform, round_num, events)

    # Detect regressions (manual_download_learning_required for sigs already in history)
    # We need history BEFORE this run's persist events were added.
    # Re-read history before update to get pre-run state.
    # Actually we already updated — regressions are sigs in manual_download_learning_required
    # that appear in history. Since we only add to history from resume_persist_confirmed,
    # a sig that appears in manual_download_learning_required AND in history means it was
    # persisted in a PREVIOUS round (or earlier in this run). This is correct behavior.
    regressions = _find_regressions(history, platform, events)

    # Signature sanity warning
    _check_signature_sanity(history, platform, events, round_num)

    # Extract run_id
    run_id = "unknown"
    for e in events:
        rid = e.get("run_id", "")
        if rid:
            run_id = rid
            break

    # Build summary
    summary = _build_summary(
        round_num=round_num,
        platform=platform,
        run_id=run_id,
        jsonl_path_str=args.jsonl_path,
        events=events,
        regressions=regressions,
        test_runs_dir=test_runs_dir,
    )

    # Write summary file
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_filename = f"round_{round_num}_{platform}_{ts}.md"
    summary_path = test_runs_dir / summary_filename
    summary_path.write_text(summary, encoding="utf-8")

    print(f"Summary written: {summary_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
