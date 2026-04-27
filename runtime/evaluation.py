from __future__ import annotations

from typing import Any

from runtime.contracts import normalize_open_questions
from runtime.persistence import persist_round_bundle, read_round_bundle


ROUND_EVALUATION_REQUIRED_FIELDS = (
    "round_id",
    "round_number",
    "contract_id",
    "continuation_decision_ref",
    "hypothesis_updates",
    "residual_update",
    "residual_score",
    "residual_band",
    "open_questions",
    "continuation_guidance",
    "scores",
    "recommended_next_action",
    "should_continue",
    "stop_reason",
    "operator_gain",
    "gain_direction",
    "confidence_shift",
    "correction_mode",
    "conclusion_state",
    "incompleteness_category",
)

RECOMMENDED_NEXT_ACTIONS = {"refine", "pivot", "stop", "restart"}
GAIN_DIRECTIONS = {"positive", "flat", "negative"}
CONFIDENCE_SHIFTS = {"up", "flat", "down"}
CONCLUSION_STATES = {
    "completed",
    "partial_answer_available",
    "restart_required",
    "blocked_runtime",
}

RETENTION_FIELDS_TO_PRESERVE = (
    "result_rows",
    "result_rows_persisted",
    "retention_mode_applied",
    "sensitivity_class",
    "redaction_profile",
    "source_result_hash",
    "result_rows_purged_at",
    "retention_cleanup_status",
)
INCOMPLETENESS_CATEGORIES = {
    "",
    "warehouse_load",
    "budget_exhausted",
    "no_progress",
    "schema_gap",
    "correction_mode",
}
RESIDUAL_BANDS = {"very_high", "high", "medium", "low", "very_low"}
RESIDUAL_CONFIDENCE_BANDS = {"low", "medium", "high"}
WAREHOUSE_BURDEN_LEVELS = {"low", "medium", "high"}
USABLE_EVIDENCE_STATUSES = {"success", "cached"}


def _validate_continuation_guidance(
    guidance: Any,
    *,
    open_questions: list[dict[str, Any]],
    recommended_next_action: str,
    should_continue: bool,
) -> None:
    if not should_continue:
        if guidance is None:
            return
        if not isinstance(guidance, dict):
            raise ValueError("RoundEvaluationResult.continuation_guidance must be an object when provided.")
        return
    if not isinstance(guidance, dict):
        raise ValueError("RoundEvaluationResult.continuation_guidance must be an object when continuation is authorized.")
    required_fields = (
        "primary_residual_component",
        "priority_open_questions",
        "expected_gain_if_resolved",
        "why_continuation_is_worth_it",
        "required_transition_shape",
        "disqualified_paths",
    )
    missing = [field for field in required_fields if field not in guidance]
    if missing:
        raise ValueError(
            "RoundEvaluationResult.continuation_guidance missing required fields: "
            + ", ".join(missing)
        )
    primary_residual_component = guidance["primary_residual_component"]
    if not isinstance(primary_residual_component, str) or not primary_residual_component.strip():
        raise ValueError("RoundEvaluationResult.continuation_guidance.primary_residual_component must be a non-empty string.")
    if guidance["required_transition_shape"] != recommended_next_action:
        raise ValueError(
            "RoundEvaluationResult.continuation_guidance.required_transition_shape must match recommended_next_action when continuing."
        )
    priority_open_questions = guidance["priority_open_questions"]
    if not isinstance(priority_open_questions, list) or not priority_open_questions:
        raise ValueError("RoundEvaluationResult.continuation_guidance.priority_open_questions must be a non-empty list.")
    known_question_ids = {item["question_id"] for item in open_questions}
    for question_id in priority_open_questions:
        if not isinstance(question_id, str) or not question_id.strip():
            raise ValueError(
                "RoundEvaluationResult.continuation_guidance.priority_open_questions entries must be non-empty strings."
            )
        if question_id not in known_question_ids:
            raise ValueError(
                "RoundEvaluationResult.continuation_guidance.priority_open_questions must reference RoundEvaluationResult.open_questions."
            )
    if not isinstance(guidance["expected_gain_if_resolved"], str) or not guidance["expected_gain_if_resolved"].strip():
        raise ValueError("RoundEvaluationResult.continuation_guidance.expected_gain_if_resolved must be a non-empty string.")
    if not isinstance(guidance["why_continuation_is_worth_it"], str) or not guidance["why_continuation_is_worth_it"].strip():
        raise ValueError("RoundEvaluationResult.continuation_guidance.why_continuation_is_worth_it must be a non-empty string.")
    disqualified_paths = guidance["disqualified_paths"]
    if not isinstance(disqualified_paths, list):
        raise ValueError("RoundEvaluationResult.continuation_guidance.disqualified_paths must be a list.")
    for index, path in enumerate(disqualified_paths, start=1):
        if not isinstance(path, str) or not path.strip():
            raise ValueError(
                f"RoundEvaluationResult.continuation_guidance.disqualified_paths[{index}] must be a non-empty string."
            )


