from __future__ import annotations

import hashlib
import json
from typing import Any

QUESTION_STYLES = {"abstract", "operational", "comparative"}
BUSINESS_OBJECT_ENTITY_TYPES = {"business_scope", "channel", "product", "region", "seller"}
TIME_GRAINS = {"day", "week", "month", "quarter", "year", "rolling_window", "unknown"}
COMPARISON_SCOPE_TYPES = {"none", "mom", "yoy", "explicit", "custom"}
MAPPING_CONFIDENCE_LEVELS = {"high", "low"}
COMPARISON_FEASIBILITY_STATUSES = {"supported", "partial", "blocked"}
WAREHOUSE_LOAD_STATUSES = {"normal", "constrained", "degraded"}
QUALITY_REPORT_STATUSES = {"pass", "warn", "block"}
EVIDENCE_STATUSES = {"available", "partial", "blocked"}
JOIN_PATH_STATUSES = {"validated", "partial", "blocked"}
HYPOTHESIS_CLASSES = {"audit", "driver"}
HYPOTHESIS_LAYERS = {"audit", "demand", "value", "structure", "fulfillment"}
SCHEMA_FEASIBILITY_STATUSES = {"feasible", "not_testable"}
HYPOTHESIS_STATUSES = {"proposed", "supported", "weakened", "rejected", "not_tested", "blocked_by_load"}
EVIDENCE_LANES = {"warehouse_sql", "web_search"}
WEB_SEARCH_STATUSES = {"success", "failed", "timeout", "blocked"}
WEB_RECALL_SCORE_FIELDS = {
    "temporal_fit",
    "entity_fit",
    "source_authority",
    "source_independence",
    "corroboration_strength",
    "specificity",
    "freshness",
    "retrieval_diversity",
    "contradiction_signal",
    "actionability",
}
WEB_RECALL_CONCLUSIONS = {
    "usable_supporting",
    "usable_contradicting",
    "usable_contextual",
    "needs_refinement",
    "insufficient",
}
CONTINUATION_ACTIONS = {"refine", "pivot"}
TRANSITION_MODES = {"normal", "rework", "restart"}
STAGE_DECISION_PHASES = {"enter", "complete", "restart"}
ACTION_RATIONALE_TYPES = {
    "schema_probe",
    "contract_execution",
    "evaluation_continuation",
    "final_answer_synthesis",
    "artifact_persistence",
    "orchestrator_control",
}
COMPLIANCE_SEVERITIES = {"strict_violation", "soft_deviation", "efficiency_drift"}
COMPLIANCE_VERDICTS = {"pass", "warn", "fail"}
VISUALIZATION_COVERAGE_STATUSES = {
    "charts_generated",
    "text_only",
    "no_chartable_evidence",
}
REPORT_EVIDENCE_SECTIONS = {"supported_claims", "contradictions", "residual_context"}
DEFAULT_MAX_ROUNDS = 20


