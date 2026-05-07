from __future__ import annotations

import time
from typing import Any

from runtime.contracts import (
    validate_action_rationale,
    validate_compliance_report,
    validate_stage_decision,
    validate_tool_usage_envelope,
)
from runtime.persistence import (
    get_active_generation_id,
    list_round_bundles,
    persist_artifact,
    read_artifact,
)


PROTOCOL_TRACE_FILENAME = "protocol_trace.json"
EVIDENCE_GRAPH_FILENAME = "evidence_graph.json"
COMPLIANCE_REPORT_FILENAME = "compliance_report.json"
PROTOCOL_MODE = "research_session_protocol_v1"
CHOSEN_SKILL = "deep-research"


def _default_protocol_trace() -> dict[str, Any]:
    return {
        "version": 1,
        "chosen_skill": CHOSEN_SKILL,
        "protocol_mode": PROTOCOL_MODE,
        "stage_timeline": [],
        "stage_decisions": [],
        "actions": [],
        "tool_usages": [],
        "gate_results": [],
        "updated_at": time.time(),
    }


def _load_protocol_trace(slug: str, *, session_id: str | None = None) -> dict[str, Any]:
    trace = read_artifact(
        slug,
        PROTOCOL_TRACE_FILENAME,
        session_id=session_id,
        strict_session=bool(session_id),
    )
    if not isinstance(trace, dict):
        return _default_protocol_trace()
    normalized = _default_protocol_trace()
    normalized.update(trace)
    for key in ("stage_timeline", "stage_decisions", "actions", "tool_usages", "gate_results"):
        if not isinstance(normalized.get(key), list):
            normalized[key] = []
    return normalized


def _persist_protocol_trace(slug: str, trace: dict[str, Any], *, session_id: str | None = None) -> str:
    trace["updated_at"] = time.time()
    return persist_artifact(
        slug,
        PROTOCOL_TRACE_FILENAME,
        trace,
        session_id=session_id,
        strict_session=bool(session_id),
    )


def append_stage_decision(slug: str, decision: dict[str, Any], *, session_id: str | None = None) -> str:
    trace = _load_protocol_trace(slug, session_id=session_id)
    decision = dict(decision)
    if not decision.get("decision_ref"):
        decision["decision_ref"] = f"decision_{len(trace['stage_decisions']) + 1}"
    validate_stage_decision(decision)
    trace["stage_decisions"].append(decision)
    trace["stage_timeline"].append(
        {
            "stage": decision["stage"],
            "phase": decision["phase"],
            "decision_ref": decision["decision_ref"],
            "transition_mode": decision["transition_mode"],
            "next_stage": decision["next_stage"],
            "timestamp": decision["timestamp"],
        }
    )
    _persist_protocol_trace(slug, trace, session_id=session_id)
    return str(decision["decision_ref"])


def append_action_rationale(slug: str, rationale: dict[str, Any], *, session_id: str | None = None) -> str:
    trace = _load_protocol_trace(slug, session_id=session_id)
    rationale = dict(rationale)
    if not rationale.get("action_ref"):
        rationale["action_ref"] = f"action_{len(trace['actions']) + 1}"
    validate_action_rationale(rationale)
    trace["actions"].append(rationale)
    _persist_protocol_trace(slug, trace, session_id=session_id)
    return str(rationale["action_ref"])


def append_tool_usage_envelope(slug: str, envelope: dict[str, Any], *, session_id: str | None = None) -> str:
    trace = _load_protocol_trace(slug, session_id=session_id)
    envelope = dict(envelope)
    if not envelope.get("tool_ref"):
        envelope["tool_ref"] = f"tool_{len(trace['tool_usages']) + 1}"
    validate_tool_usage_envelope(envelope)
    trace["tool_usages"].append(envelope)
    _persist_protocol_trace(slug, trace, session_id=session_id)
    return str(envelope["tool_ref"])


