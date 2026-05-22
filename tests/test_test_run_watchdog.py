"""Watchdog / Misroute 纯逻辑测试。"""

from datetime import datetime, timedelta
from recruitment_assistant.services.test_run_watchdog import (
    WatchdogState,
    MisrouteDetector,
    CANDIDATE_TIMEOUT_SECONDS,
    GLOBAL_TIMEOUT_SECONDS,
)


def test_candidate_state_persisted_then_learning_is_a2_misroute():
    detector = MisrouteDetector()
    detector.on_event("candidate_clicked", "C1", {})
    detector.on_event("resume_persist_confirmed", "C1", {})
    out = detector.on_event("manual_download_learning_required", "C1", {})
    assert len(out) == 1
    assert out[0]["kind"] == "A2"
    assert out[0]["candidate_id"] == "C1"


def test_candidate_state_learned_used_then_learning_is_a3_misroute():
    detector = MisrouteDetector()
    detector.on_event("candidate_clicked", "C2", {})
    detector.on_event("learned_download_click_used", "C2", {})
    out = detector.on_event("manual_download_learning_required", "C2", {})
    assert len(out) == 1
    assert out[0]["kind"] == "A3"


def test_clean_path_no_misroute():
    detector = MisrouteDetector()
    detector.on_event("candidate_clicked", "C3", {})
    detector.on_event("auto_download_click_used", "C3", {})
    detector.on_event("resume_persist_confirmed", "C3", {})
    # 没有 learning_required 也就没有 misroute
    out = detector.on_event("candidate_clicked", "C4", {})
    assert out == []


def test_a2_and_a3_both_trigger_when_persisted_and_learned_then_learning():
    detector = MisrouteDetector()
    detector.on_event("candidate_clicked", "C5", {})
    detector.on_event("learned_download_click_used", "C5", {})
    detector.on_event("resume_persist_confirmed", "C5", {})
    out = detector.on_event("manual_download_learning_required", "C5", {})
    kinds = sorted(o["kind"] for o in out)
    assert kinds == ["A2", "A3"]


def test_candidate_clicked_resets_state_for_same_cid():
    detector = MisrouteDetector()
    detector.on_event("candidate_clicked", "C6", {})
    detector.on_event("resume_persist_confirmed", "C6", {})
    # 候选人重新点击应该清空状态
    detector.on_event("candidate_clicked", "C6", {})
    out = detector.on_event("manual_download_learning_required", "C6", {})
    assert out == []


def test_repeated_learning_required_emits_each_time():
    """每次 manual_download_learning_required 都 emit；重复触发是加重信号。"""
    detector = MisrouteDetector()
    detector.on_event("candidate_clicked", "C_REPEAT", {})
    detector.on_event("resume_persist_confirmed", "C_REPEAT", {})
    first = detector.on_event("manual_download_learning_required", "C_REPEAT", {})
    second = detector.on_event("manual_download_learning_required", "C_REPEAT", {})
    assert len(first) == 1 and first[0]["kind"] == "A2"
    assert len(second) == 1 and second[0]["kind"] == "A2"


def test_constants_match_spec():
    assert CANDIDATE_TIMEOUT_SECONDS == 120
    assert GLOBAL_TIMEOUT_SECONDS == 300


def test_candidate_watchdog_fires_after_timeout():
    state = WatchdogState(candidate_timeout=120)
    state.on_event("candidate_clicked", "C7", {})
    state.on_event("resume_preview_not_found", "C7", {})
    # 模拟当前时间是 121 秒后
    later = state.detector.states["C7"].last_event_at + timedelta(seconds=121)
    timed_out = state.check_candidates(now=later)
    assert len(timed_out) == 1
    assert timed_out[0].candidate_id == "C7"
    assert timed_out[0].elapsed_seconds >= 120
    assert timed_out[0].last_event_type == "resume_preview_not_found"


def test_candidate_watchdog_skips_terminal_candidate():
    state = WatchdogState(candidate_timeout=120)
    state.on_event("candidate_clicked", "C8", {})
    state.on_event("resume_persist_confirmed", "C8", {})
    later = state.detector.states["C8"].last_event_at + timedelta(seconds=200)
    timed_out = state.check_candidates(now=later)
    assert timed_out == []  # 已落库不超时


def test_candidate_watchdog_fires_only_once():
    state = WatchdogState(candidate_timeout=60)
    state.on_event("candidate_clicked", "C9", {})
    state.on_event("resume_preview_not_found", "C9", {})
    later = state.detector.states["C9"].last_event_at + timedelta(seconds=70)
    first = state.check_candidates(now=later)
    second = state.check_candidates(now=later + timedelta(seconds=10))
    assert len(first) == 1
    assert second == []  # 标记 terminal 后不再触发


def test_global_watchdog_fires_after_idle():
    state = WatchdogState(global_timeout=300)
    state.on_event("collect_started", "", {})
    later = state.global_last_event_at + timedelta(seconds=301)
    elapsed = state.check_global(now=later)
    assert elapsed is not None and elapsed >= 300


def test_global_watchdog_quiet_under_idle():
    state = WatchdogState(global_timeout=300)
    state.on_event("collect_started", "", {})
    later = state.global_last_event_at + timedelta(seconds=200)
    assert state.check_global(now=later) is None


def test_heartbeat_does_not_reset_global_watchdog():
    state = WatchdogState(global_timeout=300)
    state.on_event("collect_started", "", {})
    base = state.global_last_event_at
    # 心跳过 200 秒
    state.on_event("heartbeat", "", {})
    # global_last_event_at 不应该被心跳刷新
    assert state.global_last_event_at == base


def test_candidate_skipped_marks_terminal():
    state = WatchdogState(candidate_timeout=60)
    state.on_event("candidate_clicked", "C_SKIP", {})
    state.on_event("candidate_skipped", "C_SKIP", {})
    later = state.detector.states["C_SKIP"].last_event_at + timedelta(seconds=120)
    assert state.check_candidates(now=later) == []


def test_resume_request_success_marks_terminal():
    state = WatchdogState(candidate_timeout=60)
    state.on_event("candidate_clicked", "C_REQ", {})
    state.on_event("resume_request_success", "C_REQ", {})
    later = state.detector.states["C_REQ"].last_event_at + timedelta(seconds=120)
    assert state.check_candidates(now=later) == []


def test_reset_clears_state_and_global_timer():
    state = WatchdogState(candidate_timeout=60, global_timeout=300)
    state.on_event("candidate_clicked", "C_R", {})
    state.on_event("collect_started", "", {})
    assert state.global_last_event_at is not None
    assert "C_R" in state.detector.states
    state.reset()
    assert state.global_last_event_at is None
    assert state.detector.states == {}
    # 新 detector，旧引用应失效
    assert state.check_global() is None
    assert state.check_candidates() == []
