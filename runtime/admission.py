from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class LoadState(str, Enum):
    NORMAL = "normal"
    CONSTRAINED = "constrained"
    DEGRADED = "degraded"


@dataclass
class AdmissionDecision:
    allowed: bool
    mode: str  # "live" | "cache_only" | "live_constrained" | "blocked"
    load_state: LoadState
    reason: str


STATE_FILE = Path("RESEARCH") / ".warehouse_load_state.json"

# Thresholds (tune per warehouse)
_CONSTRAINED_TIMEOUT_RATE = 0.30   # 30% of recent queries timed out → constrained
_DEGRADED_TIMEOUT_RATE = 0.60      # 60% → degraded
_WINDOW_SIZE = 10                  # look at the last N queries


@dataclass
class _LoadTracker:
    recent: list[dict[str, Any]] = field(default_factory=list)
    state: LoadState = LoadState.NORMAL

    def record(self, *, timed_out: bool) -> None:
        self.recent.append({"ts": time.time(), "timed_out": timed_out})
        if len(self.recent) > _WINDOW_SIZE:
            self.recent = self.recent[-_WINDOW_SIZE:]
        self._recompute()

    def _recompute(self) -> None:
        if not self.recent:
            self.state = LoadState.NORMAL
            return
        rate = sum(1 for r in self.recent if r["timed_out"]) / len(self.recent)
        if rate >= _DEGRADED_TIMEOUT_RATE:
            self.state = LoadState.DEGRADED
        elif rate >= _CONSTRAINED_TIMEOUT_RATE:
            self.state = LoadState.CONSTRAINED
        else:
            self.state = LoadState.NORMAL

    def snapshot(self) -> dict[str, Any]:
        return {
            "load_state": self.state.value,
            "recent_query_count": len(self.recent),
            "timeout_rate": (
                sum(1 for r in self.recent if r["timed_out"]) / len(self.recent)
                if self.recent else 0.0
            ),
        }


_tracker = _LoadTracker()

_STALE_THRESHOLD_SECONDS = 3600  # discard persisted entries older than 1 hour


def _persist_state() -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({**_tracker.snapshot(), "recent": _tracker.recent}, indent=2),
        encoding="utf-8",
    )


def _load_state() -> None:
    """Restore load tracker from the persisted file if it exists and is not stale."""
    if not STATE_FILE.exists():
        return
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    recent = data.get("recent", [])
    if not recent:
        return

    # Discard entries older than the stale threshold so a previous session's
    # degraded state does not bleed into an unrelated new session.
    cutoff = time.time() - _STALE_THRESHOLD_SECONDS
    recent = [r for r in recent if isinstance(r, dict) and r.get("ts", 0) > cutoff]

    _tracker.recent = recent
    _tracker._recompute()


_load_state()  # restore persisted state on module import


def record_query_outcome(*, timed_out: bool) -> None:
    """Call this after every SQL execution to update load state."""
    _tracker.record(timed_out=timed_out)
    _persist_state()


def get_warehouse_snapshot() -> dict[str, Any]:
    """Return the current warehouse load snapshot (for LLM context)."""
    return _tracker.snapshot()


# ---------------------------------------------------------------------------
# Admission control — called by the SQL execution tool
# ---------------------------------------------------------------------------

def check_admission(cost_class: str, *, allow_cache_fallback: bool = True) -> AdmissionDecision:
    """
    Decide whether a query should be executed, queued, or blocked.

    Cost classes:
        "cheap"    — scalar queries, no GROUP BY, fast
        "standard" — GROUP BY / JOIN queries, slower

    Returns an AdmissionDecision. The SQL execution tool enforces the decision;
    the LLM is informed of the outcome in the query result.
    """
    state = _tracker.state

    if state == LoadState.NORMAL:
        return AdmissionDecision(
            allowed=True, mode="live", load_state=state,
            reason="Warehouse load is normal.",
        )

    if state == LoadState.CONSTRAINED:
        if cost_class == "cheap":
            return AdmissionDecision(
                allowed=True, mode="live_constrained", load_state=state,
                reason="Warehouse constrained; cheap query allowed live.",
            )
        if allow_cache_fallback:
            return AdmissionDecision(
                allowed=False, mode="cache_only", load_state=state,
                reason="Warehouse constrained; standard query degraded to cache-only.",
            )
        return AdmissionDecision(
            allowed=False, mode="blocked", load_state=state,
            reason="Warehouse constrained; standard query blocked (no cache fallback).",
        )

    # DEGRADED
    if allow_cache_fallback:
        return AdmissionDecision(
            allowed=False, mode="cache_only", load_state=state,
            reason="Warehouse degraded; all live queries suspended, cache-only mode.",
        )
    return AdmissionDecision(
        allowed=False, mode="blocked", load_state=state,
        reason="Warehouse degraded; query blocked.",
    )
