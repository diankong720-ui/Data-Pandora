from __future__ import annotations

import re
import time
from typing import Any

from runtime.compliance import append_protocol_gate_result
from runtime.contracts import normalize_open_questions, stable_payload_hash
from runtime.persistence import list_round_bundles, read_artifact


PROTOCOL_GATE_ENFORCEMENT: dict[str, str] = {
    "intent.no_sql": "strict",
    "intent.no_schema_leak": "observe",
    "intent.clarification_block": "strict",
    "discovery.stage_purity": "strict",
    "discovery.semantic_overreach": "observe",
    "plan.audit_first": "strict",
    "plan.no_future_script": "observe",
    "execution.contract_immutable": "strict",
    "execution.query_membership": "strict",
    "evaluation.open_question_materiality": "observe",
    "finalization.claim_overreach": "observe",
    "visualization.reference_integrity": "strict",
}

AUDIT_OPERATOR_ALLOWLIST = {
    "audit",
    "audit_baseline",
    "audit_verification",
}

FORBIDDEN_DISCOVERY_KEYS = {
    "supported_claims",
    "recommended_next_action",
    "continuation_guidance",
    "operator_id",
    "hypothesis_board",
    "hypothesis_ranking",
    "query_plan",
    "final_answer",
    "headline_verified",
    "root_cause",
    "delta",
    "sql",
}

SQL_PATTERN = re.compile(r"\b(select|with|insert|update|delete)\b[\s\S]{0,120}\bfrom\b", re.IGNORECASE)
SCHEMA_HINT_PATTERN = re.compile(
    r"(\btable\b|\bcolumn\b|\bfield\b|\bjoin\b|[A-Za-z_][\w]*\.[A-Za-z_][\w]*)",
    re.IGNORECASE,
)
SEMANTIC_GUARD_PATTERN_KEYS = (
    "discovery.semantic_overreach",
    "plan.no_future_script",
    "finalization.claim_overreach",
)
_SEMANTIC_GUARD_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    key: [] for key in SEMANTIC_GUARD_PATTERN_KEYS
}
_SEMANTIC_GUARD_MODE = "disabled"


class ProtocolViolation(ValueError):
    """Base class for protocol gate failures."""


class StagePurityViolation(ProtocolViolation):
    """Raised when a stage emits payloads outside its allowed responsibility boundary."""


class ForbiddenFieldViolation(ProtocolViolation):
    """Raised when forbidden fields or executable content are detected in a stage payload."""


class SemanticOverreachViolation(ProtocolViolation):
    """Raised when a payload contains non-local conclusions or unsupported semantics."""


class LineageIntegrityViolation(ProtocolViolation):
    """Raised when references point outside the persisted session lineage."""


def configure_semantic_guard_policy(policy: dict[str, Any] | None = None) -> None:
    """Configure host-owned semantic regex guards as disabled, observe, or strict."""
    global _SEMANTIC_GUARD_MODE, _SEMANTIC_GUARD_PATTERNS
    policy = policy or {"mode": "disabled", "patterns": {}}
    mode = policy.get("mode", "disabled")
    if mode not in {"disabled", "observe", "strict"}:
        raise ValueError("semantic_guard_policy.mode must be disabled, observe, or strict.")
    patterns = policy.get("patterns", {})
    if mode == "disabled":
        patterns = {}
    if not isinstance(patterns, dict):
        raise ValueError("semantic_guard_policy.patterns must be an object.")
    configured: dict[str, list[re.Pattern[str]]] = {
        key: [] for key in SEMANTIC_GUARD_PATTERN_KEYS
    }
    for gate_id, raw_patterns in (patterns or {}).items():
        if gate_id not in configured:
            raise ValueError(f"Unsupported semantic guard gate id: {gate_id}")
        if not isinstance(raw_patterns, list):
            raise ValueError(f"Semantic guard patterns for {gate_id} must be a list.")
        compiled: list[re.Pattern[str]] = []
        for index, raw_pattern in enumerate(raw_patterns, start=1):
            if not isinstance(raw_pattern, str) or not raw_pattern.strip():
                raise ValueError(
                    f"Semantic guard pattern {gate_id}[{index}] must be a non-empty string."
                )
            compiled.append(re.compile(raw_pattern, re.IGNORECASE))
        configured[gate_id] = compiled
    for gate_id in SEMANTIC_GUARD_PATTERN_KEYS:
        PROTOCOL_GATE_ENFORCEMENT[gate_id] = "strict" if mode == "strict" else "observe"
    _SEMANTIC_GUARD_MODE = str(mode)
    _SEMANTIC_GUARD_PATTERNS = configured