def summarize_execution_outcomes(executed_queries: list[dict[str, Any]]) -> dict[str, int]:
    """Count execution outcomes by status family for evaluator/runtime guards."""
    summary = {
        "usable": 0,
        "degraded": 0,
        "failed": 0,
        "blocked": 0,
        "success": 0,
        "cached": 0,
        "degraded_to_cache": 0,
        "timeout": 0,
        "failed_status": 0,
    }
    for query in executed_queries:
        status = query.get("status")
        if status in USABLE_EVIDENCE_STATUSES:
            summary["usable"] += 1
        elif status == "degraded_to_cache":
            summary["degraded"] += 1
            summary["degraded_to_cache"] += 1
            continue
        else:
            summary["failed"] += 1
        if status == "blocked":
            summary["blocked"] += 1
        elif status == "success":
            summary["success"] += 1
        elif status == "cached":
            summary["cached"] += 1
        elif status == "timeout":
            summary["timeout"] += 1
        elif status == "failed":
            summary["failed_status"] += 1
    return summary


def blocked_runtime_preconditions_met(executed_queries: list[dict[str, Any]]) -> bool:
    """
    blocked_runtime is legal only when no usable evidence exists and runtime
    blocking prevented execution.
    """
    if not executed_queries:
        return False
    summary = summarize_execution_outcomes(executed_queries)
    return summary["usable"] == 0 and summary["blocked"] > 0 and summary["degraded"] == 0


def validate_round_evaluation_result(
    evaluation: dict[str, Any],
    *,
    contract: dict[str, Any] | None = None,
    executed_queries: list[dict[str, Any]] | None = None,
) -> None:
    missing = [field for field in ROUND_EVALUATION_REQUIRED_FIELDS if field not in evaluation]
    if missing:
        raise ValueError(
            f"RoundEvaluationResult missing required fields: {', '.join(missing)}"
        )

    if evaluation["recommended_next_action"] not in RECOMMENDED_NEXT_ACTIONS:
        raise ValueError("RoundEvaluationResult.recommended_next_action is invalid.")
    if not isinstance(evaluation["continuation_decision_ref"], str) or not evaluation["continuation_decision_ref"]:
        raise ValueError("RoundEvaluationResult.continuation_decision_ref must be a non-empty string.")
    if evaluation["gain_direction"] not in GAIN_DIRECTIONS:
        raise ValueError("RoundEvaluationResult.gain_direction is invalid.")
    if evaluation["confidence_shift"] not in CONFIDENCE_SHIFTS:
        raise ValueError("RoundEvaluationResult.confidence_shift is invalid.")
    if evaluation["conclusion_state"] not in CONCLUSION_STATES:
        raise ValueError("RoundEvaluationResult.conclusion_state is invalid.")
    if evaluation["incompleteness_category"] not in INCOMPLETENESS_CATEGORIES:
        raise ValueError("RoundEvaluationResult.incompleteness_category is invalid.")
    if evaluation["residual_band"] not in RESIDUAL_BANDS:
        raise ValueError("RoundEvaluationResult.residual_band is invalid.")
    open_questions = normalize_open_questions(
        evaluation["open_questions"],
        label="RoundEvaluationResult.open_questions",
    )

    residual_update = evaluation["residual_update"]
    if not isinstance(residual_update, dict):
        raise ValueError("RoundEvaluationResult.residual_update must be an object.")
    if residual_update.get("confidence_band") not in RESIDUAL_CONFIDENCE_BANDS:
        raise ValueError("RoundEvaluationResult.residual_update.confidence_band is invalid.")
    for field in ("stalled_round_streak", "negative_gain_streak"):
        value = residual_update.get(field)
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"RoundEvaluationResult.residual_update.{field} must be a non-negative integer.")

    scores = evaluation["scores"]
    if not isinstance(scores, dict):
        raise ValueError("RoundEvaluationResult.scores must be an object.")
    if scores.get("warehouse_burden") not in WAREHOUSE_BURDEN_LEVELS:
        raise ValueError("RoundEvaluationResult.scores.warehouse_burden is invalid.")

    _validate_continuation_guidance(
        evaluation.get("continuation_guidance"),
        open_questions=open_questions,
        recommended_next_action=evaluation["recommended_next_action"],
        should_continue=bool(evaluation["should_continue"]),
    )

    if contract is not None:
        if evaluation["contract_id"] != contract.get("contract_id"):
            raise ValueError("RoundEvaluationResult.contract_id must match the persisted contract.")
        if evaluation["round_number"] != contract.get("round_number"):
            raise ValueError("RoundEvaluationResult.round_number must match the persisted contract.")

    if evaluation["conclusion_state"] == "blocked_runtime":
        if executed_queries is None or not blocked_runtime_preconditions_met(executed_queries):
            raise ValueError(
                "blocked_runtime requires zero usable evidence and at least one runtime-blocked query."
            )

    if evaluation["should_continue"] and evaluation["recommended_next_action"] in {"stop", "restart"}:
        raise ValueError(
            "RoundEvaluationResult.should_continue cannot be true when recommended_next_action is stop or restart."
        )
    if not evaluation["should_continue"] and evaluation["recommended_next_action"] in {"refine", "pivot"}:
        raise ValueError(
            "RoundEvaluationResult.should_continue cannot be false when recommended_next_action is refine or pivot."
        )
    if evaluation["correction_mode"] and evaluation["incompleteness_category"] not in {"", "correction_mode"}:
        raise ValueError(
            "RoundEvaluationResult.correction_mode should only use incompleteness_category '' or 'correction_mode'."
        )