def append_protocol_gate_result(slug: str, gate_result: dict[str, Any], *, session_id: str | None = None) -> str:
    trace = _load_protocol_trace(slug, session_id=session_id)
    normalized = dict(gate_result)
    if not isinstance(normalized.get("gate_id"), str) or not normalized["gate_id"]:
        raise ValueError("Protocol gate results require a non-empty gate_id.")
    if normalized.get("severity") not in {"strict_violation", "soft_deviation", "efficiency_drift"}:
        raise ValueError("Protocol gate result severity is invalid.")
    if normalized.get("outcome") not in {"blocked", "observed"}:
        raise ValueError("Protocol gate result outcome is invalid.")
    if not isinstance(normalized.get("message"), str) or not normalized["message"]:
        raise ValueError("Protocol gate result message must be a non-empty string.")
    refs = normalized.get("refs")
    if refs is None:
        normalized["refs"] = []
    elif not isinstance(refs, list):
        raise ValueError("Protocol gate result refs must be a list when provided.")
    if not isinstance(normalized.get("timestamp"), (int, float)):
        normalized["timestamp"] = time.time()
    trace["gate_results"].append(normalized)
    _persist_protocol_trace(slug, trace, session_id=session_id)
    return str(normalized["gate_id"])


def build_evidence_graph(slug: str, *, session_id: str | None = None) -> dict[str, Any]:
    active_generation_id = get_active_generation_id(
        slug,
        session_id=session_id,
        strict_session=bool(session_id),
    )
    round_bundles = list_round_bundles(
        slug,
        generation_id=active_generation_id,
        session_id=session_id,
        strict_session=bool(session_id),
    )
    final_answer = read_artifact(
        slug,
        "final_answer.json",
        session_id=session_id,
        strict_session=bool(session_id),
    )
    query_nodes: list[dict[str, Any]] = []
    web_nodes: list[dict[str, Any]] = []
    web_recall_nodes: list[dict[str, Any]] = []
    evaluation_nodes: list[dict[str, Any]] = []
    claim_edges: list[dict[str, Any]] = []
    known_query_refs: set[tuple[str, str]] = set()
    known_web_refs: set[tuple[str, str]] = set()
    known_eval_refs: set[str] = set()

    for bundle in round_bundles:
        contract = bundle.get("contract") if isinstance(bundle, dict) else None
        evaluation = bundle.get("evaluation") if isinstance(bundle, dict) else None
        round_id = evaluation.get("round_id") if isinstance(evaluation, dict) else None
        if isinstance(evaluation, dict) and isinstance(round_id, str):
            evaluation_nodes.append(
                {
                    "evaluation_ref": f"{round_id}:evaluation",
                    "round_id": round_id,
                    "contract_id": contract.get("contract_id") if isinstance(contract, dict) else None,
                    "continuation_decision_ref": evaluation.get("continuation_decision_ref"),
                }
            )
            known_eval_refs.add(f"{round_id}:evaluation")
        for query in bundle.get("executed_queries", []) if isinstance(bundle, dict) else []:
            query_id = query.get("query_id")
            if not isinstance(query_id, str):
                continue
            query_nodes.append(
                {
                    "round_id": round_id,
                    "query_id": query_id,
                    "status": query.get("status"),
                    "source": query.get("source"),
                }
            )
            if isinstance(round_id, str):
                known_query_refs.add((round_id, query_id))
        for search in bundle.get("executed_web_searches", []) if isinstance(bundle, dict) else []:
            search_id = search.get("search_id")
            if not isinstance(search_id, str):
                continue
            web_nodes.append(
                {
                    "round_id": round_id,
                    "search_id": search_id,
                    "status": search.get("status"),
                    "provider": search.get("provider"),
                    "result_count": len(search.get("results", [])) if isinstance(search.get("results"), list) else 0,
                }
            )
            if isinstance(round_id, str):
                known_web_refs.add((round_id, search_id))
        for assessment in bundle.get("web_recall_assessments", []) if isinstance(bundle, dict) else []:
            assessment_id = assessment.get("assessment_id")
            search_id = assessment.get("search_id")
            if not isinstance(assessment_id, str) or not isinstance(search_id, str):
                continue
            web_recall_nodes.append(
                {
                    "round_id": round_id,
                    "assessment_id": assessment_id,
                    "search_id": search_id,
                    "conclusion": assessment.get("conclusion"),
                    "needs_refinement": assessment.get("needs_refinement"),
                }
            )

    if isinstance(final_answer, dict):
        for index, claim in enumerate(final_answer.get("supported_claims", []), start=1):
            if not isinstance(claim, dict):
                continue
            claim_ref = claim.get("claim_ref") or f"claim_{index}"
            for query_ref in claim.get("query_refs", []):
                if isinstance(query_ref, dict):
                    claim_edges.append(
                        {
                            "claim_ref": claim_ref,
                            "round_id": query_ref.get("round_id"),
                            "query_id": query_ref.get("query_id"),
                            "kind": "query_ref",
                        }
                    )
            for evaluation_ref in claim.get("evaluation_refs", []):
                claim_edges.append(
                    {
                        "claim_ref": claim_ref,
                        "evaluation_ref": evaluation_ref,
                        "kind": "evaluation_ref",
                    }
                )
            for web_ref in claim.get("web_refs", []):
                if isinstance(web_ref, dict):
                    claim_edges.append(
                        {
                            "claim_ref": claim_ref,
                            "round_id": web_ref.get("round_id"),
                            "search_id": web_ref.get("search_id"),
                            "kind": "web_ref",
                        }
                    )

    graph = {
        "session_slug": slug,
        "generation_id": active_generation_id,
        "query_nodes": query_nodes,
        "web_nodes": web_nodes,
        "web_recall_nodes": web_recall_nodes,
        "evaluation_nodes": evaluation_nodes,
        "claim_edges": claim_edges,
        "known_query_refs": sorted({"%s:%s" % ref for ref in known_query_refs}),
        "known_web_refs": sorted({"%s:%s" % ref for ref in known_web_refs}),
        "known_evaluation_refs": sorted(known_eval_refs),
    }
    persist_artifact(
        slug,
        EVIDENCE_GRAPH_FILENAME,
        graph,
        session_id=session_id,
        strict_session=bool(session_id),
    )
    return graph


