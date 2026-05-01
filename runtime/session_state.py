from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from runtime.contracts import normalize_open_questions, stable_payload_hash
from runtime.persistence import persist_artifact, read_artifact, read_round_bundle


SESSION_STATE_FILENAME = "session_state.json"
STAGE_SEQUENCE = (
    "intent",
    "discovery",
    "planning",
    "execution",
    "evaluation",
    "finalization",
    "chart_spec",
    "chart_render",
    "report_assembly",
    "suggestion_synthesis",
    "done",
)
RUN_STATUSES = {"running", "completed", "failed"}
STAGE_STATUSES = {"pending", "in_progress", "completed", "failed"}
SESSION_MODE_ORCHESTRATED_ONLY = "orchestrated_only"
TRANSITION_MODES = {"normal", "rework", "restart"}

STAGE_TO_ARTIFACT = {
    "intent": "intent.json",
    "discovery": "environment_scan.json",
    "planning": "plan.json",
    "finalization": "final_answer.json",
    "chart_spec": "chart_spec_bundle.json",
    "chart_render": "visualization_manifest.json",
    "report_assembly": "report.md",
    "suggestion_synthesis": "domain_pack_suggestions.json",
}


class SessionFlowError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        current_stage: str | None = None,
        required_prerequisites: list[str] | None = None,
        blocking_artifacts: list[str] | None = None,
        suggested_next_step: str | None = None,
    ) -> None:
        super().__init__(message)
        self.current_stage = current_stage
        self.required_prerequisites = required_prerequisites or []
        self.blocking_artifacts = blocking_artifacts or []
        self.suggested_next_step = suggested_next_step


class StageOrderViolation(SessionFlowError):
    pass


class MissingPrerequisiteArtifact(SessionFlowError):
    pass


class FrozenArtifactMutation(SessionFlowError):
    pass


class IllegalDirectStageEntry(SessionFlowError):
    pass


class RoundSequenceViolation(SessionFlowError):
    pass


class FinalizationPreconditionViolation(SessionFlowError):
    pass


class InvalidContinuationToken(SessionFlowError):
    pass


def _artifact_signature(payload: Any) -> str:
    return stable_payload_hash(payload)


def _stage_status_defaults() -> dict[str, str]:
    return {stage: "pending" for stage in STAGE_SEQUENCE[:-1]}


def initialize_session_state(
    slug: str,
    *,
    session_mode: str = SESSION_MODE_ORCHESTRATED_ONLY,
    session_id: str | None = None,
) -> dict[str, Any]:
    existing = read_session_state(slug, session_id=session_id)
    if existing is not None:
        return existing
    now = time.time()
    state = {
        "session_slug": slug,
        "session_mode": session_mode,
        "status": "running",
        "current_stage": "intent",
        "active_generation_id": "gen_1",
        "transition_mode": "normal",
        "stage_statuses": _stage_status_defaults(),
        "frozen_artifacts": {},
        "latest_round_number": 0,
        "continuation_tokens": {},
        "decision_refs": {},
        "protocol_warnings_count": 0,
        "strict_violation_count": 0,
        "restart_count": 0,
        "restart_history": [],
        "created_at": now,
        "updated_at": now,
    }
    persist_session_state(slug, state, session_id=session_id)
    return state


def read_session_state(slug: str, *, session_id: str | None = None) -> dict[str, Any] | None:
    state = read_artifact(slug, SESSION_STATE_FILENAME, session_id=session_id, strict_session=True)
    return state if isinstance(state, dict) else None


def persist_session_state(slug: str, state: dict[str, Any], *, session_id: str | None = None) -> str:
    state["updated_at"] = time.time()
    return persist_artifact(slug, SESSION_STATE_FILENAME, state, session_id=session_id, strict_session=True)


def require_orchestrated_entry(session_mode: str) -> None:
    if session_mode != SESSION_MODE_ORCHESTRATED_ONLY:
        raise IllegalDirectStageEntry(
            "This stage entry is orchestrator-only.",
            suggested_next_step="Use run_research_session() or an orchestrated stage helper.",
        )


