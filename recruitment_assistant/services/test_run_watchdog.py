"""测试期看门狗 & 学习模式误进检测的纯逻辑。

bridge 在每条 emit 来到时调用 MisrouteDetector.on_event()，
得到需要 emit 的 misroute 事件 list；候选人 / 全局超时由
WatchdogState 持有时间戳，asyncio 任务定期巡检。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

CANDIDATE_TIMEOUT_SECONDS = 120
GLOBAL_TIMEOUT_SECONDS = 300

# 每个候选人的终态：到达后停止该 cid 的 watchdog
CANDIDATE_TERMINAL_EVENTS = frozenset({
    "resume_persist_confirmed",
    "candidate_skipped",
    "resume_request_success",
})


@dataclass
class CandidateState:
    persisted: bool = False
    auto_used: bool = False
    learned_used: bool = False
    last_event_at: datetime = field(default_factory=datetime.now)
    last_event_type: str = ""
    terminal: bool = False


class MisrouteDetector:
    """在 bridge 端按 emit 流维护 per-candidate state，
    收到 manual_download_learning_required 时返回需要 emit 的 misroute 事件。"""

    def __init__(self) -> None:
        self._state: dict[str, CandidateState] = {}

    @property
    def states(self) -> dict[str, CandidateState]:
        """供测试 / 巡检读取用；调用方应视为只读，请勿直接 .clear() / 删除条目。"""
        return self._state

    def on_event(self, event_type: str, candidate_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not candidate_id:
            return out
        if event_type == "candidate_clicked":
            # 重置该 cid 的 state
            self._state[candidate_id] = CandidateState()
            return out

        st = self._state.setdefault(candidate_id, CandidateState())
        st.last_event_at = datetime.now()
        st.last_event_type = event_type

        if event_type == "auto_download_click_used":
            st.auto_used = True
        elif event_type == "learned_download_click_used":
            st.learned_used = True
        elif event_type == "resume_persist_confirmed":
            st.persisted = True
        elif event_type == "manual_download_learning_required":
            # 每次 learning_required 都 emit；重复触发是 misroute 加重信号，不去重
            sig = payload.get("candidate_signature", "")
            if st.persisted:
                out.append({
                    "kind": "A2",
                    "candidate_id": candidate_id,
                    "candidate_signature": sig,
                    "note": "已落库却又弹学习模式（重复触发）",
                })
            if st.learned_used:
                out.append({
                    "kind": "A3",
                    "candidate_id": candidate_id,
                    "candidate_signature": sig,
                    "note": "learnedClick 已用却又弹学习模式（学习成果未生效）",
                })

        if event_type in CANDIDATE_TERMINAL_EVENTS:
            st.terminal = True
        return out


@dataclass
class TimedOutCandidate:
    candidate_id: str
    last_event_type: str
    elapsed_seconds: float


@dataclass
class WatchdogState:
    """看门狗状态：bridge 用 datetime 走，asyncio 任务定期 poll check_candidates() / check_global()。"""

    candidate_timeout: int = CANDIDATE_TIMEOUT_SECONDS
    global_timeout: int = GLOBAL_TIMEOUT_SECONDS
    global_last_event_at: datetime | None = None
    detector: MisrouteDetector = field(default_factory=MisrouteDetector)

    def on_event(self, event_type: str, candidate_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        # 心跳不算事件
        if event_type != "heartbeat":
            self.global_last_event_at = datetime.now()
        return self.detector.on_event(event_type, candidate_id, payload)

    def check_candidates(self, now: datetime | None = None) -> list[TimedOutCandidate]:
        now = now or datetime.now()
        timed_out: list[TimedOutCandidate] = []
        for cid, st in list(self.detector.states.items()):
            if st.terminal:
                continue
            elapsed = (now - st.last_event_at).total_seconds()
            if elapsed >= self.candidate_timeout:
                timed_out.append(TimedOutCandidate(
                    candidate_id=cid,
                    last_event_type=st.last_event_type,
                    elapsed_seconds=elapsed,
                ))
                # 标记为终态，避免重复触发
                st.terminal = True
        return timed_out

    def check_global(self, now: datetime | None = None) -> float | None:
        if not self.global_last_event_at:
            return None
        now = now or datetime.now()
        elapsed = (now - self.global_last_event_at).total_seconds()
        if elapsed >= self.global_timeout:
            return elapsed
        return None

    def reset(self) -> None:
        self.global_last_event_at = None
        self.detector = MisrouteDetector()