def _count_completed_stage_decisions(trace: dict[str, Any], stage: str) -> int:
    return sum(
        1
        for item in trace.get("stage_decisions", [])
        if isinstance(item, dict) and item.get("stage") == stage and item.get("phase") == "complete"
    )


def _append_event(
    events: list[dict[str, Any]],
    *,
    severity: str,
    message: str,
    ref: str,
) -> None:
    events.append(
        {
            "severity": severity,
            "message": message,
            "ref": ref,
            "timestamp": time.time(),
        }
    )


def _audit_stage_artifact_presence(
    slug: str,
    trace: dict[str, Any],
    round_bundles: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    session_id: str | None = None,
) -> None:
    required_singletons = {
        "intent": "intent.json",
        "discovery": "environment_scan.json",
        "planning": "plan.json",
        "finalization": "final_answer.json",
        "chart_spec": "chart_spec_bundle.json",
        "chart_render": "visualization_manifest.json",
        "report_assembly": "report.md",
        "suggestion_synthesis": "domain_pack_suggestions.json",
    }
    for stage, artifact_name in required_singletons.items():
        if _count_completed_stage_decisions(trace, stage) <= 0:
            continue
        if read_artifact(
            slug,
            artifact_name,
            session_id=session_id,
            strict_session=bool(session_id),
        ) is None:
            _append_event(
                events,
                severity="strict_violation",
                message=f"Completed stage '{stage}' is missing its required artifact.",
                ref=artifact_name,
            )

    if _count_completed_stage_decisions(trace, "finalization") > 0:
        for artifact_name in ("report_evidence.json", "report_evidence_index.json"):
            if read_artifact(
                slug,
                artifact_name,
                session_id=session_id,
                strict_session=bool(session_id),
            ) is None:
                _append_event(
                    events,
                    severity="strict_violation",
                    message=f"Completed stage 'finalization' is missing its required artifact {artifact_name}.",
                    ref=artifact_name,
                )

    if _count_completed_stage_decisions(trace, "chart_render") > 0:
        for artifact_name in ("descriptive_stats.json",):
            if read_artifact(
                slug,
                artifact_name,
                session_id=session_id,
                strict_session=bool(session_id),
            ) is None:
                _append_event(
                    events,
                    severity="strict_violation",
                    message=f"Completed stage 'chart_render' is missing its required artifact {artifact_name}.",
                    ref=artifact_name,
                )

    execution_complete_count = _count_completed_stage_decisions(trace, "execution")
    if execution_complete_count > len(round_bundles):
        _append_event(
            events,
            severity="strict_violation",
            message="Execution stage completion count exceeds persisted round bundles.",
            ref="rounds/",
        )

    evaluation_complete_count = _count_completed_stage_decisions(trace, "evaluation")
    persisted_evaluation_count = sum(
        1
        for bundle in round_bundles
        if isinstance(bundle, dict) and isinstance(bundle.get("evaluation"), dict)
    )
    if evaluation_complete_count > persisted_evaluation_count:
        _append_event(
            events,
            severity="strict_violation",
            message="Evaluation stage completion count exceeds persisted round evaluations.",
            ref="rounds/",
        )


