from __future__ import annotations

import inspect
import time
from typing import Any, Callable

from runtime.compliance import (
    CHOSEN_SKILL,
    PROTOCOL_MODE,
    append_action_rationale,
    append_protocol_gate_result,
    append_stage_decision,
    append_tool_usage_envelope,
    run_protocol_audit,
)
from runtime.contracts import (
    normalize_open_questions,
    validate_chart_spec_bundle,
    stable_payload_hash,
    validate_data_context_bundle,
    validate_descriptive_stats_bundle,
    validate_intent_recognition_result,
    validate_investigation_contract,
    validate_plan_bundle,
    validate_report_evidence_bundle,
    validate_report_evidence_index,
    validate_visualization_manifest,
)
from runtime.domain_pack_suggestions import persist_domain_pack_suggestions
from runtime.evaluation import persist_round_evaluation, validate_round_evaluation_result
from runtime.final_answer import get_latest_round_evaluation, persist_final_answer
from runtime.final_answer import validate_final_answer
from runtime.orchestration import execute_evidence_contract
from runtime.protocol_guards import (
    configure_semantic_guard_policy,
    validate_chart_spec_stage_payload,
    validate_discovery_stage_payload,
    validate_evaluation_stage_payload,
    validate_execution_stage_payload,
    validate_finalization_stage_payload,
    validate_intent_ready_for_downstream,
    validate_intent_stage_payload,
    validate_plan_stage_payload,
)
from runtime.persistence import (
    get_active_generation_id,
    list_round_bundles,
    load_session_evidence,
    persist_artifact,
    persist_manifest,
    persist_round_bundle,
    read_artifact,
    read_round_bundle,
    start_session,
)
from runtime.session_state import (
    SESSION_MODE_ORCHESTRATED_ONLY,
    assert_round_sequence,
    begin_stage,
    complete_stage,
    consume_continuation_token,
    ensure_session_state,
    append_decision_ref,
    fail_stage,
    guard_frozen_artifact,
    get_continuation_token,
    issue_continuation_token,
    InvalidContinuationToken,
    mark_restart,
    mark_session_complete,
    register_protocol_violation,
    read_session_state,
    require_discovery_ready,
    require_evaluation_ready,
    FinalizationPreconditionViolation,
    require_finalization_ready,
    require_intent_ready,
    require_orchestrated_entry,
    require_plan_ready,
    require_round_execution_ready,
    require_chart_spec_ready,
    require_chart_render_ready,
    require_report_assembly_ready,
    set_transition_mode,
)
from runtime.visualization import (
    assemble_report_artifacts,
    compile_chart_specs_from_affordance_plan,
    persist_chart_affordance_bundle,
    render_chart_artifacts,
)
from runtime.visualization import set_report_template
from runtime.visualization_capabilities import get_visualization_capabilities
from runtime.web_search import (
    WebSearchClient,
    get_web_search_configuration_status,
    resolve_default_web_client,
)


def _stage_goal(stage: str) -> str:
    goals = {
        "intent": "Freeze the normalized research intent for downstream stages.",
        "discovery": "Map schema and evidence availability without promoting business claims.",
        "planning": "Produce a bounded investigation contract for the next executable round.",
        "execution": "Execute only the explicit investigation contract and retain evidence lineage.",
        "evaluation": "Assess round evidence, update residual state, and decide the next move.",
        "finalization": "Persist the evidence-backed final answer and explicit report evidence bundle.",
        "chart_spec": "Persist runtime-compiled chart specs from chart-ready evidence affordances.",
        "chart_render": "Validate chart specs, resolve render modes, render charts, and persist plot-data lineage.",
        "report_assembly": "Assemble the final human-readable markdown report from persisted evidence and rendered charts.",
        "suggestion_synthesis": "Produce best-effort pack suggestions after the session has ended.",
    }
    return goals.get(stage, stage)