def _require_fields(payload: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    missing = [field for field in fields if field not in payload]
    if missing:
        raise ValueError(f"{label} missing required fields: {', '.join(missing)}")


def _require_enum(value: Any, legal_values: set[str], label: str) -> None:
    if value not in legal_values:
        raise ValueError(f"{label} is invalid.")


def _require_non_empty_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")


def _require_non_empty_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list.")
    normalized: list[str] = []
    for index, item in enumerate(value, start=1):
        _require_non_empty_string(item, f"{label}[{index}]")
        normalized.append(str(item).strip())
    return normalized


def _require_non_empty_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{label} must be a non-empty object.")
    return value


def _require_non_negative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer.")
    return value


def _normalize_change_note(value: Any, label: str) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return normalized
    elif isinstance(value, list):
        parts: list[str] = []
        for index, item in enumerate(value, start=1):
            _require_non_empty_string(item, f"{label}[{index}]")
            parts.append(str(item).strip())
        if parts:
            return "; ".join(parts)
    raise ValueError(f"{label} must be a non-empty string or list of non-empty strings.")


def normalize_open_question(question: Any, *, index: int) -> dict[str, Any]:
    if isinstance(question, str):
        text = question.strip()
        if not text:
            raise ValueError("OpenQuestion string entries must be non-empty.")
        return {
            "question_id": f"legacy_open_question_{index}",
            "text": text,
            "residual_component": "unspecified",
            "priority": index,
            "why_unresolved": "Legacy open question migrated from string form.",
        }
    if not isinstance(question, dict):
        raise ValueError("OpenQuestion entries must be strings or objects.")
    normalized = dict(question)
    text = normalized.get("text")
    _require_non_empty_string(text, f"OpenQuestion[{index}].text")
    question_id = normalized.get("question_id")
    if not isinstance(question_id, str) or not question_id.strip():
        normalized["question_id"] = f"open_question_{index}"
    residual_component = normalized.get("residual_component")
    if not isinstance(residual_component, str) or not residual_component.strip():
        normalized["residual_component"] = "unspecified"
    priority = normalized.get("priority")
    if not isinstance(priority, int) or priority <= 0:
        normalized["priority"] = index
    why_unresolved = normalized.get("why_unresolved")
    if not isinstance(why_unresolved, str) or not why_unresolved.strip():
        normalized["why_unresolved"] = "Not explicitly provided."
    normalized["text"] = str(text).strip()
    normalized["question_id"] = str(normalized["question_id"]).strip()
    normalized["residual_component"] = str(normalized["residual_component"]).strip()
    normalized["why_unresolved"] = str(normalized["why_unresolved"]).strip()
    return normalized


def normalize_open_questions(open_questions: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(open_questions, list):
        raise ValueError(f"{label} must be a list.")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, question in enumerate(open_questions, start=1):
        item = normalize_open_question(question, index=index)
        question_id = item["question_id"]
        if question_id in seen_ids:
            raise ValueError(f"{label} contains duplicate question_id values.")
        seen_ids.add(question_id)
        normalized.append(item)
    return normalized


def open_question_ids(open_questions: Any, *, label: str) -> list[str]:
    return [item["question_id"] for item in normalize_open_questions(open_questions, label=label)]


def stable_payload_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def validate_normalized_intent(intent: dict[str, Any]) -> None:
    required_fields = (
        "intent_id",
        "raw_question",
        "question_style",
        "problem_type",
        "primary_problem_type",
        "business_object",
        "core_metric",
        "time_scope",
        "comparison_scope",
        "dimensions",
        "filters",
        "intent_profile",
        "problem_type_scores",
        "domain_pack_id",
        "mapping_confidence",
        "clarification_needed",
        "clarification_reasons",
        "clarification_request",
    )
    _require_fields(intent, required_fields, "NormalizedIntent")
    _require_enum(intent["question_style"], QUESTION_STYLES, "NormalizedIntent.question_style")
    _require_enum(intent["mapping_confidence"], MAPPING_CONFIDENCE_LEVELS, "NormalizedIntent.mapping_confidence")

    business_object = intent["business_object"]
    if not isinstance(business_object, dict):
        raise ValueError("NormalizedIntent.business_object must be an object.")
    _require_fields(business_object, ("label", "entity_type"), "NormalizedIntent.business_object")
    _require_enum(
        business_object["entity_type"],
        BUSINESS_OBJECT_ENTITY_TYPES,
        "NormalizedIntent.business_object.entity_type",
    )

    time_scope = intent["time_scope"]
    if not isinstance(time_scope, dict) or not isinstance(time_scope.get("primary"), dict):
        raise ValueError("NormalizedIntent.time_scope.primary must be an object.")
    _require_fields(
        time_scope["primary"],
        ("label", "start", "end", "grain"),
        "NormalizedIntent.time_scope.primary",
    )
    _require_enum(
        time_scope["primary"]["grain"],
        TIME_GRAINS,
        "NormalizedIntent.time_scope.primary.grain",
    )

    comparison_scope = intent["comparison_scope"]
    if not isinstance(comparison_scope, dict):
        raise ValueError("NormalizedIntent.comparison_scope must be an object.")
    _require_fields(comparison_scope, ("type", "windows"), "NormalizedIntent.comparison_scope")
    _require_enum(
        comparison_scope["type"],
        COMPARISON_SCOPE_TYPES,
        "NormalizedIntent.comparison_scope.type",
    )
    if comparison_scope["type"] != "none" and not comparison_scope["windows"]:
        raise ValueError("NormalizedIntent.comparison_scope.windows cannot be empty unless type is 'none'.")

    if intent["clarification_needed"] and intent["clarification_request"] is None:
        raise ValueError(
            "NormalizedIntent.clarification_request must be non-null when clarification_needed is true."
        )


def validate_intent_recognition_result(result: dict[str, Any]) -> None:
    _require_fields(result, ("normalized_intent", "pack_gaps"), "IntentRecognitionResult")
    if not isinstance(result["pack_gaps"], list):
        raise ValueError("IntentRecognitionResult.pack_gaps must be an array.")
    normalized_intent = result["normalized_intent"]
    if not isinstance(normalized_intent, dict):
        raise ValueError("IntentRecognitionResult.normalized_intent must be an object.")
    validate_normalized_intent(normalized_intent)


def validate_data_context_bundle(bundle: dict[str, Any]) -> None:
    required_fields = (
        "intent_id",
        "environment_scan",
        "schema_map",
        "metric_mapping",
        "time_fields",
        "dimension_fields",
        "supported_dimension_capabilities",
        "joinability",
        "comparison_feasibility",
        "warehouse_load_status",
        "report_conflict_hint",
        "quality_report",
        "evidence_status",
    )
    _require_fields(bundle, required_fields, "DataContextBundle")
    _require_enum(
        bundle["warehouse_load_status"],
        WAREHOUSE_LOAD_STATUSES,
        "DataContextBundle.warehouse_load_status",
    )
    _require_enum(bundle["evidence_status"], EVIDENCE_STATUSES, "DataContextBundle.evidence_status")
    comparison_feasibility = bundle["comparison_feasibility"]
    if not isinstance(comparison_feasibility, dict):
        raise ValueError("DataContextBundle.comparison_feasibility must be an object.")
    _require_fields(
        comparison_feasibility,
        ("status", "reason"),
        "DataContextBundle.comparison_feasibility",
    )
    _require_enum(
        comparison_feasibility["status"],
        COMPARISON_FEASIBILITY_STATUSES,
        "DataContextBundle.comparison_feasibility.status",
    )
    quality_report = bundle["quality_report"]
    if not isinstance(quality_report, dict):
        raise ValueError("DataContextBundle.quality_report must be an object.")
    _require_fields(quality_report, ("status", "issues"), "DataContextBundle.quality_report")
    _require_enum(
        quality_report["status"],
        QUALITY_REPORT_STATUSES,
        "DataContextBundle.quality_report.status",
    )
    joinability = bundle["joinability"]
    if not isinstance(joinability, dict):
        raise ValueError("DataContextBundle.joinability must be an object.")
    if not isinstance(joinability.get("join_paths"), list):
        raise ValueError("DataContextBundle.joinability.join_paths must be a list.")
    for join_path in joinability["join_paths"]:
        if not isinstance(join_path, dict):
            raise ValueError("DataContextBundle.joinability.join_paths entries must be objects.")
        _require_fields(
            join_path,
            ("from_table", "to_table", "join_key", "status"),
            "DataContextBundle.joinability.join_path",
        )
        _require_enum(
            join_path["status"],
            JOIN_PATH_STATUSES,
            "DataContextBundle.joinability.join_path.status",
        )


def validate_query_execution_request(request: dict[str, Any]) -> None:
    required_fields = (
        "query_id",
        "description",
        "sql",
        "workspace",
        "output_name",
        "cache_policy",
        "cost_class",
    )
    _require_fields(request, required_fields, "QueryExecutionRequest")
    if "persist_result_rows" in request and not isinstance(request["persist_result_rows"], bool):
        raise ValueError("QueryExecutionRequest.persist_result_rows must be a boolean when provided.")
    _require_non_empty_string(request["query_id"], "QueryExecutionRequest.query_id")
    _require_non_empty_string(request["description"], "QueryExecutionRequest.description")
    _require_non_empty_string(request["sql"], "QueryExecutionRequest.sql")
    _require_non_empty_string(request["workspace"], "QueryExecutionRequest.workspace")
    _require_non_empty_string(request["output_name"], "QueryExecutionRequest.output_name")
    _require_non_empty_string(request["cost_class"], "QueryExecutionRequest.cost_class")
    if request["cache_policy"] not in {"bypass", "allow_read", "require_read"}:
        raise ValueError("QueryExecutionRequest.cache_policy is invalid.")
    if "addresses_open_question_ids" in request:
        _require_non_empty_string_list(
            request["addresses_open_question_ids"],
            "QueryExecutionRequest.addresses_open_question_ids",
        )
    if "addresses_residual_component" in request:
        _require_non_empty_string(
            request["addresses_residual_component"],
            "QueryExecutionRequest.addresses_residual_component",
        )


def _has_residual_binding(request: dict[str, Any], label: str) -> bool:
    has_open_question_binding = bool(request.get("addresses_open_question_ids"))
    has_residual_binding = isinstance(request.get("addresses_residual_component"), str) and bool(
        str(request.get("addresses_residual_component")).strip()
    )
    if has_open_question_binding:
        _require_non_empty_string_list(
            request["addresses_open_question_ids"],
            f"{label}.addresses_open_question_ids",
        )
    if has_residual_binding:
        _require_non_empty_string(
            request["addresses_residual_component"],
            f"{label}.addresses_residual_component",
        )
    return has_open_question_binding or has_residual_binding


def validate_web_search_request(request: dict[str, Any]) -> None:
    required_fields = (
        "search_id",
        "question",
        "query",
        "time_window",
        "geo_scope",
        "entity_scope",
        "source_policy",
        "freshness_requirement",
        "expected_signal",
    )
    _require_fields(request, required_fields, "WebSearchRequest")
    for field in ("search_id", "question", "query", "expected_signal"):
        _require_non_empty_string(request[field], f"WebSearchRequest.{field}")
    time_window = _require_non_empty_object(request["time_window"], "WebSearchRequest.time_window")
    if not (
        isinstance(time_window.get("label"), str)
        and time_window["label"].strip()
        or isinstance(time_window.get("start"), str)
        and time_window["start"].strip()
        and isinstance(time_window.get("end"), str)
        and time_window["end"].strip()
    ):
        raise ValueError("WebSearchRequest.time_window must include a label or start/end.")
    geo_scope = request["geo_scope"]
    if isinstance(geo_scope, str):
        _require_non_empty_string(geo_scope, "WebSearchRequest.geo_scope")
    else:
        _require_non_empty_object(geo_scope, "WebSearchRequest.geo_scope")
    entity_scope = request["entity_scope"]
    if isinstance(entity_scope, list):
        _require_non_empty_string_list(entity_scope, "WebSearchRequest.entity_scope")
    else:
        _require_non_empty_object(entity_scope, "WebSearchRequest.entity_scope")
    _require_non_empty_object(request["source_policy"], "WebSearchRequest.source_policy")
    freshness_requirement = request["freshness_requirement"]
    if isinstance(freshness_requirement, str):
        _require_non_empty_string(
            freshness_requirement,
            "WebSearchRequest.freshness_requirement",
        )
    else:
        _require_non_empty_object(
            freshness_requirement,
            "WebSearchRequest.freshness_requirement",
        )
    if not _has_residual_binding(request, "WebSearchRequest"):
        raise ValueError("WebSearchRequest must bind to open questions or a residual component.")

    is_refinement = "parent_search_id" in request
    if is_refinement:
        for field in ("parent_search_id", "recall_gap", "refined_question", "expected_new_signal"):
            _require_non_empty_string(request.get(field), f"WebSearchRequest.{field}")
        _require_non_empty_string_list(request.get("changed_axes"), "WebSearchRequest.changed_axes")
        if request["parent_search_id"] == request["search_id"]:
            raise ValueError("WebSearchRequest.parent_search_id must differ from search_id.")


def validate_web_search_result(result: dict[str, Any]) -> None:
    required_fields = (
        "search_id",
        "status",
        "provider",
        "retrieved_at",
        "results",
        "fetched_pages",
        "source_quality_notes",
    )
    _require_fields(result, required_fields, "WebSearchResult")
    _require_non_empty_string(result["search_id"], "WebSearchResult.search_id")
    _require_enum(result["status"], WEB_SEARCH_STATUSES, "WebSearchResult.status")
    _require_non_empty_string(result["provider"], "WebSearchResult.provider")
    if not isinstance(result["retrieved_at"], (int, float)):
        raise ValueError("WebSearchResult.retrieved_at must be numeric.")
    if not isinstance(result["results"], list):
        raise ValueError("WebSearchResult.results must be a list.")
    if not isinstance(result["fetched_pages"], list):
        raise ValueError("WebSearchResult.fetched_pages must be a list.")
    if not isinstance(result["source_quality_notes"], list):
        raise ValueError("WebSearchResult.source_quality_notes must be a list.")
    for item in result["results"]:
        if not isinstance(item, dict):
            raise ValueError("WebSearchResult.results entries must be objects.")
        _require_fields(item, ("title", "url"), "WebSearchResult.result")
        _require_non_empty_string(item["title"], "WebSearchResult.result.title")
        _require_non_empty_string(item["url"], "WebSearchResult.result.url")


def validate_web_recall_assessment(assessment: dict[str, Any]) -> None:
    required_fields = (
        "assessment_id",
        "search_id",
        "scores",
        "conclusion",
        "rationale",
        "needs_refinement",
        "refinement_requests",
    )
    _require_fields(assessment, required_fields, "WebRecallAssessment")
    _require_non_empty_string(assessment["assessment_id"], "WebRecallAssessment.assessment_id")
    _require_non_empty_string(assessment["search_id"], "WebRecallAssessment.search_id")
    scores = assessment["scores"]
    if not isinstance(scores, dict):
        raise ValueError("WebRecallAssessment.scores must be an object.")
    missing_scores = WEB_RECALL_SCORE_FIELDS - set(scores)
    if missing_scores:
        raise ValueError(
            "WebRecallAssessment.scores missing fields: " + ", ".join(sorted(missing_scores))
        )
    for field in WEB_RECALL_SCORE_FIELDS:
        value = scores[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 5:
            raise ValueError(f"WebRecallAssessment.scores.{field} must be an integer from 0 to 5.")
    _require_enum(assessment["conclusion"], WEB_RECALL_CONCLUSIONS, "WebRecallAssessment.conclusion")
    _require_non_empty_string(assessment["rationale"], "WebRecallAssessment.rationale")
    if not isinstance(assessment["needs_refinement"], bool):
        raise ValueError("WebRecallAssessment.needs_refinement must be a boolean.")
    if assessment["needs_refinement"] and assessment["conclusion"] != "needs_refinement":
        raise ValueError("WebRecallAssessment.needs_refinement=true requires conclusion=needs_refinement.")
    if assessment["conclusion"] == "usable_contradicting":
        _require_non_empty_string(
            assessment.get("contradiction_summary"),
            "WebRecallAssessment.contradiction_summary",
        )
    refinement_requests = assessment["refinement_requests"]
    if not isinstance(refinement_requests, list):
        raise ValueError("WebRecallAssessment.refinement_requests must be a list.")
    for request in refinement_requests:
        if not isinstance(request, dict):
            raise ValueError("WebRecallAssessment.refinement_requests entries must be objects.")
        validate_web_search_request(request)
        if request.get("parent_search_id") != assessment["search_id"]:
            raise ValueError("WebRecallAssessment refinement requests must point to assessment.search_id.")


def validate_web_refs(value: Any, label: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list.")
    for web_ref in value:
        if not isinstance(web_ref, dict):
            raise ValueError(f"{label} entries must be objects.")
        _require_fields(web_ref, ("round_id", "search_id"), f"{label}.entry")
        _require_non_empty_string(web_ref["round_id"], f"{label}.round_id")
        _require_non_empty_string(web_ref["search_id"], f"{label}.search_id")


def validate_investigation_contract(contract: dict[str, Any]) -> None:
    required_fields = (
        "contract_id",
        "round_number",
        "operator_id",
        "target_hypotheses",
        "sql_budget",
        "allowed_cost_classes",
        "queries",
        "pass_conditions",
        "pivot_conditions",
        "max_rounds",
        "notes",
    )
    _require_fields(contract, required_fields, "InvestigationContract")
    _require_non_empty_string(contract["contract_id"], "InvestigationContract.contract_id")
    if not isinstance(contract["round_number"], int) or contract["round_number"] <= 0:
        raise ValueError("InvestigationContract.round_number must be a positive integer.")
    if not isinstance(contract["max_rounds"], int) or contract["max_rounds"] <= 0:
        raise ValueError("InvestigationContract.max_rounds must be a positive integer.")
    if contract["max_rounds"] > DEFAULT_MAX_ROUNDS:
        raise ValueError(
            f"InvestigationContract.max_rounds must not exceed the hard ceiling of {DEFAULT_MAX_ROUNDS}."
        )
    if not isinstance(contract["sql_budget"], int) or contract["sql_budget"] <= 0:
        raise ValueError("InvestigationContract.sql_budget must be a positive integer.")
    if not isinstance(contract["allowed_cost_classes"], list) or not contract["allowed_cost_classes"]:
        raise ValueError("InvestigationContract.allowed_cost_classes must be a non-empty list.")
    allowed_cost_classes = set()
    for value in contract["allowed_cost_classes"]:
        _require_non_empty_string(value, "InvestigationContract.allowed_cost_classes[]")
        allowed_cost_classes.add(value)
    evidence_lanes = contract.get("evidence_lanes")
    if evidence_lanes is not None:
        if not isinstance(evidence_lanes, list) or not evidence_lanes:
            raise ValueError("InvestigationContract.evidence_lanes must be a non-empty list when provided.")
        for lane in evidence_lanes:
            _require_enum(lane, EVIDENCE_LANES, "InvestigationContract.evidence_lanes[]")
    if not isinstance(contract["queries"], list):
        raise ValueError("InvestigationContract.queries must be a list.")
    if len(contract["queries"]) > contract["sql_budget"]:
        raise ValueError("InvestigationContract.queries exceeds InvestigationContract.sql_budget.")
    for query in contract["queries"]:
        if not isinstance(query, dict):
            raise ValueError("InvestigationContract.queries entries must be objects.")
        validate_query_execution_request(query)
        if query["cost_class"] not in allowed_cost_classes:
            raise ValueError("InvestigationContract query cost_class is not allowed by allowed_cost_classes.")
    web_searches = contract.get("web_searches", [])
    if web_searches is None:
        web_searches = []
    if not isinstance(web_searches, list):
        raise ValueError("InvestigationContract.web_searches must be a list when provided.")
    if web_searches:
        if evidence_lanes is None or "web_search" not in evidence_lanes:
            raise ValueError("InvestigationContract.evidence_lanes must include web_search when web_searches are present.")
        if contract["queries"] and "warehouse_sql" not in evidence_lanes:
            raise ValueError("InvestigationContract.evidence_lanes must include warehouse_sql when queries are present.")
        web_search_budget = contract.get("web_search_budget")
        if not isinstance(web_search_budget, int) or web_search_budget <= 0:
            raise ValueError("InvestigationContract.web_search_budget must be a positive integer when web_searches are present.")
        web_refinement_budget = _require_non_negative_int(
            contract.get("web_refinement_budget"),
            "InvestigationContract.web_refinement_budget",
        )
        if len(web_searches) > web_search_budget:
            raise ValueError("InvestigationContract.web_searches exceeds InvestigationContract.web_search_budget.")
        refinement_count = sum(
            1
            for search in web_searches
            if isinstance(search, dict) and isinstance(search.get("parent_search_id"), str)
        )
        if refinement_count > web_refinement_budget:
            raise ValueError("InvestigationContract.web_searches exceeds InvestigationContract.web_refinement_budget.")
    elif evidence_lanes is not None and "web_search" in evidence_lanes:
        _require_non_negative_int(
            contract.get("web_search_budget", 0),
            "InvestigationContract.web_search_budget",
        )
        _require_non_negative_int(
            contract.get("web_refinement_budget", 0),
            "InvestigationContract.web_refinement_budget",
        )
    seen_search_ids: set[str] = set()
    for search in web_searches:
        if not isinstance(search, dict):
            raise ValueError("InvestigationContract.web_searches entries must be objects.")
        validate_web_search_request(search)
        search_id = search["search_id"]
        if search_id in seen_search_ids:
            raise ValueError(f"Duplicate search_id in InvestigationContract: {search_id}")
        seen_search_ids.add(search_id)
        parent_search_id = search.get("parent_search_id")
        if isinstance(parent_search_id, str) and parent_search_id not in seen_search_ids:
            raise ValueError("WebSearchRequest.parent_search_id must reference an earlier search in the same contract.")
    round_number = contract.get("round_number")
    if isinstance(round_number, int) and round_number > 1:
        lineage_fields = (
            "session_slug",
            "intent_id",
            "intent_hash",
            "plan_hash",
            "parent_round_id",
            "parent_contract_id",
            "parent_evaluation_round_number",
            "board_basis_round",
            "hypothesis_state_basis",
            "continuation_token",
            "contract_lineage",
            "continuation_basis",
            "material_change_reason",
            "lineage_reason",
        )
        _require_fields(contract, lineage_fields, "Round2PlusInvestigationContract")
        continuation_basis = contract["continuation_basis"]
        if not isinstance(continuation_basis, dict):
            raise ValueError("Round2PlusInvestigationContract.continuation_basis must be an object.")
        _require_fields(
            continuation_basis,
            (
                "from_round",
                "from_recommended_next_action",
                "target_residual_component",
                "target_open_question_ids",
                "expected_gain_type",
                "material_changes_from_parent",
                "why_this_round_can_reduce_residual",
                "why_not_stop_now",
                "why_not_restart",
            ),
            "Round2PlusInvestigationContract.continuation_basis",
        )
        _require_enum(
            continuation_basis["from_recommended_next_action"],
            CONTINUATION_ACTIONS,
            "Round2PlusInvestigationContract.continuation_basis.from_recommended_next_action",
        )
        _require_non_empty_string(
            continuation_basis["target_residual_component"],
            "Round2PlusInvestigationContract.continuation_basis.target_residual_component",
        )
        _require_non_empty_string_list(
            continuation_basis["target_open_question_ids"],
            "Round2PlusInvestigationContract.continuation_basis.target_open_question_ids",
        )
        _require_non_empty_string(
            continuation_basis["expected_gain_type"],
            "Round2PlusInvestigationContract.continuation_basis.expected_gain_type",
        )
        _require_non_empty_string(
            continuation_basis["why_this_round_can_reduce_residual"],
            "Round2PlusInvestigationContract.continuation_basis.why_this_round_can_reduce_residual",
        )
        _require_non_empty_string(
            continuation_basis["why_not_stop_now"],
            "Round2PlusInvestigationContract.continuation_basis.why_not_stop_now",
        )
        _require_non_empty_string(
            continuation_basis["why_not_restart"],
            "Round2PlusInvestigationContract.continuation_basis.why_not_restart",
        )
        changes = continuation_basis["material_changes_from_parent"]
        if not isinstance(changes, dict):
            raise ValueError(
                "Round2PlusInvestigationContract.continuation_basis.material_changes_from_parent must be an object."
            )
        changed_axes: list[str] = []
        for axis in ("target_hypotheses", "operator_id", "queries"):
            if axis in changes:
                _normalize_change_note(
                    changes[axis],
                    f"Round2PlusInvestigationContract.continuation_basis.material_changes_from_parent.{axis}",
                )
                changed_axes.append(axis)
        if not changed_axes:
            raise ValueError(
                "Round2PlusInvestigationContract.continuation_basis.material_changes_from_parent must describe at least one changed axis."
            )
        material_change_reason = contract["material_change_reason"]
        if not isinstance(material_change_reason, dict):
            raise ValueError("Round2PlusInvestigationContract.material_change_reason must be an object.")
        _require_fields(
            material_change_reason,
            (
                "changed_axes",
                "why_material",
                "residual_reduction_claim",
                "why_not_repeating_parent",
            ),
            "Round2PlusInvestigationContract.material_change_reason",
        )
        reason_axes = material_change_reason["changed_axes"]
        if not isinstance(reason_axes, list) or not reason_axes:
            raise ValueError("Round2PlusInvestigationContract.material_change_reason.changed_axes must be a non-empty list.")
        allowed_axes = {"target_hypotheses", "operator_id", "queries"}
        for axis in reason_axes:
            _require_non_empty_string(axis, "Round2PlusInvestigationContract.material_change_reason.changed_axes[]")
            if axis not in allowed_axes:
                raise ValueError("Round2PlusInvestigationContract.material_change_reason.changed_axes contains an unsupported axis.")
        for field in ("why_material", "residual_reduction_claim", "why_not_repeating_parent"):
            _require_non_empty_string(
                material_change_reason[field],
                f"Round2PlusInvestigationContract.material_change_reason.{field}",
            )
        for query in contract["queries"]:
            if not _has_residual_binding(query, "Round2PlusInvestigationContract.query"):
                raise ValueError(
                    "Round2PlusInvestigationContract queries must bind to target open questions or a residual component."
                )
        for search in web_searches:
            if not _has_residual_binding(search, "Round2PlusInvestigationContract.web_search"):
                raise ValueError(
                    "Round2PlusInvestigationContract web_searches must bind to target open questions or a residual component."
                )


def validate_hypothesis_board_item(item: dict[str, Any]) -> None:
    required_fields = (
        "hypothesis_id",
        "family",
        "class",
        "layer",
        "statement",
        "relevance_score",
        "evidence_basis",
        "schema_feasibility",
        "status",
        "query_plan",
        "notes",
    )
    _require_fields(item, required_fields, "HypothesisBoardItem")
    _require_enum(item["class"], HYPOTHESIS_CLASSES, "HypothesisBoardItem.class")
    _require_enum(item["layer"], HYPOTHESIS_LAYERS, "HypothesisBoardItem.layer")
    _require_enum(
        item["schema_feasibility"],
        SCHEMA_FEASIBILITY_STATUSES,
        "HypothesisBoardItem.schema_feasibility",
    )
    _require_enum(item["status"], HYPOTHESIS_STATUSES, "HypothesisBoardItem.status")
    if "evidence_channel_plan" in item:
        channel_plan = item["evidence_channel_plan"]
        if not isinstance(channel_plan, dict):
            raise ValueError("HypothesisBoardItem.evidence_channel_plan must be an object when provided.")
        lanes = channel_plan.get("lanes")
        if not isinstance(lanes, list) or not lanes:
            raise ValueError("HypothesisBoardItem.evidence_channel_plan.lanes must be a non-empty list.")
        for lane in lanes:
            _require_enum(lane, EVIDENCE_LANES, "HypothesisBoardItem.evidence_channel_plan.lanes[]")
        if "rationale" in channel_plan:
            _require_non_empty_string(
                channel_plan["rationale"],
                "HypothesisBoardItem.evidence_channel_plan.rationale",
            )


def validate_plan_bundle(bundle: dict[str, Any]) -> None:
    _require_fields(bundle, ("hypothesis_board", "round_1_contract", "planning_notes", "max_rounds"), "PlanBundle")
    if not isinstance(bundle["max_rounds"], int) or bundle["max_rounds"] <= 0:
        raise ValueError("PlanBundle.max_rounds must be a positive integer.")
    if bundle["max_rounds"] > DEFAULT_MAX_ROUNDS:
        raise ValueError(f"PlanBundle.max_rounds must not exceed the hard ceiling of {DEFAULT_MAX_ROUNDS}.")
    if not isinstance(bundle["hypothesis_board"], list):
        raise ValueError("PlanBundle.hypothesis_board must be a list.")
    for item in bundle["hypothesis_board"]:
        if not isinstance(item, dict):
            raise ValueError("PlanBundle.hypothesis_board entries must be objects.")
        validate_hypothesis_board_item(item)
    round_1_contract = bundle["round_1_contract"]
    if not isinstance(round_1_contract, dict):
        raise ValueError("PlanBundle.round_1_contract must be an object.")
    validate_investigation_contract(round_1_contract)
    if round_1_contract.get("round_number") != 1:
        raise ValueError("PlanBundle.round_1_contract.round_number must be 1.")
    if round_1_contract.get("max_rounds") != bundle.get("max_rounds"):
        raise ValueError("PlanBundle.round_1_contract.max_rounds must match PlanBundle.max_rounds.")
    if any(not isinstance(hypothesis, dict) or hypothesis.get("layer") != "audit" for hypothesis in bundle["hypothesis_board"] if hypothesis.get("hypothesis_id") in round_1_contract.get("target_hypotheses", [])):
        raise ValueError("PlanBundle.round_1_contract.target_hypotheses must all come from the audit layer.")


def validate_stage_decision(decision: dict[str, Any]) -> None:
    _require_fields(
        decision,
        (
            "decision_ref",
            "stage",
            "phase",
            "goal",
            "completion_criteria",
            "transition_mode",
            "next_stage",
            "timestamp",
        ),
        "StageDecision",
    )
    _require_non_empty_string(decision["decision_ref"], "StageDecision.decision_ref")
    _require_non_empty_string(decision["stage"], "StageDecision.stage")
    _require_enum(decision["phase"], STAGE_DECISION_PHASES, "StageDecision.phase")
    _require_non_empty_string(decision["goal"], "StageDecision.goal")
    if not isinstance(decision["completion_criteria"], list):
        raise ValueError("StageDecision.completion_criteria must be a list.")
    _require_enum(decision["transition_mode"], TRANSITION_MODES, "StageDecision.transition_mode")
    _require_non_empty_string(decision["next_stage"], "StageDecision.next_stage")
    if not isinstance(decision["timestamp"], (int, float)):
        raise ValueError("StageDecision.timestamp must be numeric.")


def validate_action_rationale(rationale: dict[str, Any]) -> None:
    _require_fields(
        rationale,
        (
            "action_ref",
            "current_stage",
            "action_type",
            "purpose",
            "expected_output_type",
            "artifact_impact",
            "why_not_a_later_stage_claim",
            "timestamp",
        ),
        "ActionRationale",
    )
    _require_non_empty_string(rationale["action_ref"], "ActionRationale.action_ref")
    _require_non_empty_string(rationale["current_stage"], "ActionRationale.current_stage")
    _require_enum(rationale["action_type"], ACTION_RATIONALE_TYPES, "ActionRationale.action_type")
    _require_non_empty_string(rationale["purpose"], "ActionRationale.purpose")
    _require_non_empty_string(rationale["expected_output_type"], "ActionRationale.expected_output_type")
    if not isinstance(rationale["artifact_impact"], list):
        raise ValueError("ActionRationale.artifact_impact must be a list.")
    _require_non_empty_string(
        rationale["why_not_a_later_stage_claim"],
        "ActionRationale.why_not_a_later_stage_claim",
    )
    if not isinstance(rationale["timestamp"], (int, float)):
        raise ValueError("ActionRationale.timestamp must be numeric.")


def validate_tool_usage_envelope(envelope: dict[str, Any]) -> None:
    _require_fields(
        envelope,
        (
            "tool_ref",
            "tool_name",
            "stage",
            "purpose",
            "expected_artifact_impact",
            "produced_evidence_refs",
            "timestamp",
        ),
        "ToolUsageEnvelope",
    )
    _require_non_empty_string(envelope["tool_ref"], "ToolUsageEnvelope.tool_ref")
    _require_non_empty_string(envelope["tool_name"], "ToolUsageEnvelope.tool_name")
    _require_non_empty_string(envelope["stage"], "ToolUsageEnvelope.stage")
    _require_non_empty_string(envelope["purpose"], "ToolUsageEnvelope.purpose")
    if not isinstance(envelope["expected_artifact_impact"], list):
        raise ValueError("ToolUsageEnvelope.expected_artifact_impact must be a list.")
    if not isinstance(envelope["produced_evidence_refs"], list):
        raise ValueError("ToolUsageEnvelope.produced_evidence_refs must be a list.")
    if not isinstance(envelope["timestamp"], (int, float)):
        raise ValueError("ToolUsageEnvelope.timestamp must be numeric.")


def validate_compliance_event(event: dict[str, Any]) -> None:
    _require_fields(
        event,
        ("severity", "message", "ref", "timestamp"),
        "ComplianceEvent",
    )
    _require_enum(event["severity"], COMPLIANCE_SEVERITIES, "ComplianceEvent.severity")
    _require_non_empty_string(event["message"], "ComplianceEvent.message")
    _require_non_empty_string(event["ref"], "ComplianceEvent.ref")
    if not isinstance(event["timestamp"], (int, float)):
        raise ValueError("ComplianceEvent.timestamp must be numeric.")


def validate_compliance_report(report: dict[str, Any]) -> None:
    _require_fields(
        report,
        (
            "session_slug",
            "generation_id",
            "chosen_skill",
            "protocol_mode",
            "stage_timeline",
            "attributable_actions",
            "unattributed_actions",
            "evidence_lineage_coverage",
            "claims_without_lineage",
            "events",
            "final_verdict",
        ),
        "ComplianceReport",
    )
    _require_non_empty_string(report["session_slug"], "ComplianceReport.session_slug")
    _require_non_empty_string(report["generation_id"], "ComplianceReport.generation_id")
    _require_non_empty_string(report["chosen_skill"], "ComplianceReport.chosen_skill")
    _require_non_empty_string(report["protocol_mode"], "ComplianceReport.protocol_mode")
    if not isinstance(report["stage_timeline"], list):
        raise ValueError("ComplianceReport.stage_timeline must be a list.")
    if not isinstance(report["attributable_actions"], list):
        raise ValueError("ComplianceReport.attributable_actions must be a list.")
    if not isinstance(report["unattributed_actions"], list):
        raise ValueError("ComplianceReport.unattributed_actions must be a list.")
    if not isinstance(report["claims_without_lineage"], list):
        raise ValueError("ComplianceReport.claims_without_lineage must be a list.")
    if not isinstance(report["evidence_lineage_coverage"], dict):
        raise ValueError("ComplianceReport.evidence_lineage_coverage must be an object.")
    if not isinstance(report["events"], list):
        raise ValueError("ComplianceReport.events must be a list.")
    for event in report["events"]:
        if not isinstance(event, dict):
            raise ValueError("ComplianceReport.events entries must be objects.")
        validate_compliance_event(event)
    _require_enum(report["final_verdict"], COMPLIANCE_VERDICTS, "ComplianceReport.final_verdict")


def validate_descriptive_stats_bundle(bundle: dict[str, Any]) -> None:
    _require_fields(
        bundle,
        (
            "session_slug",
            "session_id",
            "visualization_coverage",
            "statistical_summary",
            "omitted_visuals",
            "omission_reasons",
            "generated_at",
        ),
        "DescriptiveStatsBundle",
    )
    _require_non_empty_string(bundle["session_slug"], "DescriptiveStatsBundle.session_slug")
    _require_non_empty_string(bundle["session_id"], "DescriptiveStatsBundle.session_id")
    _require_enum(
        bundle["visualization_coverage"],
        VISUALIZATION_COVERAGE_STATUSES,
        "DescriptiveStatsBundle.visualization_coverage",
    )
    if not isinstance(bundle["statistical_summary"], list):
        raise ValueError("DescriptiveStatsBundle.statistical_summary must be a list.")
    if not isinstance(bundle["omitted_visuals"], list):
        raise ValueError("DescriptiveStatsBundle.omitted_visuals must be a list.")
    if not isinstance(bundle["omission_reasons"], list):
        raise ValueError("DescriptiveStatsBundle.omission_reasons must be a list.")
    if not isinstance(bundle["generated_at"], (int, float)):
        raise ValueError("DescriptiveStatsBundle.generated_at must be numeric.")


def validate_report_evidence_index(index: dict[str, Any]) -> None:
    _require_fields(
        index,
        (
            "session_slug",
            "session_id",
            "report_evidence_refs",
            "generated_at",
        ),
        "ReportEvidenceIndex",
    )
    _require_non_empty_string(index["session_slug"], "ReportEvidenceIndex.session_slug")
    _require_non_empty_string(index["session_id"], "ReportEvidenceIndex.session_id")
    if not isinstance(index["report_evidence_refs"], list):
        raise ValueError("ReportEvidenceIndex.report_evidence_refs must be a list.")
    if "web_evidence_refs" in index and not isinstance(index["web_evidence_refs"], list):
        raise ValueError("ReportEvidenceIndex.web_evidence_refs must be a list when provided.")
    if not isinstance(index["generated_at"], (int, float)):
        raise ValueError("ReportEvidenceIndex.generated_at must be numeric.")
    for item in index["report_evidence_refs"]:
        if not isinstance(item, dict):
            raise ValueError("ReportEvidenceIndex.report_evidence_refs entries must be objects.")
        _require_fields(
            item,
            (
                "section",
                "round_id",
                "query_id",
                "reason",
            ),
            "ReportEvidenceIndex.entry",
        )
        _require_enum(item["section"], REPORT_EVIDENCE_SECTIONS, "ReportEvidenceIndex.entry.section")
        _require_non_empty_string(item["round_id"], "ReportEvidenceIndex.entry.round_id")
        _require_non_empty_string(item["query_id"], "ReportEvidenceIndex.entry.query_id")
        _require_non_empty_string(item["reason"], "ReportEvidenceIndex.entry.reason")
    for item in index.get("web_evidence_refs", []):
        if not isinstance(item, dict):
            raise ValueError("ReportEvidenceIndex.web_evidence_refs entries must be objects.")
        _require_fields(
            item,
            (
                "section",
                "round_id",
                "search_id",
                "reason",
            ),
            "ReportEvidenceIndex.web_entry",
        )
        _require_enum(item["section"], REPORT_EVIDENCE_SECTIONS, "ReportEvidenceIndex.web_entry.section")
        _require_non_empty_string(item["round_id"], "ReportEvidenceIndex.web_entry.round_id")
        _require_non_empty_string(item["search_id"], "ReportEvidenceIndex.web_entry.search_id")
        _require_non_empty_string(item["reason"], "ReportEvidenceIndex.web_entry.reason")


def validate_report_evidence_bundle(bundle: dict[str, Any]) -> None:
    _require_fields(
        bundle,
        (
            "session_slug",
            "session_id",
            "entries",
            "generated_at",
        ),
        "ReportEvidenceBundle",
    )
    _require_non_empty_string(bundle["session_slug"], "ReportEvidenceBundle.session_slug")
    _require_non_empty_string(bundle["session_id"], "ReportEvidenceBundle.session_id")
    if not isinstance(bundle["entries"], list):
        raise ValueError("ReportEvidenceBundle.entries must be a list.")
    if not isinstance(bundle["generated_at"], (int, float)):
        raise ValueError("ReportEvidenceBundle.generated_at must be numeric.")
    for entry in bundle["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("ReportEvidenceBundle.entries entries must be objects.")
        _require_fields(
            entry,
            (
                "evidence_ref",
                "section",
                "text",
                "query_refs",
            ),
            "ReportEvidenceBundle.entry",
        )
        _require_non_empty_string(entry["evidence_ref"], "ReportEvidenceBundle.entry.evidence_ref")
        _require_enum(entry["section"], REPORT_EVIDENCE_SECTIONS, "ReportEvidenceBundle.entry.section")
        _require_non_empty_string(entry["text"], "ReportEvidenceBundle.entry.text")
        if not isinstance(entry["query_refs"], list):
            raise ValueError("ReportEvidenceBundle.entry.query_refs must be a list.")
        if "web_refs" in entry:
            validate_web_refs(entry["web_refs"], "ReportEvidenceBundle.entry.web_refs")
        if "evaluation_refs" in entry and not isinstance(entry["evaluation_refs"], list):
            raise ValueError("ReportEvidenceBundle.entry.evaluation_refs must be a list when provided.")
        if "importance" in entry and not isinstance(entry["importance"], int):
            raise ValueError("ReportEvidenceBundle.entry.importance must be an integer when provided.")
        if "chartability_note" in entry and not isinstance(entry["chartability_note"], str):
            raise ValueError("ReportEvidenceBundle.entry.chartability_note must be a string when provided.")
        for query_ref in entry["query_refs"]:
            if not isinstance(query_ref, dict):
                raise ValueError("ReportEvidenceBundle.entry.query_refs entries must be objects.")
            _require_fields(
                query_ref,
                ("round_id", "query_id"),
                "ReportEvidenceBundle.entry.query_ref",
            )
            _require_non_empty_string(
                query_ref["round_id"],
                "ReportEvidenceBundle.entry.query_ref.round_id",
            )
            _require_non_empty_string(
                query_ref["query_id"],
                "ReportEvidenceBundle.entry.query_ref.query_id",
            )


def validate_chart_spec_bundle(bundle: dict[str, Any]) -> None:
    _require_fields(
        bundle,
        (
            "session_slug",
            "session_id",
            "specs",
            "generated_at",
        ),
        "ChartSpecBundle",
    )
    _require_non_empty_string(bundle["session_slug"], "ChartSpecBundle.session_slug")
    _require_non_empty_string(bundle["session_id"], "ChartSpecBundle.session_id")
    if not isinstance(bundle["specs"], list):
        raise ValueError("ChartSpecBundle.specs must be a list.")
    if not isinstance(bundle["generated_at"], (int, float)):
        raise ValueError("ChartSpecBundle.generated_at must be numeric.")
    for spec in bundle["specs"]:
        if not isinstance(spec, dict):
            raise ValueError("ChartSpecBundle.specs entries must be objects.")
        _require_fields(
            spec,
            (
                "spec_id",
                "title",
                "caption",
                "semantic_chart_type",
                "narrative_role",
                "report_section",
                "evidence_refs",
                "query_refs",
                "source_query_ref",
                "plot_data",
                "plot_spec",
                "why_this_chart",
            ),
            "ChartSpec",
        )
        for field in (
            "spec_id",
            "title",
            "caption",
            "semantic_chart_type",
            "narrative_role",
            "report_section",
            "why_this_chart",
        ):
            _require_non_empty_string(spec[field], f"ChartSpec.{field}")
        if not isinstance(spec["evidence_refs"], list) or not spec["evidence_refs"]:
            raise ValueError("ChartSpec.evidence_refs must be a non-empty list.")
        if not isinstance(spec["query_refs"], list) or not spec["query_refs"]:
            raise ValueError("ChartSpec.query_refs must be a non-empty list.")
        if not isinstance(spec["source_query_ref"], dict):
            raise ValueError("ChartSpec.source_query_ref must be an object.")
        _require_fields(spec["source_query_ref"], ("round_id", "query_id"), "ChartSpec.source_query_ref")
        if not isinstance(spec["plot_data"], dict):
            raise ValueError("ChartSpec.plot_data must be an object.")
        if not isinstance(spec["plot_data"].get("items"), list):
            raise ValueError("ChartSpec.plot_data.items must be a list.")
        for item in spec["plot_data"]["items"]:
            if not isinstance(item, dict):
                raise ValueError("ChartSpec.plot_data.items entries must be objects.")
            _require_fields(item, ("item_id", "payload"), "ChartSpec.plot_data.item")
            _require_non_empty_string(item["item_id"], "ChartSpec.plot_data.item.item_id")
            if not isinstance(item["payload"], dict):
                raise ValueError("ChartSpec.plot_data.item.payload must be an object.")
            if "source_row_index" in item and not isinstance(item["source_row_index"], int):
                raise ValueError("ChartSpec.plot_data.item.source_row_index must be an integer when provided.")
            if "source_row_indexes" in item:
                if not isinstance(item["source_row_indexes"], list):
                    raise ValueError("ChartSpec.plot_data.item.source_row_indexes must be a list when provided.")
                for index in item["source_row_indexes"]:
                    if not isinstance(index, int):
                        raise ValueError("ChartSpec.plot_data.item.source_row_indexes entries must be integers.")
        if not isinstance(spec["plot_spec"], dict):
            raise ValueError("ChartSpec.plot_spec must be an object.")
        chart_type = spec["plot_spec"].get("chart_type")
        if "chart_type" in spec["plot_spec"] and not isinstance(chart_type, str):
            raise ValueError("ChartSpec.plot_spec.chart_type must be a string when provided.")
        if "renderer_hint" in spec:
            _require_non_empty_string(spec["renderer_hint"], "ChartSpec.renderer_hint")


def validate_visualization_manifest(manifest: dict[str, Any]) -> None:
    _require_fields(
        manifest,
        (
            "session_slug",
            "session_id",
            "report_path",
            "charts",
            "generated_at",
        ),
        "VisualizationManifest",
    )
    _require_non_empty_string(manifest["session_slug"], "VisualizationManifest.session_slug")
    _require_non_empty_string(manifest["session_id"], "VisualizationManifest.session_id")
    _require_non_empty_string(manifest["report_path"], "VisualizationManifest.report_path")
    if not isinstance(manifest["charts"], list):
        raise ValueError("VisualizationManifest.charts must be a list.")
    if not isinstance(manifest["generated_at"], (int, float)):
        raise ValueError("VisualizationManifest.generated_at must be numeric.")
    for chart in manifest["charts"]:
        if not isinstance(chart, dict):
            raise ValueError("VisualizationManifest.charts entries must be objects.")
        _require_fields(
            chart,
            (
                "chart_id",
                "spec_id",
                "semantic_chart_type",
                "render_engine",
                "title",
                "caption",
                "file_path",
                "plot_data_path",
                "spec_hash",
                "plot_spec_hash",
                "source_result_hash",
                "query_refs",
                "evidence_refs",
                "report_section",
            ),
            "VisualizationManifest.chart",
        )
        _require_non_empty_string(chart["chart_id"], "VisualizationManifest.chart.chart_id")
        _require_non_empty_string(chart["spec_id"], "VisualizationManifest.chart.spec_id")
        _require_non_empty_string(chart["semantic_chart_type"], "VisualizationManifest.chart.semantic_chart_type")
        _require_non_empty_string(chart["render_engine"], "VisualizationManifest.chart.render_engine")
        _require_non_empty_string(chart["title"], "VisualizationManifest.chart.title")
        _require_non_empty_string(chart["caption"], "VisualizationManifest.chart.caption")
        _require_non_empty_string(chart["file_path"], "VisualizationManifest.chart.file_path")
        _require_non_empty_string(chart["plot_data_path"], "VisualizationManifest.chart.plot_data_path")
        _require_non_empty_string(chart["spec_hash"], "VisualizationManifest.chart.spec_hash")
        _require_non_empty_string(chart["plot_spec_hash"], "VisualizationManifest.chart.plot_spec_hash")
        _require_non_empty_string(chart["source_result_hash"], "VisualizationManifest.chart.source_result_hash")
        if not isinstance(chart["query_refs"], list):
            raise ValueError("VisualizationManifest.chart.query_refs must be a list.")
        if not isinstance(chart["evidence_refs"], list):
            raise ValueError("VisualizationManifest.chart.evidence_refs must be a list.")
        _require_non_empty_string(
            chart["report_section"],
            "VisualizationManifest.chart.report_section",
        )