def configure_semantic_guard_patterns(patterns: dict[str, list[str]] | None = None) -> None:
    """
    Configure optional regex-based semantic guard patterns in observe mode.

    These checks are intentionally host-configured rather than runtime-hardcoded:
    they can be useful as soft audit signals, but they are too domain- and
    language-sensitive to remain fixed inside the runtime.
    """
    configure_semantic_guard_policy(
        {"mode": "observe" if patterns else "disabled", "patterns": patterns or {}}
    )


def _match_semantic_guard_refs(
    gate_id: str,
    string_fields: list[tuple[str, str]],
) -> list[str]:
    patterns = _SEMANTIC_GUARD_PATTERNS.get(gate_id, [])
    if _SEMANTIC_GUARD_MODE == "disabled" or not patterns:
        return []
    refs: list[str] = []
    for path, value in string_fields:
        if any(pattern.search(value) for pattern in patterns):
            refs.append(path)
    return refs


def _walk_strings(payload: Any, *, path: str = "", skip_keys: set[str] | None = None) -> list[tuple[str, str]]:
    skip = skip_keys or set()
    found: list[tuple[str, str]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            next_path = f"{path}.{key}" if path else str(key)
            if str(key) in skip:
                continue
            found.extend(_walk_strings(value, path=next_path, skip_keys=skip))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            next_path = f"{path}[{index}]"
            found.extend(_walk_strings(value, path=next_path, skip_keys=skip))
    elif isinstance(payload, str):
        found.append((path, payload))
    return found


def _walk_keys(payload: Any, *, prefix: str = "") -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            keys.append((path, str(key)))
            keys.extend(_walk_keys(value, prefix=path))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            path = f"{prefix}[{index}]"
            keys.extend(_walk_keys(value, prefix=path))
    return keys


def _record_gate(
    slug: str,
    *,
    gate_id: str,
    severity: str,
    message: str,
    refs: list[str] | None = None,
    session_id: str | None = None,
    exception_type: type[ProtocolViolation] = ProtocolViolation,
) -> None:
    enforcement = PROTOCOL_GATE_ENFORCEMENT.get(gate_id, "observe")
    blocked = enforcement == "strict" and severity in {"strict_violation", "soft_deviation"}
    append_protocol_gate_result(
        slug,
        {
            "gate_id": gate_id,
            "severity": severity,
            "outcome": "blocked" if blocked else "observed",
            "message": message,
            "refs": refs or [],
            "timestamp": time.time(),
        },
        session_id=session_id,
    )
    if blocked:
        raise exception_type(message)


def validate_intent_stage_payload(
    slug: str,
    intent_result: dict[str, Any],
    *,
    session_id: str | None = None,
) -> None:
    normalized_intent = intent_result.get("normalized_intent", {})
    string_fields = _walk_strings(normalized_intent, skip_keys={"raw_question"})

    sql_refs = [path for path, value in string_fields if SQL_PATTERN.search(value)]
    if sql_refs:
        _record_gate(
            slug,
            gate_id="intent.no_sql",
            severity="strict_violation",
            message="Intent stage emitted SQL-like content; Stage 1 must remain semantic-only.",
            refs=sql_refs,
            session_id=session_id,
            exception_type=ForbiddenFieldViolation,
        )

    schema_refs = [path for path, value in string_fields if SCHEMA_HINT_PATTERN.search(value)]
    if schema_refs:
        _record_gate(
            slug,
            gate_id="intent.no_schema_leak",
            severity="soft_deviation",
            message="Intent stage appears to contain physical schema hints; keep Stage 1 semantic-only.",
            refs=schema_refs,
            session_id=session_id,
            exception_type=StagePurityViolation,
        )


def validate_intent_ready_for_downstream(
    slug: str,
    *,
    session_id: str | None = None,
) -> None:
    intent = read_artifact(slug, "intent.json", session_id=session_id, strict_session=True)
    if not isinstance(intent, dict):
        return
    if intent.get("clarification_needed") is True:
        _record_gate(
            slug,
            gate_id="intent.clarification_block",
            severity="strict_violation",
            message="Intent still requires clarification; downstream stages must stop until clarification is resolved.",
            refs=["intent.json"],
            session_id=session_id,
            exception_type=StagePurityViolation,
        )


def validate_discovery_stage_payload(
    slug: str,
    discovery_bundle: dict[str, Any],
    *,
    session_id: str | None = None,
) -> None:
    key_refs = [path for path, key in _walk_keys(discovery_bundle) if key in FORBIDDEN_DISCOVERY_KEYS]
    string_fields = _walk_strings(discovery_bundle)
    sql_refs = [path for path, value in string_fields if SQL_PATTERN.search(value)]
    strict_refs = key_refs + sql_refs
    if strict_refs:
        _record_gate(
            slug,
            gate_id="discovery.stage_purity",
            severity="strict_violation",
            message="Discovery stage emitted downstream decision content or executable SQL.",
            refs=strict_refs,
            session_id=session_id,
            exception_type=StagePurityViolation,
        )

    semantic_refs = _match_semantic_guard_refs(
        "discovery.semantic_overreach",
        string_fields,
    )
    if semantic_refs:
        _record_gate(
            slug,
            gate_id="discovery.semantic_overreach",
            severity="soft_deviation",
            message="Discovery stage language looks conclusion-oriented; keep Stage 2 focused on environment facts and capabilities.",
            refs=semantic_refs,
            session_id=session_id,
            exception_type=SemanticOverreachViolation,
        )


def validate_plan_stage_payload(
    slug: str,
    plan_bundle: dict[str, Any],
    *,
    session_id: str | None = None,
) -> None:
    round_1_contract = plan_bundle.get("round_1_contract", {})
    operator_id = str(round_1_contract.get("operator_id") or "")
    if operator_id not in AUDIT_OPERATOR_ALLOWLIST and not operator_id.startswith("audit_"):
        _record_gate(
            slug,
            gate_id="plan.audit_first",
            severity="strict_violation",
            message="Round 1 operator must be an audit operator.",
            refs=["round_1_contract.operator_id"],
            session_id=session_id,
            exception_type=StagePurityViolation,
        )

    future_script_refs: list[str] = []
    for index, note in enumerate(plan_bundle.get("planning_notes", [])):
        if not isinstance(note, str):
            continue
        if _match_semantic_guard_refs(
            "plan.no_future_script",
            [(f"planning_notes[{index}]", note)],
        ):
            future_script_refs.append(f"planning_notes[{index}]")
    if future_script_refs:
        _record_gate(
            slug,
            gate_id="plan.no_future_script",
            severity="soft_deviation",
            message="Planning notes appear to pre-script later rounds; keep Stage 3 limited to candidate space and Round 1.",
            refs=future_script_refs,
            session_id=session_id,
            exception_type=SemanticOverreachViolation,
        )


def validate_execution_stage_payload(
    slug: str,
    contract: dict[str, Any],
    executed_queries: list[dict[str, Any]],
    *,
    expected_contract_hash: str,
    session_id: str | None = None,
) -> None:
    if stable_payload_hash(contract) != expected_contract_hash:
        _record_gate(
            slug,
            gate_id="execution.contract_immutable",
            severity="strict_violation",
            message="Execution contract mutated after validation; runtime may execute only the frozen contract.",
            refs=[str(contract.get("contract_id") or "contract")],
            session_id=session_id,
            exception_type=ForbiddenFieldViolation,
        )

    expected_query_ids = [str(query.get("query_id")) for query in contract.get("queries", []) if isinstance(query, dict)]
    actual_query_ids = [str(query.get("query_id")) for query in executed_queries if isinstance(query, dict)]
    if expected_query_ids != actual_query_ids:
        _record_gate(
            slug,
            gate_id="execution.query_membership",
            severity="strict_violation",
            message="Executed query ids do not match the frozen InvestigationContract query set.",
            refs=[str(contract.get("contract_id") or "contract")],
            session_id=session_id,
            exception_type=LineageIntegrityViolation,
        )


def validate_evaluation_stage_payload(
    slug: str,
    evaluation_result: dict[str, Any],
    *,
    session_id: str | None = None,
) -> None:
    open_questions = normalize_open_questions(
        evaluation_result.get("open_questions", []),
        label="RoundEvaluationResult.open_questions",
    )
    materiality_refs: list[str] = []
    for index, question in enumerate(open_questions):
        if question.get("residual_component") == "unspecified":
            materiality_refs.append(f"open_questions[{index}].residual_component")
        if question.get("why_unresolved") == "Not explicitly provided.":
            materiality_refs.append(f"open_questions[{index}].why_unresolved")
    if materiality_refs:
        _record_gate(
            slug,
            gate_id="evaluation.open_question_materiality",
            severity="soft_deviation",
            message="Evaluation open questions should remain materially grounded in residual reduction, not legacy placeholders.",
            refs=materiality_refs,
            session_id=session_id,
            exception_type=SemanticOverreachViolation,
        )


def validate_finalization_stage_payload(
    slug: str,
    final_answer: dict[str, Any],
    report_evidence: dict[str, Any],
    *,
    session_id: str | None = None,
) -> None:
    entries = report_evidence.get("entries", []) if isinstance(report_evidence, dict) else []
    evidence_text_by_query_ref: dict[tuple[str, str], list[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if not isinstance(text, str):
            continue
        for query_ref in entry.get("query_refs", []):
            if not isinstance(query_ref, dict):
                continue
            round_id = query_ref.get("round_id")
            query_id = query_ref.get("query_id")
            if isinstance(round_id, str) and isinstance(query_id, str):
                evidence_text_by_query_ref.setdefault((round_id, query_id), []).append(text)

    semantic_claim_patterns = _SEMANTIC_GUARD_PATTERNS.get("finalization.claim_overreach", [])
    if not semantic_claim_patterns:
        return

    overreach_refs: list[str] = []
    for index, claim in enumerate(final_answer.get("supported_claims", [])):
        if not isinstance(claim, dict):
            continue
        claim_text = claim.get("claim")
        if not isinstance(claim_text, str) or not any(
            pattern.search(claim_text) for pattern in semantic_claim_patterns
        ):
            continue
        linked_text = " ".join(
            text
            for query_ref in claim.get("query_refs", [])
            if isinstance(query_ref, dict)
            for text in evidence_text_by_query_ref.get((query_ref.get("round_id"), query_ref.get("query_id")), [])
        )
        if linked_text and not any(
            pattern.search(linked_text) for pattern in semantic_claim_patterns
        ):
            overreach_refs.append(f"supported_claims[{index}]")
    if overreach_refs:
        _record_gate(
            slug,
            gate_id="finalization.claim_overreach",
            severity="soft_deviation",
            message="Finalization claim text appears to introduce quantitative or time-window detail not visible in linked evidence text.",
            refs=overreach_refs,
            session_id=session_id,
            exception_type=SemanticOverreachViolation,
        )


def validate_chart_spec_stage_payload(
    slug: str,
    chart_spec_bundle: dict[str, Any],
    *,
    session_id: str | None = None,
) -> None:
    round_bundles = list_round_bundles(
        slug,
        session_id=session_id,
        generation_id=None,
        strict_session=bool(session_id),
    )
    known_query_refs: set[tuple[str, str]] = set()
    for bundle in round_bundles:
        if not isinstance(bundle, dict):
            continue
        evaluation = bundle.get("evaluation")
        round_id = evaluation.get("round_id") if isinstance(evaluation, dict) else None
        if not isinstance(round_id, str):
            continue
        for query in bundle.get("executed_queries", []):
            if not isinstance(query, dict):
                continue
            query_id = query.get("query_id")
            if isinstance(query_id, str) and query_id:
                known_query_refs.add((round_id, query_id))

    report_evidence = read_artifact(slug, "report_evidence.json", session_id=session_id, strict_session=bool(session_id)) or {}
    known_evidence_refs = {
        str(entry.get("evidence_ref"))
        for entry in report_evidence.get("entries", [])
        if isinstance(entry, dict) and isinstance(entry.get("evidence_ref"), str) and entry.get("evidence_ref")
    }

    invalid_refs: list[str] = []
    for index, spec in enumerate(chart_spec_bundle.get("specs", [])):
        if not isinstance(spec, dict):
            continue
        source_query_ref = spec.get("source_query_ref")
        if not isinstance(source_query_ref, dict):
            invalid_refs.append(f"specs[{index}].source_query_ref")
        else:
            query_key = (source_query_ref.get("round_id"), source_query_ref.get("query_id"))
            if query_key not in known_query_refs:
                invalid_refs.append(f"specs[{index}].source_query_ref")
        for ref_index, query_ref in enumerate(spec.get("query_refs", [])):
            if not isinstance(query_ref, dict):
                invalid_refs.append(f"specs[{index}].query_refs[{ref_index}]")
                continue
            query_key = (query_ref.get("round_id"), query_ref.get("query_id"))
            if query_key not in known_query_refs:
                invalid_refs.append(f"specs[{index}].query_refs[{ref_index}]")
        for ref_index, evidence_ref in enumerate(spec.get("evidence_refs", [])):
            if evidence_ref not in known_evidence_refs:
                invalid_refs.append(f"specs[{index}].evidence_refs[{ref_index}]")

    if invalid_refs:
        _record_gate(
            slug,
            gate_id="visualization.reference_integrity",
            severity="strict_violation",
            message="Chart specs must reference persisted query lineage and report evidence from the current session.",
            refs=invalid_refs,
            session_id=session_id,
            exception_type=LineageIntegrityViolation,
        )
