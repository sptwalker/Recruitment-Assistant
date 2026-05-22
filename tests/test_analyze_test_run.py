"""Tests for scripts/analyze_test_run.py — 8 test cases per spec."""

import json
import sys
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helper: import main() from the script without running it
# ---------------------------------------------------------------------------

def _import_main():
    """Import main from scripts/analyze_test_run.py."""
    import importlib.util
    script = Path(__file__).parent.parent / "scripts" / "analyze_test_run.py"
    spec = importlib.util.spec_from_file_location("analyze_test_run", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_jsonl(tmp_path: Path, platform: str, run_id: str, events: list[dict]) -> Path:
    """Create a JSONL file at the canonical path logs/<platform>_extension/YYYYMMDD/run_<run_id>.jsonl."""
    date_str = "20260522"
    log_dir = tmp_path / "logs" / f"{platform}_extension" / date_str
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / f"run_{run_id}.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return jsonl_path


def _base_events(run_id: str, platform: str = "boss") -> list[dict]:
    """Minimal events that make a valid (non-empty) run."""
    return [
        {
            "seq": 1,
            "at": "2026-05-22T10:00:00",
            "run_id": run_id,
            "event": "collect_started",
            "payload": {"expected_total": 5},
        },
        {
            "seq": 2,
            "at": "2026-05-22T10:06:12",
            "run_id": run_id,
            "event": "collect_finished",
            "payload": {},
        },
    ]


def _persist_event(run_id: str, sig: str, seq: int = 10) -> dict:
    return {
        "seq": seq,
        "at": "2026-05-22T10:03:00",
        "run_id": run_id,
        "event": "resume_persist_confirmed",
        "payload": {"candidate_signature": sig},
    }


def _learning_event(run_id: str, sig: str, seq: int = 20) -> dict:
    return {
        "seq": seq,
        "at": "2026-05-22T10:04:00",
        "run_id": run_id,
        "event": "manual_download_learning_required",
        "payload": {"candidate_signature": sig},
    }


def _misroute_event(run_id: str, sig: str, kind: str = "A2", seq: int = 30) -> dict:
    return {
        "seq": seq,
        "at": "2026-05-22T10:05:00",
        "run_id": run_id,
        "event": "learning_misroute_detected",
        "payload": {"kind": kind, "candidate_signature": sig, "candidate_id": sig, "note": "test note"},
    }


def _button_event(run_id: str, event_name: str, sig: str, seq: int = 40) -> dict:
    return {
        "seq": seq,
        "at": "2026-05-22T10:02:00",
        "run_id": run_id,
        "event": event_name,
        "payload": {"candidate_signature": sig},
    }


# ---------------------------------------------------------------------------
# Test 1: round counter increments per platform
# ---------------------------------------------------------------------------

def test_round_counter_increments_per_platform(tmp_path):
    main = _import_main()

    # boss round 1
    boss_jsonl = _make_jsonl(tmp_path, "boss", "run001", _base_events("run001", "boss"))
    rel_boss = boss_jsonl.relative_to(tmp_path).as_posix()
    main([rel_boss], base_dir=tmp_path)

    # zhilian round 1
    zhilian_jsonl = _make_jsonl(tmp_path, "zhilian", "run002", _base_events("run002", "zhilian"))
    rel_zhilian = zhilian_jsonl.relative_to(tmp_path).as_posix()
    main([rel_zhilian], base_dir=tmp_path)

    counter_path = tmp_path / "logs" / "test_runs" / "_round_counter.json"
    counter = json.loads(counter_path.read_text(encoding="utf-8"))
    assert counter.get("boss") == 1
    assert counter.get("zhilian") == 1

    # boss round 2
    boss_jsonl2 = _make_jsonl(tmp_path, "boss", "run003", _base_events("run003", "boss"))
    rel_boss2 = boss_jsonl2.relative_to(tmp_path).as_posix()
    main([rel_boss2], base_dir=tmp_path)

    counter = json.loads(counter_path.read_text(encoding="utf-8"))
    assert counter.get("boss") == 2
    assert counter.get("zhilian") == 1


# ---------------------------------------------------------------------------
# Test 2: history records first and last success
# ---------------------------------------------------------------------------

def test_history_records_first_and_last_success(tmp_path):
    main = _import_main()
    sig = "袁先生在上海|28岁|本科"

    # Round 1: one persist event
    events1 = _base_events("run_r1") + [_persist_event("run_r1", sig, seq=5)]
    jsonl1 = _make_jsonl(tmp_path, "boss", "run_r1", events1)
    main([jsonl1.relative_to(tmp_path).as_posix()], base_dir=tmp_path)

    # Round 2: same signature again
    events2 = _base_events("run_r2") + [_persist_event("run_r2", sig, seq=5)]
    jsonl2 = _make_jsonl(tmp_path, "boss", "run_r2", events2)
    main([jsonl2.relative_to(tmp_path).as_posix()], base_dir=tmp_path)

    history_path = tmp_path / "logs" / "test_runs" / "_persist_history.json"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    entry = history["boss"][sig]
    assert entry["total_success"] == 2
    assert entry["first_success_round"] == 1
    assert entry["last_success_round"] == 2


# ---------------------------------------------------------------------------
# Test 3: misroute detection in summary
# ---------------------------------------------------------------------------

def test_misroute_detection_in_summary(tmp_path):
    main = _import_main()

    # With misroute
    events_with = _base_events("run_m1") + [_misroute_event("run_m1", "张先生|30岁|本科", kind="A2", seq=5)]
    jsonl_with = _make_jsonl(tmp_path, "boss", "run_m1", events_with)
    main([jsonl_with.relative_to(tmp_path).as_posix()], base_dir=tmp_path)

    # Find the generated markdown
    md_files = list((tmp_path / "logs" / "test_runs").glob("round_1_boss_*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "A2:" in content

    # Without misroute (new tmp_path context via separate run in same dir)
    events_without = _base_events("run_m2")
    jsonl_without = _make_jsonl(tmp_path, "boss", "run_m2", events_without)
    main([jsonl_without.relative_to(tmp_path).as_posix()], base_dir=tmp_path)

    md_files2 = list((tmp_path / "logs" / "test_runs").glob("round_2_boss_*.md"))
    assert len(md_files2) == 1
    content2 = md_files2[0].read_text(encoding="utf-8")
    # No misroute → "- 无"
    assert "- 无" in content2


# ---------------------------------------------------------------------------
# Test 4: history regression listed in summary
# ---------------------------------------------------------------------------

def test_history_regression_listed(tmp_path):
    main = _import_main()
    sig = "李女士在北京|25岁|硕士"

    # Round 1: persist confirmed
    events1 = _base_events("run_reg1") + [_persist_event("run_reg1", sig, seq=5)]
    jsonl1 = _make_jsonl(tmp_path, "boss", "run_reg1", events1)
    main([jsonl1.relative_to(tmp_path).as_posix()], base_dir=tmp_path)

    # Round 2: same sig goes into learning
    events2 = _base_events("run_reg2") + [_learning_event("run_reg2", sig, seq=5)]
    jsonl2 = _make_jsonl(tmp_path, "boss", "run_reg2", events2)
    main([jsonl2.relative_to(tmp_path).as_posix()], base_dir=tmp_path)

    md_files = list((tmp_path / "logs" / "test_runs").glob("round_2_boss_*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "历史回归" in content
    assert sig in content


# ---------------------------------------------------------------------------
# Test 5: button hit counts
# ---------------------------------------------------------------------------

def test_button_hit_counts(tmp_path):
    main = _import_main()

    events = _base_events("run_btn")
    for i in range(3):
        events.append(_button_event("run_btn", "auto_download_click_used", f"sig{i}", seq=10 + i))
    for i in range(2):
        events.append(_button_event("run_btn", "learned_download_click_used", f"sig{i+10}", seq=20 + i))

    jsonl = _make_jsonl(tmp_path, "boss", "run_btn", events)
    main([jsonl.relative_to(tmp_path).as_posix()], base_dir=tmp_path)

    md_files = list((tmp_path / "logs" / "test_runs").glob("round_1_boss_*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")

    # Table should show 3 for auto and 2 for learned
    assert "auto_download_click_used" in content
    assert "learned_download_click_used" in content
    # Check the counts appear in the table rows
    lines = content.splitlines()
    for line in lines:
        if "auto_download_click_used" in line:
            assert "3" in line
        if "learned_download_click_used" in line:
            assert "2" in line


# ---------------------------------------------------------------------------
# Test 6: unknown platform falls back gracefully
# ---------------------------------------------------------------------------

def test_unknown_platform_falls_back(tmp_path):
    main = _import_main()

    # Path that doesn't match the *_extension/YYYYMMDD/ pattern
    weird_dir = tmp_path / "logs" / "some_random_dir"
    weird_dir.mkdir(parents=True, exist_ok=True)
    jsonl = weird_dir / "run_weird.jsonl"
    events = _base_events("run_weird")
    with jsonl.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

    rel = jsonl.relative_to(tmp_path).as_posix()
    # Should not raise
    result = main([rel], base_dir=tmp_path)
    assert result == 0

    # Platform should be "unknown"
    counter_path = tmp_path / "logs" / "test_runs" / "_round_counter.json"
    counter = json.loads(counter_path.read_text(encoding="utf-8"))
    assert "unknown" in counter


# ---------------------------------------------------------------------------
# Test 7: empty JSONL exits non-zero
# ---------------------------------------------------------------------------

def test_empty_jsonl_exits_nonzero(tmp_path):
    main = _import_main()

    empty_dir = tmp_path / "logs" / "boss_extension" / "20260522"
    empty_dir.mkdir(parents=True, exist_ok=True)
    empty_file = empty_dir / "run_empty.jsonl"
    empty_file.write_text("", encoding="utf-8")

    rel = empty_file.relative_to(tmp_path).as_posix()
    result = main([rel], base_dir=tmp_path)
    assert result != 0


# ---------------------------------------------------------------------------
# Test 8: round comparison appended
# ---------------------------------------------------------------------------

def test_round_comparison_appended(tmp_path):
    main = _import_main()

    # Round 1: 2 auto_download_click_used
    events1 = _base_events("run_cmp1")
    for i in range(2):
        events1.append(_button_event("run_cmp1", "auto_download_click_used", f"sig{i}", seq=10 + i))
    jsonl1 = _make_jsonl(tmp_path, "boss", "run_cmp1", events1)
    main([jsonl1.relative_to(tmp_path).as_posix()], base_dir=tmp_path)

    # Round 2: 5 auto_download_click_used
    events2 = _base_events("run_cmp2")
    for i in range(5):
        events2.append(_button_event("run_cmp2", "auto_download_click_used", f"sig{i+10}", seq=10 + i))
    jsonl2 = _make_jsonl(tmp_path, "boss", "run_cmp2", events2)
    main([jsonl2.relative_to(tmp_path).as_posix()], base_dir=tmp_path)

    md_files = list((tmp_path / "logs" / "test_runs").glob("round_2_boss_*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text(encoding="utf-8")

    assert "与 Round 1 对比" in content
    # Should show the delta for auto_download_click_used: 2 → 5 (+3)
    assert "auto_download_click_used" in content
    assert "+3" in content