def ensure_session_state(slug: str, *, session_mode: str, session_id: str | None = None) -> dict[str, Any]:
    require_orchestrated_entry(session_mode)
    return initialize_session_state(slug, session_mode=session_mode, session_id=session_id)


def _assert_stage_known(stage: str) -> None:
    if stage not in STAGE_SEQUENCE[:-1]:
        raise ValueError(f"Unknown stage: {stage}")


def assert_stage_transition(state: dict[str, Any], stage: str) -> None:
    _assert_stage_known(stage)
    current_stage = state.get("current_stage")
    if current_stage != stage:
        raise StageOrderViolation(
            f"Cannot enter stage '{stage}' while current stage is '{current_stage}'.",
            current_stage=str(current_stage),
            suggested_next_step=f"Complete stage '{current_stage}' before entering '{stage}'.",
        )


def begin_stage(slug: str, stage: str, *, session_mode: str, session_id: str | None = None) -> dict[str, Any]:
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    assert_stage_transition(state, stage)
    state["stage_statuses"][stage] = "in_progress"
    persist_session_state(slug, state, session_id=session_id)
    return state


def _next_stage(stage: str) -> str:
    index = STAGE_SEQUENCE.index(stage)
    return STAGE_SEQUENCE[index + 1]


def complete_stage(
    slug: str,
    stage: str,
    *,
    session_mode: str,
    session_id: str | None = None,
    frozen_artifact: str | None = None,
    artifact_payload: Any | None = None,
    additional_frozen_artifacts: dict[str, Any] | None = None,
    latest_round_number: int | None = None,
    allow_terminal_completion: bool = False,
    next_stage_override: str | None = None,
) -> dict[str, Any]:
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    assert_stage_transition(state, stage)
    state["stage_statuses"][stage] = "completed"
    if frozen_artifact is not None and artifact_payload is not None:
        state["frozen_artifacts"][frozen_artifact] = _artifact_signature(artifact_payload)
    for artifact_name, payload in (additional_frozen_artifacts or {}).items():
        state["frozen_artifacts"][artifact_name] = _artifact_signature(payload)
    if latest_round_number is not None:
        state["latest_round_number"] = latest_round_number
    if stage == "finalization" and allow_terminal_completion:
        state["status"] = "completed"
    state["transition_mode"] = "normal"
    next_stage = next_stage_override or _next_stage(stage)
    state["current_stage"] = next_stage
    if next_stage == "done":
        state["status"] = "completed"
    persist_session_state(slug, state, session_id=session_id)
    return state


def fail_stage(slug: str, stage: str, *, session_mode: str, session_id: str | None = None) -> dict[str, Any]:
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    state["stage_statuses"][stage] = "failed"
    state["status"] = "failed"
    state["current_stage"] = stage
    persist_session_state(slug, state, session_id=session_id)
    return state


def mark_session_complete(
    slug: str,
    *,
    session_mode: str,
    session_id: str | None = None,
    current_stage: str = "done",
) -> dict[str, Any]:
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    state["status"] = "completed"
    state["current_stage"] = current_stage
    persist_session_state(slug, state, session_id=session_id)
    return state


def guard_frozen_artifact(state: dict[str, Any], artifact_name: str, payload: Any) -> None:
    existing_signature = state.get("frozen_artifacts", {}).get(artifact_name)
    if existing_signature is None:
        return
    new_signature = _artifact_signature(payload)
    if new_signature != existing_signature:
        raise FrozenArtifactMutation(
            f"{artifact_name} is frozen and cannot be mutated in place.",
            current_stage=str(state.get("current_stage")),
            blocking_artifacts=[artifact_name],
            suggested_next_step="Create a restart flow instead of modifying the frozen artifact.",
        )


def require_artifact(
    slug: str,
    artifact_name: str,
    *,
    current_stage: str,
    suggested_next_step: str,
    session_id: str | None = None,
) -> Any:
    artifact = read_artifact(slug, artifact_name, session_id=session_id, strict_session=True)
    if artifact is None:
        raise MissingPrerequisiteArtifact(
            f"Missing prerequisite artifact: {artifact_name}",
            current_stage=current_stage,
            required_prerequisites=[artifact_name],
            blocking_artifacts=[artifact_name],
            suggested_next_step=suggested_next_step,
        )
    return artifact