def _preserve_query_retention_state(
    incoming_queries: list[dict[str, Any]],
    existing_queries: list[Any],
) -> list[dict[str, Any]]:
    existing_by_query_id = {
        query.get("query_id"): query
        for query in existing_queries
        if isinstance(query, dict) and isinstance(query.get("query_id"), str)
    }
    merged_queries: list[dict[str, Any]] = []
    for incoming in incoming_queries:
        if not isinstance(incoming, dict):
            merged_queries.append(incoming)
            continue
        merged = dict(incoming)
        query_id = merged.get("query_id")
        existing = existing_by_query_id.get(query_id)
        if not isinstance(existing, dict):
            merged_queries.append(merged)
            continue

        existing_rows_were_purged = (
            existing.get("retention_cleanup_status") == "purged_after_chart_render"
            or "result_rows_purged_at" in existing
        )
        if existing_rows_were_purged:
            for field in (
                "result_rows_persisted",
                "source_result_hash",
                "result_rows_purged_at",
                "retention_cleanup_status",
            ):
                if field in existing:
                    merged[field] = existing[field]
            merged.pop("result_rows", None)
            merged_queries.append(merged)
            continue

        existing_rows_are_retained = (
            existing.get("result_rows_persisted") is True
            and isinstance(existing.get("result_rows"), list)
        )
        incoming_rows_are_retained = (
            merged.get("result_rows_persisted") is True
            and isinstance(merged.get("result_rows"), list)
        )
        if existing_rows_are_retained and not incoming_rows_are_retained:
            for field in RETENTION_FIELDS_TO_PRESERVE:
                if field in existing:
                    merged[field] = existing[field]

        merged_queries.append(merged)
    return merged_queries


def persist_round_evaluation(
    slug: str,
    evaluation: dict[str, Any],
    *,
    contract: dict[str, Any] | None = None,
    executed_queries: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
) -> str:
    """
    Persist a validated RoundEvaluationResult into the formal round bundle.

    If contract/executed_queries are omitted, they are loaded from the existing
    round bundle identified by evaluation.round_id.
    """
    round_id = evaluation.get("round_id")
    if not isinstance(round_id, str) or not round_id:
        raise ValueError("RoundEvaluationResult.round_id must be a non-empty string.")

    existing_bundle = read_round_bundle(
        slug,
        round_id,
        session_id=session_id,
        strict_session=bool(session_id),
    )
    if contract is None and existing_bundle is not None:
        contract = existing_bundle.get("contract")
    if executed_queries is None and existing_bundle is not None:
        executed_queries = existing_bundle.get("executed_queries")

    if not isinstance(contract, dict):
        raise ValueError("persist_round_evaluation requires a contract or an existing round bundle.")
    if not isinstance(executed_queries, list):
        raise ValueError("persist_round_evaluation requires executed_queries or an existing round bundle.")
    if isinstance(existing_bundle, dict) and isinstance(existing_bundle.get("executed_queries"), list):
        executed_queries = _preserve_query_retention_state(
            executed_queries,
            existing_bundle["executed_queries"],
        )

    validate_round_evaluation_result(
        evaluation,
        contract=contract,
        executed_queries=executed_queries,
    )
    return persist_round_bundle(
        slug,
        round_id,
        contract,
        executed_queries,
        evaluation,
        session_id=session_id,
        strict_session=bool(session_id),
    )