def _audit_evaluation_decision_lineage(
    trace: dict[str, Any],
    round_bundles: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> None:
    known_decision_refs = {
        item.get("decision_ref")
        for item in trace.get("stage_decisions", [])
        if isinstance(item, dict) and isinstance(item.get("decision_ref"), str)
    }
    for bundle in round_bundles:
        if not isinstance(bundle, dict):
            continue
        evaluation = bundle.get("evaluation")
        if not isinstance(evaluation, dict):
            continue
        decision_ref = evaluation.get("continuation_decision_ref")
        round_id = evaluation.get("round_id", "unknown_round")
        if not isinstance(decision_ref, str) or not decision_ref:
            _append_event(
                events,
                severity="strict_violation",
                message="Round evaluation is missing continuation_decision_ref lineage.",
                ref=str(round_id),
            )
            continue
        if decision_ref not in known_decision_refs:
            _append_event(
                events,
                severity="strict_violation",
                message="Round evaluation references a protocol decision that was never persisted.",
                ref=decision_ref,
            )


def run_protocol_audit(slug: str, *, session_id: str | None = None) -> dict[str, Any]:
    trace = _load_protocol_trace(slug, session_id=session_id)
    graph = build_evidence_graph(slug, session_id=session_id)
    session_state = read_artifact(
        slug,
        "session_state.json",
        session_id=session_id,
        strict_session=bool(session_id),
    ) or {}
    final_answer = read_artifact(
        slug,
        "final_answer.json",
        session_id=session_id,
        strict_session=bool(session_id),
    ) or {}
    active_generation_id = get_active_generation_id(
        slug,
        session_id=session_id,
        strict_session=bool(session_id),
    )
    round_bundles = list_round_bundles(
        slug,
        generation_id=active_generation_id,
        session_id=session_id,
        strict_session=bool(session_id),
    )

    attributable_actions = [
        item.get("action_ref")
        for item in trace.get("actions", [])
        if isinstance(item, dict) and isinstance(item.get("action_ref"), str)
    ]
    attributable_actions.extend(
        item.get("tool_ref")
        for item in trace.get("tool_usages", [])
        if isinstance(item, dict) and isinstance(item.get("tool_ref"), str)
    )
    unattributed_actions: list[str] = []
    claims_without_lineage: list[str] = []
    events: list[dict[str, Any]] = []
    known_query_refs = {
        tuple(ref.split(":", 1))
        for ref in graph.get("known_query_refs", [])
        if isinstance(ref, str) and ":" in ref
    }
    known_web_refs = {
        tuple(ref.split(":", 1))
        for ref in graph.get("known_web_refs", [])
        if isinstance(ref, str) and ":" in ref
    }
    known_eval_refs = {
        ref for ref in graph.get("known_evaluation_refs", []) if isinstance(ref, str)
    }

    query_count = sum(len(bundle.get("executed_queries", [])) for bundle in round_bundles if isinstance(bundle, dict))
    web_count = sum(len(bundle.get("executed_web_searches", [])) for bundle in round_bundles if isinstance(bundle, dict))
    execution_tool_usage_count = sum(
        1
        for item in trace.get("tool_usages", [])
        if isinstance(item, dict) and item.get("stage") == "execution"
    )
    if query_count > 0 and execution_tool_usage_count == 0:
        unattributed_actions.append("query_execution_without_tool_usage_envelope")
        _append_event(
            events,
            severity="soft_deviation",
            message="Some executed queries do not have a matching tool usage envelope.",
            ref="protocol_trace.tool_usages",
        )
    if web_count > 0 and execution_tool_usage_count == 0:
        unattributed_actions.append("web_search_without_tool_usage_envelope")
        _append_event(
            events,
            severity="soft_deviation",
            message="Some executed web searches do not have a matching tool usage envelope.",
            ref="protocol_trace.tool_usages",
        )

    _audit_stage_artifact_presence(slug, trace, round_bundles, events, session_id=session_id)
    _audit_evaluation_decision_lineage(trace, round_bundles, events)
    for gate_result in trace.get("gate_results", []):
        if not isinstance(gate_result, dict):
            continue
        refs = gate_result.get("refs")
        if isinstance(refs, list) and refs:
            ref = str(refs[0])
        else:
            ref = str(gate_result.get("gate_id") or "protocol_gate")
        _append_event(
            events,
            severity=str(gate_result.get("severity") or "soft_deviation"),
            message=f"[{gate_result.get('gate_id', 'protocol_gate')}] {gate_result.get('message', 'Protocol gate triggered.')}",
            ref=ref,
        )

    for claim in final_answer.get("supported_claims", []) if isinstance(final_answer, dict) else []:
        if not isinstance(claim, dict):
            claims_without_lineage.append(str(claim))
            continue
        claim_ref = str(claim.get("claim_ref") or claim.get("claim") or "unsupported_claim")
        query_refs = claim.get("query_refs", [])
        web_refs = claim.get("web_refs", [])
        evaluation_refs = claim.get("evaluation_refs", [])
        if not query_refs and not web_refs and not evaluation_refs:
            claims_without_lineage.append(claim_ref)
            continue
        for query_ref in query_refs:
            ref_tuple = (query_ref.get("round_id"), query_ref.get("query_id")) if isinstance(query_ref, dict) else None
            if not ref_tuple or ref_tuple not in known_query_refs:
                claims_without_lineage.append(claim_ref)
                break
        for evaluation_ref in evaluation_refs:
            if evaluation_ref not in known_eval_refs:
                claims_without_lineage.append(claim_ref)
                break
        for web_ref in web_refs:
            ref_tuple = (web_ref.get("round_id"), web_ref.get("search_id")) if isinstance(web_ref, dict) else None
            if not ref_tuple or ref_tuple not in known_web_refs:
                claims_without_lineage.append(claim_ref)
                break

    claims_without_lineage = sorted(set(claims_without_lineage))
    for claim_ref in claims_without_lineage:
        _append_event(
            events,
            severity="strict_violation",
            message="Supported claim is missing valid evidence lineage.",
            ref=claim_ref,
        )

    evidence_lineage_coverage = {
        "supported_claims": len(final_answer.get("supported_claims", [])) if isinstance(final_answer, dict) else 0,
        "claims_with_lineage": max(
            0,
            (len(final_answer.get("supported_claims", [])) if isinstance(final_answer, dict) else 0) - len(claims_without_lineage),
        ),
        "executed_queries": query_count,
        "executed_web_searches": web_count,
        "tool_usage_envelopes": len(trace.get("tool_usages", [])),
    }

    strict_count = sum(1 for event in events if event["severity"] == "strict_violation")
    warning_count = sum(1 for event in events if event["severity"] != "strict_violation")
    verdict = "fail" if strict_count else ("warn" if events else "pass")
    report = {
        "session_slug": slug,
        "generation_id": active_generation_id,
        "chosen_skill": trace.get("chosen_skill", CHOSEN_SKILL),
        "protocol_mode": trace.get("protocol_mode", PROTOCOL_MODE),
        "stage_timeline": trace.get("stage_timeline", []),
        "attributable_actions": attributable_actions,
        "unattributed_actions": unattributed_actions,
        "evidence_lineage_coverage": evidence_lineage_coverage,
        "claims_without_lineage": claims_without_lineage,
        "events": events,
        "final_verdict": verdict,
        "session_state_snapshot": {
            "transition_mode": session_state.get("transition_mode"),
            "protocol_warnings_count": int(session_state.get("protocol_warnings_count", 0)) + warning_count,
            "strict_violation_count": int(session_state.get("strict_violation_count", 0)) + strict_count,
        },
    }
    validate_compliance_report(report)
    persist_artifact(
        slug,
        COMPLIANCE_REPORT_FILENAME,
        report,
        session_id=session_id,
        strict_session=bool(session_id),
    )
    return report