def require_intent_ready(slug: str, state: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    return require_artifact(
        slug,
        "intent.json",
        current_stage=str(state.get("current_stage")),
        suggested_next_step="Persist Stage 1 intent output first.",
        session_id=session_id,
    )


def require_discovery_ready(slug: str, state: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    return require_artifact(
        slug,
        "environment_scan.json",
        current_stage=str(state.get("current_stage")),
        suggested_next_step="Persist Stage 2 discovery output first.",
        session_id=session_id,
    )


def require_plan_ready(slug: str, state: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    return require_artifact(
        slug,
        "plan.json",
        current_stage=str(state.get("current_stage")),
        suggested_next_step="Persist Stage 3 plan output first.",
        session_id=session_id,
    )


def require_round_execution_ready(
    slug: str,
    state: dict[str, Any],
    round_number: int,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    round_id = f"round_{round_number}"
    bundle = read_round_bundle(slug, round_id, session_id=session_id, strict_session=True)
    if bundle is None:
        raise MissingPrerequisiteArtifact(
            f"Missing prerequisite artifact: rounds/{round_id}.json",
            current_stage=str(state.get("current_stage")),
            required_prerequisites=[f"rounds/{round_id}.json"],
            blocking_artifacts=[f"rounds/{round_id}.json"],
            suggested_next_step=f"Persist {round_id} execution bundle first.",
        )
    if not isinstance(bundle, dict) or "contract" not in bundle or "executed_queries" not in bundle:
        raise MissingPrerequisiteArtifact(
            f"{round_id} is missing contract or executed_queries.",
            current_stage=str(state.get("current_stage")),
            blocking_artifacts=[f"rounds/{round_id}.json"],
            suggested_next_step=f"Persist {round_id} execution bundle first.",
        )
    return bundle


def require_evaluation_ready(slug: str, state: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    round_number = int(state.get("latest_round_number", 0))
    if round_number <= 0:
        raise FinalizationPreconditionViolation(
            "No round evaluation is available for finalization.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step="Persist at least one round evaluation first.",
        )
    round_id = f"round_{round_number}"
    bundle = read_round_bundle(slug, round_id, session_id=session_id, strict_session=True)
    if bundle is None:
        raise FinalizationPreconditionViolation(
            f"Missing prerequisite artifact: rounds/{round_id}.json",
            current_stage=str(state.get("current_stage")),
            blocking_artifacts=[f"rounds/{round_id}.json"],
            suggested_next_step=f"Persist {round_id} evaluation first.",
        )
    evaluation = bundle.get("evaluation") if isinstance(bundle, dict) else None
    if not isinstance(evaluation, dict) or "round_id" not in evaluation or "conclusion_state" not in evaluation:
        raise FinalizationPreconditionViolation(
            f"{round_id} does not contain a valid evaluation.",
            current_stage=str(state.get("current_stage")),
            blocking_artifacts=[f"rounds/{round_id}.json"],
            suggested_next_step=f"Persist {round_id} evaluation first.",
        )
    if evaluation.get("recommended_next_action") == "restart" or evaluation.get("conclusion_state") == "restart_required":
        raise FinalizationPreconditionViolation(
            "Latest round evaluation requires restart; finalization is not legal for the current frozen intent.",
            current_stage=str(state.get("current_stage")),
            blocking_artifacts=[f"rounds/{round_id}.json"],
            suggested_next_step="Restart from intent and preserve the prior intent lineage instead of finalizing.",
        )
    return evaluation


def require_finalization_ready(slug: str, state: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    final_answer = require_artifact(
        slug,
        "final_answer.json",
        current_stage=str(state.get("current_stage")),
        suggested_next_step="Persist the final answer before generating chart specs.",
        session_id=session_id,
    )
    require_artifact(
        slug,
        "report_evidence.json",
        current_stage=str(state.get("current_stage")),
        suggested_next_step="Persist report_evidence.json before generating chart specs.",
        session_id=session_id,
    )
    return final_answer


def require_chart_spec_ready(slug: str, state: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    spec_bundle = require_artifact(
        slug,
        "chart_spec_bundle.json",
        current_stage=str(state.get("current_stage")),
        suggested_next_step="Persist chart_spec_bundle.json before rendering charts.",
        session_id=session_id,
    )
    if not isinstance(spec_bundle, dict):
        raise MissingPrerequisiteArtifact(
            "chart_spec_bundle.json must be an object.",
            current_stage=str(state.get("current_stage")),
            blocking_artifacts=["chart_spec_bundle.json"],
            suggested_next_step="Persist a valid chart_spec_bundle.json before continuing.",
        )
    return spec_bundle


def require_chart_render_ready(slug: str, state: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    manifest = require_artifact(
        slug,
        "visualization_manifest.json",
        current_stage=str(state.get("current_stage")),
        suggested_next_step="Persist chart-render artifacts before assembling the report.",
        session_id=session_id,
    )
    descriptive_stats = require_artifact(
        slug,
        "descriptive_stats.json",
        current_stage=str(state.get("current_stage")),
        suggested_next_step="Persist descriptive_stats.json before assembling the report.",
        session_id=session_id,
    )
    if not isinstance(manifest, dict):
        raise MissingPrerequisiteArtifact(
            "visualization_manifest.json must be an object.",
            current_stage=str(state.get("current_stage")),
            blocking_artifacts=["visualization_manifest.json"],
            suggested_next_step="Persist a valid visualization manifest before continuing.",
        )
    if not isinstance(descriptive_stats, dict):
        raise MissingPrerequisiteArtifact(
            "descriptive_stats.json must be an object.",
            current_stage=str(state.get("current_stage")),
            blocking_artifacts=["descriptive_stats.json"],
            suggested_next_step="Persist a valid descriptive_stats.json before continuing.",
        )
    return manifest


def require_report_assembly_ready(slug: str, state: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    manifest = require_chart_render_ready(slug, state, session_id=session_id)
    report = require_artifact(
        slug,
        "report.md",
        current_stage=str(state.get("current_stage")),
        suggested_next_step="Persist report.md before synthesizing pack suggestions.",
        session_id=session_id,
    )
    if not isinstance(report, str):
        raise MissingPrerequisiteArtifact(
            "report.md must be a markdown string artifact.",
            current_stage=str(state.get("current_stage")),
            blocking_artifacts=["report.md"],
            suggested_next_step="Persist report.md before continuing.",
        )
    return manifest


def assert_round_sequence(state: dict[str, Any], round_number: int) -> None:
    latest_round_number = int(state.get("latest_round_number", 0))
    expected_round_number = latest_round_number + 1
    if round_number != expected_round_number:
        raise RoundSequenceViolation(
            f"Round number {round_number} is invalid; expected {expected_round_number}.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step=f"Submit round {expected_round_number} next.",
        )


def set_transition_mode(
    slug: str,
    mode: str,
    *,
    session_mode: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    if mode not in TRANSITION_MODES:
        raise ValueError(f"Unsupported transition_mode: {mode}")
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    state["transition_mode"] = mode
    persist_session_state(slug, state, session_id=session_id)
    return state


def append_decision_ref(
    slug: str,
    stage: str,
    decision_ref: str,
    *,
    session_mode: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    refs = state.setdefault("decision_refs", {})
    stage_refs = refs.setdefault(stage, [])
    stage_refs.append(decision_ref)
    refs[stage] = stage_refs
    state["decision_refs"] = refs
    persist_session_state(slug, state, session_id=session_id)
    return state


def register_protocol_violation(
    slug: str,
    severity: str,
    *,
    session_mode: str,
    session_id: str | None = None,
    increment: int = 1,
) -> dict[str, Any]:
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    if severity == "strict_violation":
        state["strict_violation_count"] = int(state.get("strict_violation_count", 0)) + increment
    else:
        state["protocol_warnings_count"] = int(state.get("protocol_warnings_count", 0)) + increment
    persist_session_state(slug, state, session_id=session_id)
    return state


def issue_continuation_token(
    slug: str,
    *,
    session_mode: str,
    session_id: str | None = None,
    evaluation: dict[str, Any],
    hypothesis_state_basis: str,
    allowed_target_hypotheses: list[str],
    hypothesis_status_advisory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    if not evaluation.get("should_continue"):
        return {}
    recommended_next_action = evaluation.get("recommended_next_action")
    if recommended_next_action not in {"refine", "pivot"}:
        return {}
    next_round_number = int(evaluation["round_number"]) + 1
    continuation_guidance = evaluation.get("continuation_guidance", {})
    open_questions = normalize_open_questions(
        evaluation.get("open_questions", []),
        label="RoundEvaluationResult.open_questions",
    )
    priority_open_questions = continuation_guidance.get("priority_open_questions", [])
    if not isinstance(priority_open_questions, list) or not priority_open_questions:
        priority_open_questions = [item["question_id"] for item in open_questions]
    token_payload = {
        "token": secrets.token_urlsafe(18),
        "issued_from_round_number": int(evaluation["round_number"]),
        "issued_from_round_id": str(evaluation["round_id"]),
        "recommended_next_action": recommended_next_action,
        "allowed_next_round_number": next_round_number,
        "allowed_transition_type": recommended_next_action,
        "hypothesis_state_basis": hypothesis_state_basis,
        "allowed_target_hypotheses": allowed_target_hypotheses,
        "hypothesis_status_advisory": dict(hypothesis_status_advisory or {}),
        "authorized_residual_component": continuation_guidance.get("primary_residual_component"),
        "authorized_open_question_ids": priority_open_questions,
        "issued_at": time.time(),
        "consumed": False,
    }
    state.setdefault("continuation_tokens", {})[str(next_round_number)] = token_payload
    persist_session_state(slug, state, session_id=session_id)
    return token_payload


def get_continuation_token(state: dict[str, Any], round_number: int) -> dict[str, Any] | None:
    continuation_tokens = state.get("continuation_tokens", {})
    token_payload = continuation_tokens.get(str(round_number))
    return token_payload if isinstance(token_payload, dict) else None


def consume_continuation_token(
    slug: str,
    round_number: int,
    *,
    session_mode: str,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    token_payload = get_continuation_token(state, round_number)
    if token_payload is None:
        return None
    token_payload["consumed"] = True
    state.setdefault("continuation_tokens", {})[str(round_number)] = token_payload
    persist_session_state(slug, state, session_id=session_id)
    return token_payload


def mark_restart(
    slug: str,
    *,
    session_mode: str,
    session_id: str | None = None,
    reason: str | None = None,
    prior_intent_hash: str | None = None,
    triggering_generation_id: str | None = None,
    triggering_round_number: int | None = None,
    triggering_round_id: str | None = None,
    triggering_evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    prior_generation_id = str(state.get("active_generation_id", "gen_1"))
    next_generation_id = f"gen_{int(state.get('restart_count', 0)) + 2}"
    history_entry = {
        "reason": reason or "restart_requested",
        "prior_intent_hash": prior_intent_hash,
        "from_generation_id": prior_generation_id,
        "to_generation_id": next_generation_id,
        "timestamp": time.time(),
    }
    if triggering_generation_id:
        history_entry["triggering_generation_id"] = triggering_generation_id
    if triggering_round_number is not None:
        history_entry["triggering_round_number"] = triggering_round_number
    if triggering_round_id:
        history_entry["triggering_round_id"] = triggering_round_id
    if isinstance(triggering_evaluation, dict):
        history_entry["triggering_evaluation_hash"] = _artifact_signature(triggering_evaluation)
        history_entry["triggering_evaluation"] = dict(triggering_evaluation)
    state.setdefault("restart_history", []).append(history_entry)
    state["restart_count"] = int(state.get("restart_count", 0)) + 1
    state["active_generation_id"] = next_generation_id
    state["current_stage"] = "intent"
    state["transition_mode"] = "restart"
    state["stage_statuses"] = _stage_status_defaults()
    state["latest_round_number"] = 0
    state["frozen_artifacts"] = {}
    state["continuation_tokens"] = {}
    state["status"] = "running"
    persist_session_state(slug, state, session_id=session_id)
    return state