def _call_producer(producer: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """Call host producers without forcing older callbacks to accept new web kwargs."""
    signature = inspect.signature(producer)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return producer(**kwargs)
    accepted = {
        key: value
        for key, value in kwargs.items()
        if key in signature.parameters
    }
    return producer(**accepted)


def _default_completion_criteria(stage: str) -> list[str]:
    criteria = {
        "intent": ["intent.json and intent_sidecar.json persisted"],
        "discovery": ["environment_scan.json persisted"],
        "planning": ["plan.json persisted"],
        "execution": ["round bundle persisted with contract and executed evidence lanes"],
        "evaluation": ["round evaluation persisted and next transition determined"],
        "finalization": ["final_answer.json persisted", "report_evidence.json persisted", "report_evidence_index.json persisted"],
        "chart_spec": ["chart_spec_bundle.json persisted"],
        "chart_render": [
            "descriptive_stats.json persisted",
            "visualization_manifest.json persisted",
            "charts/*.png persisted",
            "charts/*.plot-data.json persisted",
        ],
        "report_assembly": [
            "report.md persisted",
            "compliance_report.json refreshed",
        ],
        "suggestion_synthesis": ["domain_pack_suggestions.json persisted when applicable"],
    }
    return criteria.get(stage, ["artifact persisted"])


def _record_stage_decision(
    slug: str,
    *,
    stage: str,
    phase: str,
    next_stage: str,
    session_mode: str,
    transition_mode: str = "normal",
    completion_criteria: list[str] | None = None,
    note: str | None = None,
    decision_ref: str | None = None,
    session_id: str | None = None,
) -> str:
    decision_ref = append_stage_decision(
        slug,
        {
            "decision_ref": decision_ref or "",
            "stage": stage,
            "phase": phase,
            "goal": note or _stage_goal(stage),
            "completion_criteria": completion_criteria or _default_completion_criteria(stage),
            "transition_mode": transition_mode,
            "next_stage": next_stage,
            "timestamp": time.time(),
        },
        session_id=session_id,
    )
    append_decision_ref(slug, stage, decision_ref, session_mode=session_mode, session_id=session_id)
    return decision_ref


def _autofill_action_rationale(
    *,
    current_stage: str,
    action_type: str,
    purpose: str,
    expected_output_type: str,
    artifact_impact: list[str],
    why_not_a_later_stage_claim: str,
) -> dict[str, Any]:
    return {
        "action_ref": "",
        "current_stage": current_stage,
        "action_type": action_type,
        "purpose": purpose,
        "expected_output_type": expected_output_type,
        "artifact_impact": artifact_impact,
        "why_not_a_later_stage_claim": why_not_a_later_stage_claim,
        "timestamp": time.time(),
    }


def _next_stage_from_evaluation(evaluation_result: dict[str, Any]) -> str:
    recommended_next_action = evaluation_result.get("recommended_next_action")
    if evaluation_result.get("should_continue") and recommended_next_action in {"refine", "pivot"}:
        return "execution"
    if recommended_next_action == "restart":
        return "intent"
    return "finalization"


def _iter_query_refs(items: list[Any], *, section: str, default_reason: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        entry_section = item.get("section") if isinstance(item.get("section"), str) and item.get("section") else section
        reason = item.get("reason") if isinstance(item.get("reason"), str) and item.get("reason") else default_reason
        for query_ref in item.get("query_refs", []):
            if not isinstance(query_ref, dict):
                continue
            round_id = query_ref.get("round_id")
            query_id = query_ref.get("query_id")
            if isinstance(round_id, str) and round_id and isinstance(query_id, str) and query_id:
                refs.append(
                    {
                        "section": entry_section,
                        "round_id": round_id,
                        "query_id": query_id,
                        "reason": reason,
                    }
                )
    return refs


def _iter_web_refs(items: list[Any], *, section: str, default_reason: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        entry_section = item.get("section") if isinstance(item.get("section"), str) and item.get("section") else section
        reason = item.get("reason") if isinstance(item.get("reason"), str) and item.get("reason") else default_reason
        for web_ref in item.get("web_refs", []):
            if not isinstance(web_ref, dict):
                continue
            round_id = web_ref.get("round_id")
            search_id = web_ref.get("search_id")
            if isinstance(round_id, str) and round_id and isinstance(search_id, str) and search_id:
                refs.append(
                    {
                        "section": entry_section,
                        "round_id": round_id,
                        "search_id": search_id,
                        "reason": reason,
                    }
                )
    return refs


def _build_report_evidence_index(
    slug: str,
    report_evidence: dict[str, Any],
    *,
    session_id: str,
) -> dict[str, Any]:
    report_evidence_refs: list[dict[str, str]] = []
    entries = report_evidence.get("entries") if isinstance(report_evidence, dict) else None
    if isinstance(entries, list):
        report_evidence_refs.extend(
            _iter_query_refs(
                entries,
                section="supported_claims",
                default_reason="supports_final_claim",
            )
        )
    web_evidence_refs: list[dict[str, str]] = []
    if isinstance(entries, list):
        web_evidence_refs.extend(
            _iter_web_refs(
                entries,
                section="supported_claims",
                default_reason="supports_final_claim",
            )
        )
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in report_evidence_refs:
        key = (item["section"], item["round_id"], item["query_id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    deduped_web: list[dict[str, str]] = []
    seen_web: set[tuple[str, str, str]] = set()
    for item in web_evidence_refs:
        key = (item["section"], item["round_id"], item["search_id"])
        if key in seen_web:
            continue
        seen_web.add(key)
        deduped_web.append(item)
    index = {
        "session_slug": slug,
        "session_id": session_id,
        "report_evidence_refs": deduped,
        "web_evidence_refs": deduped_web,
        "generated_at": time.time(),
    }
    validate_report_evidence_index(index)
    return index


def _validate_report_evidence_for_session(
    slug: str,
    report_evidence: dict[str, Any],
    *,
    final_answer: dict[str, Any],
    session_id: str | None = None,
) -> None:
    validate_report_evidence_bundle(report_evidence)
    if report_evidence.get("session_slug") != slug:
        raise ValueError("ReportEvidenceBundle.session_slug must match the active session slug.")
    if session_id is not None and report_evidence.get("session_id") != session_id:
        raise ValueError("ReportEvidenceBundle.session_id must match the active session id.")

    active_generation_id = get_active_generation_id(
        slug,
        session_id=session_id,
        strict_session=bool(session_id),
    )
    known_query_refs: set[tuple[str, str]] = set()
    known_web_refs: set[tuple[str, str]] = set()
    known_evaluation_refs: set[str] = set()
    for bundle in list_round_bundles(
        slug,
        generation_id=active_generation_id,
        session_id=session_id,
        strict_session=bool(session_id),
    ):
        if not isinstance(bundle, dict):
            continue
        evaluation = bundle.get("evaluation")
        round_id = evaluation.get("round_id") if isinstance(evaluation, dict) else None
        if isinstance(round_id, str) and round_id:
            known_evaluation_refs.add(f"{round_id}:evaluation")
        for query in bundle.get("executed_queries", []):
            if not isinstance(query, dict):
                continue
            query_id = query.get("query_id")
            if isinstance(round_id, str) and isinstance(query_id, str) and round_id and query_id:
                known_query_refs.add((round_id, query_id))
        for search in bundle.get("executed_web_searches", []):
            if not isinstance(search, dict):
                continue
            search_id = search.get("search_id")
            if isinstance(round_id, str) and isinstance(search_id, str) and round_id and search_id:
                known_web_refs.add((round_id, search_id))

    evidence_entries: list[dict[str, Any]] = []
    for entry in report_evidence.get("entries", []):
        if not isinstance(entry, dict):
            continue
        query_refs = entry.get("query_refs", [])
        if isinstance(query_refs, list):
            for query_ref in query_refs:
                if not isinstance(query_ref, dict):
                    continue
                query_key = (query_ref.get("round_id"), query_ref.get("query_id"))
                if query_key not in known_query_refs:
                    raise ValueError(
                        "ReportEvidenceBundle references unknown query lineage."
                    )
        web_refs = entry.get("web_refs", [])
        if isinstance(web_refs, list):
            for web_ref in web_refs:
                if not isinstance(web_ref, dict):
                    continue
                web_key = (web_ref.get("round_id"), web_ref.get("search_id"))
                if web_key not in known_web_refs:
                    raise ValueError(
                        "ReportEvidenceBundle references unknown web search lineage."
                    )
        evaluation_refs = entry.get("evaluation_refs", [])
        if isinstance(evaluation_refs, list):
            for evaluation_ref in evaluation_refs:
                if evaluation_ref not in known_evaluation_refs:
                    raise ValueError(
                        "ReportEvidenceBundle references unknown evaluation lineage."
                    )
        evidence_entries.append(entry)

    supported_claims = final_answer.get("supported_claims", [])
    if supported_claims and not evidence_entries:
        raise ValueError(
            "ReportEvidenceBundle must include at least one lineage-bearing entry when FinalAnswer.supported_claims is non-empty."
        )

    evidence_entry_refs = []
    for entry in evidence_entries:
        entry_query_refs = {
            (item.get("round_id"), item.get("query_id"))
            for item in entry.get("query_refs", [])
            if isinstance(item, dict)
        }
        entry_web_refs = {
            (item.get("round_id"), item.get("search_id"))
            for item in entry.get("web_refs", [])
            if isinstance(item, dict)
        }
        entry_evaluation_refs = {
            item
            for item in entry.get("evaluation_refs", [])
            if isinstance(item, str) and item
        }
        evidence_entry_refs.append((entry_query_refs, entry_web_refs, entry_evaluation_refs))

    for claim in supported_claims:
        if not isinstance(claim, dict):
            continue
        claim_query_refs = {
            (item.get("round_id"), item.get("query_id"))
            for item in claim.get("query_refs", [])
            if isinstance(item, dict)
        }
        claim_web_refs = {
            (item.get("round_id"), item.get("search_id"))
            for item in claim.get("web_refs", [])
            if isinstance(item, dict)
        }
        claim_evaluation_refs = {
            item
            for item in claim.get("evaluation_refs", [])
            if isinstance(item, str) and item
        }
        if not any(
            claim_query_refs & entry_query_refs
            or claim_web_refs & entry_web_refs
            or claim_evaluation_refs & entry_evaluation_refs
            for entry_query_refs, entry_web_refs, entry_evaluation_refs in evidence_entry_refs
        ):
            raise ValueError(
                "Each FinalAnswer.supported_claim must be backed by at least one report evidence entry."
            )


def _read_effective_hypothesis_state(slug: str, *, session_id: str | None = None) -> dict[str, dict[str, Any]]:
    plan_bundle = read_artifact(slug, "plan.json", session_id=session_id, strict_session=True)
    if not isinstance(plan_bundle, dict):
        return {}
    hypothesis_board = plan_bundle.get("hypothesis_board", [])
    effective_state: dict[str, dict[str, Any]] = {}
    if isinstance(hypothesis_board, list):
        for item in hypothesis_board:
            if isinstance(item, dict) and isinstance(item.get("hypothesis_id"), str):
                effective_state[item["hypothesis_id"]] = dict(item)
    state = read_session_state(slug, session_id=session_id) or {}
    latest_round_number = int(state.get("latest_round_number", 0))
    for round_number in range(1, latest_round_number + 1):
        bundle = read_round_bundle(
            slug,
            f"round_{round_number}",
            session_id=session_id,
            strict_session=True,
        ) or {}
        evaluation = bundle.get("evaluation")
        if not isinstance(evaluation, dict):
            continue
        updates = evaluation.get("hypothesis_updates", [])
        if not isinstance(updates, list):
            continue
        for update in updates:
            if not isinstance(update, dict):
                continue
            hypothesis_id = update.get("hypothesis_id")
            if not isinstance(hypothesis_id, str):
                continue
            snapshot = effective_state.get(hypothesis_id, {"hypothesis_id": hypothesis_id})
            snapshot.update(update)
            effective_state[hypothesis_id] = snapshot
    return effective_state


def _legal_target_hypotheses(slug: str, *, session_id: str | None = None) -> list[str]:
    effective_state = _read_effective_hypothesis_state(slug, session_id=session_id)
    blocked_statuses = {"rejected", "not_tested"}
    return sorted(
        hypothesis_id
        for hypothesis_id, snapshot in effective_state.items()
        if snapshot.get("status") not in blocked_statuses
    )


def _validate_round_2_plus_lineage(
    slug: str,
    state: dict[str, Any],
    contract: dict[str, Any],
    *,
    session_id: str | None = None,
) -> None:
    round_number = int(contract["round_number"])
    if round_number <= 1:
        return
    token_payload = get_continuation_token(state, round_number)
    if token_payload is None:
        raise InvalidContinuationToken(
            f"No continuation token is available for round {round_number}.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step=f"Complete round {round_number - 1} evaluation and obtain a continuation token first.",
        )
    if token_payload.get("consumed"):
        raise InvalidContinuationToken(
            f"The continuation token for round {round_number} has already been consumed.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step="Request a fresh continuation from the latest evaluation state.",
        )
    latest_round_number = int(state.get("latest_round_number", 0))
    latest_bundle = read_round_bundle(
        slug,
        f"round_{latest_round_number}",
        session_id=session_id,
        strict_session=True,
    ) or {}
    latest_evaluation = latest_bundle.get("evaluation")
    if not isinstance(latest_evaluation, dict):
        raise InvalidContinuationToken(
            "Latest round evaluation is missing; cannot validate continuation lineage.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step="Persist the latest round evaluation first.",
        )
    if latest_evaluation.get("should_continue") is not True:
        raise InvalidContinuationToken(
            f"Latest evaluation does not authorize continuation; recommended_next_action={latest_evaluation.get('recommended_next_action')!r}.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step="Respect the stop/restart decision or restart the session.",
        )
    if contract.get("continuation_token") != token_payload.get("token"):
        raise InvalidContinuationToken(
            "Continuation token does not match the runtime-issued authorization for this round.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step=f"Use the continuation token issued for round {round_number}.",
        )
    if contract.get("parent_round_id") != token_payload.get("issued_from_round_id"):
        raise InvalidContinuationToken(
            "parent_round_id does not match the latest authorized source round.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step=f"Anchor the contract to {token_payload.get('issued_from_round_id')}.",
        )
    if contract.get("parent_evaluation_round_number") != token_payload.get("issued_from_round_number"):
        raise InvalidContinuationToken(
            "parent_evaluation_round_number does not match the latest authorized source round number.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step=f"Anchor the contract to round {token_payload.get('issued_from_round_number')}.",
        )
    if contract.get("continuation_basis", {}).get("from_recommended_next_action") != token_payload.get("recommended_next_action"):
        raise InvalidContinuationToken(
            "Contract continuation basis does not match the runtime-issued transition type.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step=f"Use transition type {token_payload.get('recommended_next_action')!r}.",
        )
    continuation_guidance = latest_evaluation.get("continuation_guidance")
    if not isinstance(continuation_guidance, dict):
        raise InvalidContinuationToken(
            "Latest evaluation is missing continuation_guidance for an authorized continuation.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step="Persist continuation_guidance before generating the next contract.",
        )
    continuation_basis = contract.get("continuation_basis", {})
    if continuation_basis.get("from_round") != latest_round_number:
        raise InvalidContinuationToken(
            "continuation_basis.from_round must point at the latest evaluated round.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step=f"Set continuation_basis.from_round to {latest_round_number}.",
        )
    authorized_residual_component = token_payload.get("authorized_residual_component")
    if authorized_residual_component and continuation_basis.get("target_residual_component") != authorized_residual_component:
        raise InvalidContinuationToken(
            "Contract target_residual_component does not match the latest evaluation's continuation guidance.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step=f"Use target_residual_component {authorized_residual_component!r}.",
        )
    target_open_question_ids = continuation_basis.get("target_open_question_ids", [])
    if not isinstance(target_open_question_ids, list) or not target_open_question_ids:
        raise InvalidContinuationToken(
            "Contract target_open_question_ids must be a non-empty list for round continuation.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step="Bind the next round to one or more prioritized open questions.",
        )
    authorized_open_question_ids = set(token_payload.get("authorized_open_question_ids", []))
    if authorized_open_question_ids and any(question_id not in authorized_open_question_ids for question_id in target_open_question_ids):
        raise InvalidContinuationToken(
            "Contract target_open_question_ids are not authorized by the latest continuation guidance.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step="Use prioritized open questions from the latest evaluation.",
        )
    normalized_open_questions = normalize_open_questions(
        latest_evaluation.get("open_questions", []),
        label="RoundEvaluationResult.open_questions",
    )
    known_open_question_ids = {item["question_id"] for item in normalized_open_questions}
    if any(question_id not in known_open_question_ids for question_id in target_open_question_ids):
        raise InvalidContinuationToken(
            "Contract target_open_question_ids must reference the latest evaluation open_questions.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step="Anchor the next round to persisted open question ids.",
        )
    intent = read_artifact(slug, "intent.json", session_id=session_id, strict_session=True)
    plan = read_artifact(slug, "plan.json", session_id=session_id, strict_session=True)
    if contract.get("session_slug") != slug:
        raise InvalidContinuationToken("Contract session_slug does not match the active session slug.")
    if isinstance(intent, dict) and contract.get("intent_id") != intent.get("intent_id"):
        raise InvalidContinuationToken("Contract intent_id does not match the frozen NormalizedIntent.")
    if contract.get("intent_hash") != stable_payload_hash(intent):
        raise InvalidContinuationToken("Contract intent_hash does not match the frozen intent artifact.")
    if contract.get("plan_hash") != stable_payload_hash(plan):
        raise InvalidContinuationToken("Contract plan_hash does not match the frozen plan artifact.")
    if contract.get("hypothesis_state_basis") != token_payload.get("hypothesis_state_basis"):
        raise InvalidContinuationToken(
            "Contract hypothesis_state_basis does not match the runtime-issued hypothesis board snapshot.",
        )
    allowed_target_hypotheses = set(token_payload.get("allowed_target_hypotheses", []))
    target_hypotheses = contract.get("target_hypotheses", [])
    if not isinstance(target_hypotheses, list) or any(hypothesis_id not in allowed_target_hypotheses for hypothesis_id in target_hypotheses):
        raise InvalidContinuationToken(
            "Contract target_hypotheses are not legal under the authorized continuation snapshot.",
        )
    parent_contract = latest_bundle.get("contract")
    if not isinstance(parent_contract, dict):
        raise InvalidContinuationToken(
            "Latest round contract is missing; cannot validate structural continuation changes.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step="Persist the parent round contract before continuing.",
        )
    operator_changed = contract.get("operator_id") != parent_contract.get("operator_id")
    targets_changed = contract.get("target_hypotheses") != parent_contract.get("target_hypotheses")
    parent_queries = parent_contract.get("queries", [])
    current_queries = contract.get("queries", [])
    queries_changed = current_queries != parent_queries
    changed_axis_count = sum((operator_changed, targets_changed, queries_changed))
    if changed_axis_count == 0:
        raise InvalidContinuationToken(
            "Round continuation must not repeat the parent contract unchanged.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step="Revise the next contract so it materially targets the authorized residual.",
        )
    material_change_reason = contract.get("material_change_reason")
    if not isinstance(material_change_reason, dict):
        raise InvalidContinuationToken(
            "Round continuation must include material_change_reason.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step="Explain the changed axes, why the change is material, and how it reduces residual uncertainty.",
        )
    if changed_axis_count < 2:
        append_protocol_gate_result(
            slug,
            {
                "gate_id": "continuation.structural_change_soft_audit",
                "severity": "soft_deviation",
                "outcome": "observed",
                "message": "Round continuation changed fewer than two structural axes; accepted because semantic continuation gates passed.",
                "refs": [str(contract.get("contract_id") or "contract")],
                "timestamp": time.time(),
            },
            session_id=session_id,
        )
    if token_payload.get("recommended_next_action") == "pivot" and not (operator_changed or targets_changed):
        raise InvalidContinuationToken(
            "Pivot continuations must switch operator_id or target_hypotheses in substance.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step="Change the operator or primary target hypotheses for a pivot continuation.",
        )
    if token_payload.get("recommended_next_action") == "refine" and not queries_changed:
        raise InvalidContinuationToken(
            "Refine continuations must still change the query set to pursue a narrower or stronger test.",
            current_stage=str(state.get("current_stage")),
            suggested_next_step="Update the next round queries to target a more decisive residual test.",
        )
    if isinstance(current_queries, list):
        for query in current_queries:
            if not isinstance(query, dict):
                continue
            query_open_question_ids = query.get("addresses_open_question_ids", [])
            if query_open_question_ids and any(question_id not in target_open_question_ids for question_id in query_open_question_ids):
                raise InvalidContinuationToken(
                    "Round continuation queries must bind only to the round's target_open_question_ids.",
                    current_stage=str(state.get("current_stage")),
                    suggested_next_step="Align query focus bindings with the contract target open questions.",
                )
            query_residual_component = query.get("addresses_residual_component")
            if (
                isinstance(query_residual_component, str)
                and query_residual_component.strip()
                and query_residual_component != continuation_basis.get("target_residual_component")
            ):
                raise InvalidContinuationToken(
                    "Round continuation query residual bindings must match continuation_basis.target_residual_component.",
                    current_stage=str(state.get("current_stage")),
                    suggested_next_step="Align query residual bindings with the contract target residual component.",
                )


def persist_intent_stage(
    slug: str,
    intent_result: dict[str, Any],
    *,
    session_mode: str = SESSION_MODE_ORCHESTRATED_ONLY,
    session_id: str | None = None,
) -> dict[str, str]:
    require_orchestrated_entry(session_mode)
    set_transition_mode(slug, "normal", session_mode=session_mode, session_id=session_id)
    state = begin_stage(slug, "intent", session_mode=session_mode, session_id=session_id)
    _record_stage_decision(
        slug,
        stage="intent",
        phase="enter",
        next_stage="intent",
        session_mode=session_mode,
        session_id=session_id,
    )
    validate_intent_recognition_result(intent_result)
    validate_intent_stage_payload(slug, intent_result, session_id=session_id)
    normalized_intent = intent_result["normalized_intent"]
    if (
        state.get("restart_count", 0) == 0
        and read_artifact(slug, "intent.json", session_id=session_id, strict_session=True) is not None
    ):
        guard_frozen_artifact(state, "intent.json", normalized_intent)
    intent_path = persist_artifact(
        slug,
        "intent.json",
        normalized_intent,
        session_id=session_id,
        strict_session=True,
    )
    sidecar_path = persist_artifact(
        slug,
        "intent_sidecar.json",
        {"pack_gaps": intent_result["pack_gaps"]},
        session_id=session_id,
        strict_session=True,
    )
    complete_stage(
        slug,
        "intent",
        session_mode=session_mode,
        session_id=session_id,
        frozen_artifact="intent.json",
        artifact_payload=normalized_intent,
    )
    _record_stage_decision(
        slug,
        stage="intent",
        phase="complete",
        next_stage="discovery",
        session_mode=session_mode,
        session_id=session_id,
    )
    return {"intent_path": intent_path, "intent_sidecar_path": sidecar_path}


def persist_discovery_stage(
    slug: str,
    discovery_bundle: dict[str, Any],
    *,
    session_mode: str = SESSION_MODE_ORCHESTRATED_ONLY,
    session_id: str | None = None,
) -> str:
    require_orchestrated_entry(session_mode)
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    require_intent_ready(slug, state, session_id=session_id)
    validate_intent_ready_for_downstream(slug, session_id=session_id)
    set_transition_mode(slug, "normal", session_mode=session_mode, session_id=session_id)
    state = begin_stage(slug, "discovery", session_mode=session_mode, session_id=session_id)
    _record_stage_decision(
        slug,
        stage="discovery",
        phase="enter",
        next_stage="discovery",
        session_mode=session_mode,
        session_id=session_id,
    )
    validate_data_context_bundle(discovery_bundle)
    validate_discovery_stage_payload(slug, discovery_bundle, session_id=session_id)
    existing_intent = read_artifact(slug, "intent.json", session_id=session_id, strict_session=True)
    if existing_intent is None:
        require_intent_ready(slug, state, session_id=session_id)
    guard_frozen_artifact(state, "intent.json", existing_intent)
    path = persist_artifact(
        slug,
        "environment_scan.json",
        discovery_bundle,
        session_id=session_id,
        strict_session=True,
    )
    complete_stage(slug, "discovery", session_mode=session_mode, session_id=session_id)
    _record_stage_decision(
        slug,
        stage="discovery",
        phase="complete",
        next_stage="planning",
        session_mode=session_mode,
        session_id=session_id,
    )
    return path


def persist_plan_stage(
    slug: str,
    plan_bundle: dict[str, Any],
    *,
    session_mode: str = SESSION_MODE_ORCHESTRATED_ONLY,
    session_id: str | None = None,
) -> str:
    require_orchestrated_entry(session_mode)
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    require_discovery_ready(slug, state, session_id=session_id)
    set_transition_mode(slug, "normal", session_mode=session_mode, session_id=session_id)
    state = begin_stage(slug, "planning", session_mode=session_mode, session_id=session_id)
    _record_stage_decision(
        slug,
        stage="planning",
        phase="enter",
        next_stage="planning",
        session_mode=session_mode,
        session_id=session_id,
    )
    validate_plan_bundle(plan_bundle)
    validate_plan_stage_payload(slug, plan_bundle, session_id=session_id)
    guard_frozen_artifact(
        state,
        "intent.json",
        read_artifact(slug, "intent.json", session_id=session_id, strict_session=True),
    )
    path = persist_artifact(
        slug,
        "plan.json",
        plan_bundle,
        session_id=session_id,
        strict_session=True,
    )
    complete_stage(
        slug,
        "planning",
        session_mode=session_mode,
        session_id=session_id,
        frozen_artifact="plan.json",
        artifact_payload=plan_bundle,
    )
    _record_stage_decision(
        slug,
        stage="planning",
        phase="complete",
        next_stage="execution",
        session_mode=session_mode,
        session_id=session_id,
    )
    return path


def persist_round_execution_stage(
    client: Any,
    slug: str,
    contract: dict[str, Any],
    *,
    web_client: WebSearchClient | None = None,
    produce_web_recall_assessment: Callable[..., dict[str, Any]] | None = None,
    action_rationales: list[dict[str, Any]] | None = None,
    session_mode: str = SESSION_MODE_ORCHESTRATED_ONLY,
    session_id: str | None = None,
    timeout: float = 30.0,
    max_rows: int = 10_000,
    max_cache_age_seconds: float | None = None,
    web_timeout: float = 30.0,
    web_max_results: int | None = None,
) -> dict[str, Any]:
    require_orchestrated_entry(session_mode)
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    plan_bundle = require_plan_ready(slug, state, session_id=session_id)
    set_transition_mode(slug, "normal", session_mode=session_mode, session_id=session_id)
    state = begin_stage(slug, "execution", session_mode=session_mode, session_id=session_id)
    _record_stage_decision(
        slug,
        stage="execution",
        phase="enter",
        next_stage="execution",
        session_mode=session_mode,
        session_id=session_id,
    )
    validate_investigation_contract(contract)
    validated_contract_hash = stable_payload_hash(contract)
    round_number = int(contract["round_number"])
    assert_round_sequence(state, round_number)
    if round_number == 1 and contract != plan_bundle.get("round_1_contract"):
        raise ValueError("Round 1 contract must match PlanBundle.round_1_contract exactly.")
    if round_number > 1:
        _validate_round_2_plus_lineage(slug, state, contract, session_id=session_id)
    rationales = action_rationales or [
        _autofill_action_rationale(
            current_stage="execution",
            action_type="contract_execution",
            purpose=f"Execute InvestigationContract {contract['contract_id']} for round {round_number}.",
            expected_output_type="round_execution_bundle",
            artifact_impact=[f"rounds/round_{round_number}.json", "protocol_trace.json"],
            why_not_a_later_stage_claim="This action records executable evidence only and does not promote conclusions.",
        )
    ]
    recorded_action_refs: list[str] = []
    for rationale in rationales:
        recorded_action_refs.append(append_action_rationale(slug, rationale, session_id=session_id))
    if contract.get("web_searches") and web_client is None:
        append_protocol_gate_result(
            slug,
            {
                "gate_id": "execution.web_search_unavailable",
                "severity": "soft_deviation",
                "outcome": "observed",
                "message": "InvestigationContract requested web_search evidence, but no web provider is configured.",
                "refs": [str(contract.get("contract_id") or "contract")],
                "timestamp": time.time(),
            },
            session_id=session_id,
        )
    evidence_bundle = execute_evidence_contract(
        client,
        contract,
        web_client=web_client,
        produce_web_recall_assessment=produce_web_recall_assessment,
        slug=slug,
        session_id=session_id,
        timeout=timeout,
        max_rows=max_rows,
        max_cache_age_seconds=max_cache_age_seconds,
        web_timeout=web_timeout,
        web_max_results=web_max_results,
    )
    executed_queries = evidence_bundle["executed_queries"]
    executed_web_searches = evidence_bundle["executed_web_searches"]
    web_recall_assessments = evidence_bundle["web_recall_assessments"]
    validate_execution_stage_payload(
        slug,
        contract,
        executed_queries,
        executed_web_searches=executed_web_searches,
        expected_contract_hash=validated_contract_hash,
        session_id=session_id,
    )
    round_id = f"round_{round_number}"
    query_refs: list[str] = []
    for query in executed_queries:
        query_id = query.get("query_id")
        if isinstance(query_id, str):
            query_refs.append(f"{round_id}:{query_id}")
    web_refs: list[str] = []
    for search in executed_web_searches:
        search_id = search.get("search_id")
        if isinstance(search_id, str):
            web_refs.append(f"{round_id}:{search_id}")
    append_tool_usage_envelope(
        slug,
        {
            "tool_ref": "",
            "tool_name": "execute_evidence_contract",
            "stage": "execution",
            "purpose": f"Execute round contract {contract['contract_id']}.",
            "expected_artifact_impact": [f"rounds/{round_id}.json", "execution_log.json"],
            "produced_evidence_refs": query_refs + web_refs + recorded_action_refs,
            "timestamp": time.time(),
        },
        session_id=session_id,
    )
    bundle = {
        "contract": contract,
        "executed_queries": executed_queries,
        "executed_web_searches": executed_web_searches,
        "web_recall_assessments": web_recall_assessments,
        "evaluation": None,
    }
    persist_round_bundle(
        slug,
        round_id,
        contract,
        executed_queries,
        {},
        executed_web_searches=executed_web_searches,
        web_recall_assessments=web_recall_assessments,
        session_id=session_id,
        strict_session=True,
    )
    complete_stage(
        slug,
        "execution",
        session_mode=session_mode,
        session_id=session_id,
        frozen_artifact=f"rounds/{round_id}/contract",
        artifact_payload=contract,
        latest_round_number=round_number,
    )
    if round_number > 1:
        consume_continuation_token(slug, round_number, session_mode=session_mode, session_id=session_id)
    _record_stage_decision(
        slug,
        stage="execution",
        phase="complete",
        next_stage="evaluation",
        session_mode=session_mode,
        session_id=session_id,
    )
    return bundle


def persist_round_evaluation_stage(
    slug: str,
    evaluation_result: dict[str, Any],
    *,
    action_rationale: dict[str, Any] | None = None,
    session_mode: str = SESSION_MODE_ORCHESTRATED_ONLY,
    session_id: str | None = None,
) -> str:
    require_orchestrated_entry(session_mode)
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    round_number = int(evaluation_result.get("round_number", 0))
    bundle = require_round_execution_ready(slug, state, round_number, session_id=session_id)
    set_transition_mode(slug, "normal", session_mode=session_mode, session_id=session_id)
    state = begin_stage(slug, "evaluation", session_mode=session_mode, session_id=session_id)
    _record_stage_decision(
        slug,
        stage="evaluation",
        phase="enter",
        next_stage="evaluation",
        session_mode=session_mode,
        session_id=session_id,
    )
    rationale = action_rationale or _autofill_action_rationale(
        current_stage="evaluation",
        action_type="evaluation_continuation",
        purpose=f"Evaluate round {round_number} evidence and decide whether to continue, pivot, stop, or restart.",
        expected_output_type="round_evaluation_result",
        artifact_impact=[f"rounds/round_{round_number}.json", "protocol_trace.json"],
        why_not_a_later_stage_claim="This action updates residual state and next-step authorization without synthesizing the final narrative.",
    )
    append_action_rationale(slug, rationale, session_id=session_id)
    next_stage = _next_stage_from_evaluation(evaluation_result)
    transition_mode = "restart" if evaluation_result.get("recommended_next_action") == "restart" else "normal"
    decision_ref = f"decision_eval_round_{round_number}_{int(time.time() * 1_000_000)}"
    evaluation_result = dict(evaluation_result)
    evaluation_result["continuation_decision_ref"] = decision_ref
    validate_round_evaluation_result(
        evaluation_result,
        contract=bundle["contract"],
        executed_queries=bundle["executed_queries"],
        executed_web_searches=bundle.get("executed_web_searches", []),
    )
    validate_evaluation_stage_payload(slug, evaluation_result, session_id=session_id)
    path = persist_round_evaluation(
        slug,
        evaluation_result,
        contract=bundle["contract"],
        executed_queries=bundle["executed_queries"],
        executed_web_searches=bundle.get("executed_web_searches", []),
        web_recall_assessments=bundle.get("web_recall_assessments", []),
        session_id=session_id,
    )
    complete_stage(
        slug,
        "evaluation",
        session_mode=session_mode,
        session_id=session_id,
        latest_round_number=round_number,
        next_stage_override=next_stage,
    )
    _record_stage_decision(
        slug,
        stage="evaluation",
        phase="complete",
        next_stage=next_stage,
        session_mode=session_mode,
        transition_mode=transition_mode,
        note=f"Resolve round {round_number} into {evaluation_result.get('recommended_next_action', 'stop')} for the next step.",
        decision_ref=decision_ref,
        session_id=session_id,
    )
    if evaluation_result.get("should_continue") and evaluation_result.get("recommended_next_action") in {"refine", "pivot"}:
        hypothesis_state_basis = stable_payload_hash(_read_effective_hypothesis_state(slug, session_id=session_id))
        issue_continuation_token(
            slug,
            session_mode=session_mode,
            session_id=session_id,
            evaluation=evaluation_result,
            hypothesis_state_basis=hypothesis_state_basis,
            allowed_target_hypotheses=_legal_target_hypotheses(slug, session_id=session_id),
        )
    if evaluation_result.get("recommended_next_action") == "restart":
        intent_payload = read_artifact(slug, "intent.json", session_id=session_id, strict_session=True)
        prior_intent_hash = stable_payload_hash(intent_payload) if intent_payload is not None else None
        mark_restart(
            slug,
            session_mode=session_mode,
            session_id=session_id,
            reason=evaluation_result.get("stop_reason"),
            prior_intent_hash=prior_intent_hash,
        )
    return path


def persist_finalization_stage(
    slug: str,
    final_answer: dict[str, Any],
    *,
    report_evidence: dict[str, Any],
    action_rationale: dict[str, Any] | None = None,
    session_mode: str = SESSION_MODE_ORCHESTRATED_ONLY,
    session_id: str | None = None,
) -> str:
    require_orchestrated_entry(session_mode)
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    require_evaluation_ready(slug, state, session_id=session_id)
    set_transition_mode(slug, "normal", session_mode=session_mode, session_id=session_id)
    state = begin_stage(slug, "finalization", session_mode=session_mode, session_id=session_id)
    _record_stage_decision(
        slug,
        stage="finalization",
        phase="enter",
        next_stage="finalization",
        session_mode=session_mode,
        session_id=session_id,
    )
    rationale = action_rationale or _autofill_action_rationale(
        current_stage="finalization",
        action_type="final_answer_synthesis",
        purpose="Persist the evidence-backed final answer and explicit report evidence bundle.",
        expected_output_type="final_answer_and_report_evidence",
        artifact_impact=["final_answer.json", "report_evidence.json", "report_evidence_index.json"],
        why_not_a_later_stage_claim="This action freezes conclusion semantics and report evidence semantics without creating new evidence.",
    )
    append_action_rationale(slug, rationale, session_id=session_id)
    latest_evaluation = get_latest_round_evaluation(slug, session_id=session_id)
    validate_final_answer(
        final_answer,
        slug=slug,
        latest_evaluation=latest_evaluation,
        session_id=session_id,
    )
    _validate_report_evidence_for_session(
        slug,
        report_evidence,
        final_answer=final_answer,
        session_id=session_id,
    )
    validate_finalization_stage_payload(
        slug,
        final_answer,
        report_evidence,
        session_id=session_id,
    )
    path = persist_final_answer(slug, final_answer, session_id=session_id)
    persist_artifact(
        slug,
        "report_evidence.json",
        report_evidence,
        session_id=session_id,
        strict_session=True,
    )
    report_evidence_index = _build_report_evidence_index(slug, report_evidence, session_id=session_id or "legacy")
    persist_artifact(
        slug,
        "report_evidence_index.json",
        report_evidence_index,
        session_id=session_id,
        strict_session=True,
    )
    complete_stage(
        slug,
        "finalization",
        session_mode=session_mode,
        session_id=session_id,
        frozen_artifact="final_answer.json",
        artifact_payload=final_answer,
        next_stage_override="chart_spec",
    )
    _record_stage_decision(
        slug,
        stage="finalization",
        phase="complete",
        next_stage="chart_spec",
        session_mode=session_mode,
        session_id=session_id,
    )
    return path


def persist_chart_spec_stage(
    slug: str,
    chart_spec_bundle: dict[str, Any],
    *,
    action_rationale: dict[str, Any] | None = None,
    session_mode: str = SESSION_MODE_ORCHESTRATED_ONLY,
    session_id: str | None = None,
) -> str:
    require_orchestrated_entry(session_mode)
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    require_finalization_ready(slug, state, session_id=session_id)
    set_transition_mode(slug, "normal", session_mode=session_mode, session_id=session_id)
    begin_stage(slug, "chart_spec", session_mode=session_mode, session_id=session_id)
    _record_stage_decision(
        slug,
        stage="chart_spec",
        phase="enter",
        next_stage="chart_spec",
        session_mode=session_mode,
        session_id=session_id,
    )
    rationale = action_rationale or _autofill_action_rationale(
        current_stage="chart_spec",
        action_type="artifact_persistence",
        purpose="Persist runtime-compiled structured chart specs from chart-ready evidence affordances.",
        expected_output_type="chart_spec_bundle",
        artifact_impact=["chart_spec_bundle.json"],
        why_not_a_later_stage_claim="This action proposes chart interpretations but does not render or create new evidence.",
    )
    append_action_rationale(slug, rationale, session_id=session_id)
    validate_chart_spec_bundle(chart_spec_bundle)
    validate_chart_spec_stage_payload(slug, chart_spec_bundle, session_id=session_id)
    path = persist_artifact(
        slug,
        "chart_spec_bundle.json",
        chart_spec_bundle,
        session_id=session_id,
        strict_session=True,
    )
    complete_stage(
        slug,
        "chart_spec",
        session_mode=session_mode,
        session_id=session_id,
        next_stage_override="chart_render",
    )
    _record_stage_decision(
        slug,
        stage="chart_spec",
        phase="complete",
        next_stage="chart_render",
        session_mode=session_mode,
        session_id=session_id,
    )
    return path


def persist_chart_render_stage(
    slug: str,
    *,
    client: Any | None = None,
    action_rationale: dict[str, Any] | None = None,
    session_mode: str = SESSION_MODE_ORCHESTRATED_ONLY,
    session_id: str | None = None,
    rehydrate_missing_result_rows: bool = False,
    timeout: float = 30.0,
    max_rows: int = 10_000,
    max_cache_age_seconds: float | None = None,
) -> dict[str, Any]:
    require_orchestrated_entry(session_mode)
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    require_chart_spec_ready(slug, state, session_id=session_id)
    set_transition_mode(slug, "normal", session_mode=session_mode, session_id=session_id)
    begin_stage(slug, "chart_render", session_mode=session_mode, session_id=session_id)
    _record_stage_decision(
        slug,
        stage="chart_render",
        phase="enter",
        next_stage="chart_render",
        session_mode=session_mode,
        session_id=session_id,
    )
    rationale = action_rationale or _autofill_action_rationale(
        current_stage="chart_render",
        action_type="artifact_persistence",
        purpose="Validate chart specs, resolve render modes, render chart assets, and persist plot-data lineage.",
        expected_output_type="chart_render_bundle",
        artifact_impact=[
            "descriptive_stats.json",
            "visualization_manifest.json",
            "charts/*.png",
            "charts/*.plot-data.json",
        ],
        why_not_a_later_stage_claim="This action materializes charts from persisted evidence but does not create report prose or new claims.",
    )
    append_action_rationale(slug, rationale, session_id=session_id)
    bundle = render_chart_artifacts(
        slug,
        client=client,
        session_id=session_id,
        rehydrate_missing_result_rows=rehydrate_missing_result_rows,
        timeout=timeout,
        max_rows=max_rows,
        max_cache_age_seconds=max_cache_age_seconds,
    )
    validate_descriptive_stats_bundle(bundle["descriptive_stats"])
    validate_visualization_manifest(bundle["visualization_manifest"])
    complete_stage(
        slug,
        "chart_render",
        session_mode=session_mode,
        session_id=session_id,
        next_stage_override="report_assembly",
    )
    _record_stage_decision(
        slug,
        stage="chart_render",
        phase="complete",
        next_stage="report_assembly",
        session_mode=session_mode,
        session_id=session_id,
    )
    return bundle


def persist_report_assembly_stage(
    slug: str,
    *,
    action_rationale: dict[str, Any] | None = None,
    session_mode: str = SESSION_MODE_ORCHESTRATED_ONLY,
    session_id: str | None = None,
) -> dict[str, Any]:
    require_orchestrated_entry(session_mode)
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    require_chart_render_ready(slug, state, session_id=session_id)
    set_transition_mode(slug, "normal", session_mode=session_mode, session_id=session_id)
    begin_stage(slug, "report_assembly", session_mode=session_mode, session_id=session_id)
    _record_stage_decision(
        slug,
        stage="report_assembly",
        phase="enter",
        next_stage="report_assembly",
        session_mode=session_mode,
        session_id=session_id,
    )
    rationale = action_rationale or _autofill_action_rationale(
        current_stage="report_assembly",
        action_type="artifact_persistence",
        purpose="Assemble the final markdown report from final answer, report evidence, and rendered chart artifacts.",
        expected_output_type="final_report",
        artifact_impact=[
            "report.md",
            "evidence_graph.json",
            "compliance_report.json",
        ],
        why_not_a_later_stage_claim="This action packages already-persisted evidence and chart outputs into human-readable report prose without introducing new claims.",
    )
    append_action_rationale(slug, rationale, session_id=session_id)
    bundle = assemble_report_artifacts(slug, session_id=session_id)
    complete_stage(
        slug,
        "report_assembly",
        session_mode=session_mode,
        session_id=session_id,
        next_stage_override="suggestion_synthesis",
    )
    _record_stage_decision(
        slug,
        stage="report_assembly",
        phase="complete",
        next_stage="suggestion_synthesis",
        session_mode=session_mode,
        session_id=session_id,
    )
    report = run_protocol_audit(slug, session_id=session_id)
    for event in report.get("events", []):
        severity = event.get("severity")
        if severity in {"strict_violation", "soft_deviation", "efficiency_drift"}:
            register_protocol_violation(slug, severity, session_mode=session_mode, session_id=session_id)
    if report.get("final_verdict") == "fail":
        fail_stage(slug, "report_assembly", session_mode=session_mode, session_id=session_id)
        raise FinalizationPreconditionViolation(
            "Protocol audit failed after report assembly; session artifacts were retained for inspection.",
            current_stage="report_assembly",
            blocking_artifacts=[
                "compliance_report.json",
                "visualization_manifest.json",
                "report.md",
            ],
            suggested_next_step="Inspect compliance_report.json and repair the session lineage or visualization artifacts before continuing.",
        )
    return bundle


def persist_suggestion_synthesis_stage(
    slug: str,
    suggestions: dict[str, Any],
    *,
    session_mode: str = SESSION_MODE_ORCHESTRATED_ONLY,
    business_label: str | None = None,
    session_id: str | None = None,
) -> str:
    require_orchestrated_entry(session_mode)
    state = ensure_session_state(slug, session_mode=session_mode, session_id=session_id)
    require_report_assembly_ready(slug, state, session_id=session_id)
    set_transition_mode(slug, "normal", session_mode=session_mode, session_id=session_id)
    state = begin_stage(slug, "suggestion_synthesis", session_mode=session_mode, session_id=session_id)
    _record_stage_decision(
        slug,
        stage="suggestion_synthesis",
        phase="enter",
        next_stage="suggestion_synthesis",
        session_mode=session_mode,
        session_id=session_id,
    )
    path = persist_domain_pack_suggestions(slug, suggestions, business_label=business_label, session_id=session_id)
    complete_stage(slug, "suggestion_synthesis", session_mode=session_mode, session_id=session_id)
    _record_stage_decision(
        slug,
        stage="suggestion_synthesis",
        phase="complete",
        next_stage="done",
        session_mode=session_mode,
        session_id=session_id,
    )
    return path


def run_research_session(
    client: Any,
    slug: str,
    *,
    raw_question: str,
    current_date: str,
    available_domain_packs: list[dict[str, Any]] | None = None,
    forced_domain_pack_id: str | None = None,
    produce_intent: Callable[..., dict[str, Any]],
    produce_discovery: Callable[..., dict[str, Any]],
    produce_plan: Callable[..., dict[str, Any]],
    produce_evaluation: Callable[..., dict[str, Any]],
    produce_final_answer: Callable[..., dict[str, Any]],
    produce_report_evidence: Callable[..., dict[str, Any]],
    produce_chart_specs: Callable[..., dict[str, Any]],
    produce_next_contract: Callable[..., dict[str, Any]] | None = None,
    web_client: WebSearchClient | None = None,
    produce_web_recall_assessment: Callable[..., dict[str, Any]] | None = None,
    produce_domain_pack_suggestions: Callable[..., dict[str, Any] | None] | None = None,
    report_locale: str | None = None,
    report_template: dict[str, str] | None = None,
    report_policy: dict[str, Any] | None = None,
    semantic_guard_policy: dict[str, Any] | None = None,
    web_search_mode: str = "auto",
    timeout: float = 30.0,
    max_rows: int = 10_000,
    max_cache_age_seconds: float | None = None,
    web_timeout: float = 30.0,
    web_max_results: int | None = None,
) -> dict[str, Any]:
    """
    Run the full orchestrated research loop.

    Producer expectations:

    - `produce_evaluation(...)` must author a complete RoundEvaluationResult and
      emit `continuation_guidance` whenever continuation is authorized.
    - `produce_next_contract(...)` must treat `latest_evaluation` as the control
      input for Round 2+ and should not expand the original plan into a fixed
      round script.
    - Remaining round budget is never, by itself, a valid reason to continue.
    """
    session_mode = SESSION_MODE_ORCHESTRATED_ONLY
    runtime_policy: dict[str, Any] = {}
    if isinstance(report_policy, dict):
        runtime_policy["report_policy"] = report_policy
    if isinstance(semantic_guard_policy, dict):
        runtime_policy["semantic_guard_policy"] = semantic_guard_policy
        configure_semantic_guard_policy(semantic_guard_policy)
    resolved_web_client = resolve_default_web_client(web_client=web_client, mode=web_search_mode)
    runtime_policy["web_search"] = get_web_search_configuration_status(
        web_client=resolved_web_client,
        mode=web_search_mode,
    )
    set_report_template(report_template, locale=report_locale)
    session_info = start_session(
        slug,
        raw_question=raw_question,
        created_at=time.time(),
    )
    session_id = str(session_info["session_id"])
    persist_manifest(
        slug,
        {
            "slug": slug,
            "chosen_skill": CHOSEN_SKILL,
            "protocol_mode": PROTOCOL_MODE,
            "raw_question": raw_question,
            "current_date": current_date,
            "report_locale": report_locale,
            "report_template": report_template,
            "runtime_policy": runtime_policy,
        },
        session_id=session_id,
        strict_session=True,
    )
    if not runtime_policy["web_search"].get("enabled"):
        append_protocol_gate_result(
            slug,
            {
                "gate_id": "preflight.web_search_unavailable",
                "severity": "soft_deviation",
                "outcome": "observed",
                "message": "Web search provider is not configured; this session may run SQL-only unless a later contract requires blocked web evidence.",
                "refs": ["manifest.runtime_policy.web_search"],
                "timestamp": time.time(),
            },
            session_id=session_id,
        )
    intent_result = _call_producer(
        produce_intent,
        raw_question=raw_question,
        current_date=current_date,
        available_domain_packs=available_domain_packs or [],
        forced_domain_pack_id=forced_domain_pack_id,
    )
    persist_intent_stage(slug, intent_result, session_mode=session_mode, session_id=session_id)

    discovery_bundle = _call_producer(
        produce_discovery,
        normalized_intent=intent_result["normalized_intent"],
        active_domain_pack_id=intent_result["normalized_intent"]["domain_pack_id"],
    )
    persist_discovery_stage(slug, discovery_bundle, session_mode=session_mode, session_id=session_id)

    plan_bundle = _call_producer(
        produce_plan,
        normalized_intent=intent_result["normalized_intent"],
        discovery_bundle=discovery_bundle,
        active_domain_pack_id=intent_result["normalized_intent"]["domain_pack_id"],
    )
    persist_plan_stage(slug, plan_bundle, session_mode=session_mode, session_id=session_id)

    contract = plan_bundle["round_1_contract"]
    while True:
        persist_round_execution_stage(
            client,
            slug,
            contract,
            session_mode=session_mode,
            session_id=session_id,
            timeout=timeout,
            max_rows=max_rows,
            max_cache_age_seconds=max_cache_age_seconds,
            web_client=resolved_web_client,
            produce_web_recall_assessment=produce_web_recall_assessment,
            web_timeout=web_timeout,
            web_max_results=web_max_results,
        )
        round_bundle = read_round_bundle(
            slug,
            f"round_{contract['round_number']}",
            session_id=session_id,
            strict_session=True,
        ) or {}
        evaluation_result = _call_producer(
            produce_evaluation,
            contract=contract,
            executed_queries=round_bundle.get("executed_queries", []),
            executed_web_searches=round_bundle.get("executed_web_searches", []),
            web_recall_assessments=round_bundle.get("web_recall_assessments", []),
            latest_round_evaluation=get_latest_round_evaluation(slug, session_id=session_id),
            plan_bundle=plan_bundle,
        )
        persist_round_evaluation_stage(slug, evaluation_result, session_mode=session_mode, session_id=session_id)
        if evaluation_result.get("recommended_next_action") == "restart":
            latest_restart_evaluation = get_latest_round_evaluation(slug, session_id=session_id)
            restart_state = read_session_state(slug, session_id=session_id)
            return {
                "status": "restart_required",
                "next_stage": "intent",
                "slug": slug,
                "session_id": session_id,
                "session_root": session_info["session_root"],
                "latest_round_evaluation": latest_restart_evaluation,
                "blocking_artifacts": [f"rounds/round_{evaluation_result['round_number']}.json"],
                "suggested_next_step": "Regenerate intent/discovery/plan under the restart flow before continuing.",
                "session_state": restart_state,
            }
        if not evaluation_result.get("should_continue"):
            break
        if produce_next_contract is None:
            raise ValueError("produce_next_contract is required when the session should continue.")
        state = read_session_state(slug, session_id=session_id) or {}
        continuation_authorization = get_continuation_token(state, int(evaluation_result["round_number"]) + 1)
        contract = _call_producer(
            produce_next_contract,
            latest_evaluation=evaluation_result,
            plan_bundle=plan_bundle,
            latest_round_number=evaluation_result["round_number"],
            continuation_authorization=continuation_authorization,
            session_slug=slug,
            frozen_intent=read_artifact(slug, "intent.json", session_id=session_id, strict_session=True),
        )

    final_answer = _call_producer(
        produce_final_answer,
        latest_round_evaluation=get_latest_round_evaluation(slug, session_id=session_id),
        session_slug=slug,
    )
    report_evidence = _call_producer(
        produce_report_evidence,
        latest_round_evaluation=get_latest_round_evaluation(slug, session_id=session_id),
        final_answer=final_answer,
        session_slug=slug,
        session_id=session_id,
    )
    persist_finalization_stage(
        slug,
        final_answer,
        report_evidence=report_evidence,
        session_mode=session_mode,
        session_id=session_id,
    )
    chart_affordance_result = persist_chart_affordance_bundle(slug, session_id=session_id)
    chart_affordance_bundle = chart_affordance_result["chart_affordances"]
    chart_plan_or_spec = _call_producer(
        produce_chart_specs,
        final_answer=final_answer,
        report_evidence=report_evidence,
        session_slug=slug,
        session_id=session_id,
        session_evidence=load_session_evidence(slug, session_id=session_id, strict_session=True),
        chart_affordances=chart_affordance_bundle,
        visualization_capabilities=get_visualization_capabilities(),
    )
    compiled_chart_specs = compile_chart_specs_from_affordance_plan(
        chart_plan_or_spec,
        chart_affordance_bundle,
    )
    persist_artifact(
        slug,
        "chart_compile_report.json",
        compiled_chart_specs["chart_compile_report"],
        session_id=session_id,
        strict_session=True,
    )
    chart_spec_bundle = compiled_chart_specs["chart_spec_bundle"]
    persist_chart_spec_stage(slug, chart_spec_bundle, session_mode=session_mode, session_id=session_id)
    persist_chart_render_stage(slug, session_mode=session_mode, session_id=session_id)
    report_bundle = persist_report_assembly_stage(slug, session_mode=session_mode, session_id=session_id)

    suggestion_path = None
    if produce_domain_pack_suggestions is not None:
        suggestions = produce_domain_pack_suggestions(
            session_slug=slug,
            active_pack_id=intent_result["normalized_intent"]["domain_pack_id"],
        )
        if suggestions:
            suggestion_path = persist_suggestion_synthesis_stage(
                slug,
                suggestions,
                session_mode=session_mode,
                business_label=intent_result["normalized_intent"]["business_object"]["label"],
                session_id=session_id,
            )
    mark_session_complete(slug, session_mode=session_mode, session_id=session_id)
    manifest = read_artifact(slug, "manifest.json", session_id=session_id, strict_session=True)
    if isinstance(manifest, dict):
        manifest.update(
            {
                "session_id": session_id,
                "session_root": session_info["session_root"],
                "report_path": report_bundle["report_path"],
                "visualization_manifest_path": report_bundle["visualization_manifest_path"],
            }
        )
        persist_manifest(slug, manifest, session_id=session_id, strict_session=True)

    return {
        "slug": slug,
        "session_id": session_id,
        "session_root": session_info["session_root"],
        "intent": read_artifact(slug, "intent.json", session_id=session_id, strict_session=True),
        "discovery": read_artifact(slug, "environment_scan.json", session_id=session_id, strict_session=True),
        "plan": read_artifact(slug, "plan.json", session_id=session_id, strict_session=True),
        "final_answer": read_artifact(slug, "final_answer.json", session_id=session_id, strict_session=True),
        "report_evidence": read_artifact(slug, "report_evidence.json", session_id=session_id, strict_session=True),
        "report_evidence_index": read_artifact(
            slug,
            "report_evidence_index.json",
            session_id=session_id,
            strict_session=True,
        ),
        "chart_affordances": read_artifact(slug, "chart_affordances.json", session_id=session_id, strict_session=True),
        "chart_compile_report": read_artifact(
            slug,
            "chart_compile_report.json",
            session_id=session_id,
            strict_session=True,
        ),
        "chart_spec_bundle": read_artifact(slug, "chart_spec_bundle.json", session_id=session_id, strict_session=True),
        "descriptive_stats": read_artifact(slug, "descriptive_stats.json", session_id=session_id, strict_session=True),
        "visualization_manifest": read_artifact(
            slug,
            "visualization_manifest.json",
            session_id=session_id,
            strict_session=True,
        ),
        "report_path": report_bundle["report_path"],
        "visualization_manifest_path": report_bundle["visualization_manifest_path"],
        "protocol_trace": read_artifact(slug, "protocol_trace.json", session_id=session_id, strict_session=True),
        "evidence_graph": read_artifact(slug, "evidence_graph.json", session_id=session_id, strict_session=True),
        "compliance_report": read_artifact(
            slug,
            "compliance_report.json",
            session_id=session_id,
            strict_session=True,
        ),
        "domain_pack_suggestions_path": suggestion_path,
        "session_state": read_session_state(slug, session_id=session_id),
    }
