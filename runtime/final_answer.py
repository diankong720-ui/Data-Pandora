from __future__ import annotations

from typing import Any

from runtime.contracts import normalize_open_questions
from runtime.evaluation import CONCLUSION_STATES, INCOMPLETENESS_CATEGORIES
from runtime.persistence import get_active_generation_id, list_round_bundles, load_session_evidence, persist_artifact


FINAL_ANSWER_REQUIRED_FIELDS = (
    "session_slug",
    "conclusion_state",
    "headline_conclusion",
    "supported_claims",
    "contradictions",
    "residual_summary",
    "correction_mode",
    "incompleteness_category",
    "recommended_follow_up",
)


def get_latest_round_evaluation(slug: str, *, session_id: str | None = None) -> dict[str, Any] | None:
    """Return the latest persisted RoundEvaluationResult based on round_number."""
    latest: dict[str, Any] | None = None
    latest_round_number = -1
    active_generation_id = get_active_generation_id(
        slug,
        session_id=session_id,
        strict_session=bool(session_id),
    )
    for bundle in list_round_bundles(
        slug,
        generation_id=active_generation_id,
        session_id=session_id,
        strict_session=bool(session_id),
    ):
        evaluation = bundle.get("evaluation")
        if not isinstance(evaluation, dict):
            continue
        round_number = evaluation.get("round_number")
        if isinstance(round_number, int) and round_number >= latest_round_number:
            latest = evaluation
            latest_round_number = round_number
    return latest


def _validate_supported_claim_lineage(
    slug: str,
    supported_claims: list[Any],
    *,
    session_id: str | None = None,
) -> None:
    known_query_refs: set[tuple[str, str]] = set()
    known_web_refs: set[tuple[str, str]] = set()
    known_evaluation_refs: set[str] = set()
    active_generation_id = get_active_generation_id(
        slug,
        session_id=session_id,
        strict_session=bool(session_id),
    )
    for bundle in list_round_bundles(
        slug,
        generation_id=active_generation_id,
        session_id=session_id,
        strict_session=bool(session_id),
    ):
        evaluation = bundle.get("evaluation")
        round_id = evaluation.get("round_id") if isinstance(evaluation, dict) else None
        if isinstance(round_id, str):
            known_evaluation_refs.add(f"{round_id}:evaluation")
        for query in bundle.get("executed_queries", []):
            query_id = query.get("query_id")
            if isinstance(round_id, str) and isinstance(query_id, str):
                known_query_refs.add((round_id, query_id))
        for search in bundle.get("executed_web_searches", []):
            search_id = search.get("search_id")
            if isinstance(round_id, str) and isinstance(search_id, str):
                known_web_refs.add((round_id, search_id))

    for claim in supported_claims:
        if not isinstance(claim, dict):
            raise ValueError("FinalAnswer.supported_claims entries must be objects with evidence lineage.")
        if not isinstance(claim.get("claim"), str) or not claim["claim"]:
            raise ValueError("Each FinalAnswer.supported_claim must include a non-empty claim field.")
        query_refs = claim.get("query_refs", [])
        web_refs = claim.get("web_refs", [])
        evaluation_refs = claim.get("evaluation_refs", [])
        if not isinstance(query_refs, list) or not isinstance(web_refs, list) or not isinstance(evaluation_refs, list):
            raise ValueError("FinalAnswer supported-claim lineage fields must be arrays.")
        if not query_refs and not web_refs and not evaluation_refs:
            raise ValueError("Each FinalAnswer.supported_claim must include query_refs, web_refs, or evaluation_refs.")
        channels = claim.get("evidence_channels")
        if not isinstance(channels, list) or not channels:
            raise ValueError("Each FinalAnswer.supported_claim must include evidence_channels.")
        legal_channels = {"warehouse_sql", "web_search", "mixed"}
        for channel in channels:
            if channel not in legal_channels:
                raise ValueError("FinalAnswer.evidence_channels contains an unsupported channel.")
        for query_ref in query_refs:
            if not isinstance(query_ref, dict):
                raise ValueError("FinalAnswer.query_refs entries must be objects.")
            round_id = query_ref.get("round_id")
            query_id = query_ref.get("query_id")
            if not isinstance(round_id, str) or not isinstance(query_id, str):
                raise ValueError("FinalAnswer.query_refs entries must include round_id and query_id.")
            if (round_id, query_id) not in known_query_refs:
                raise ValueError(
                    f"FinalAnswer supported claim references unknown query lineage: {round_id}:{query_id}."
                )
        for web_ref in web_refs:
            if not isinstance(web_ref, dict):
                raise ValueError("FinalAnswer.web_refs entries must be objects.")
            round_id = web_ref.get("round_id")
            search_id = web_ref.get("search_id")
            if not isinstance(round_id, str) or not isinstance(search_id, str):
                raise ValueError("FinalAnswer.web_refs entries must include round_id and search_id.")
            if (round_id, search_id) not in known_web_refs:
                raise ValueError(
                    f"FinalAnswer supported claim references unknown web lineage: {round_id}:{search_id}."
                )
        channel_set = set(channels)
        if query_refs and web_refs and not ({"warehouse_sql", "web_search"} <= channel_set or "mixed" in channel_set):
            raise ValueError("FinalAnswer mixed SQL/web claims must mark evidence_channels as mixed or include both lanes.")
        if query_refs and not web_refs and "warehouse_sql" not in channel_set:
            raise ValueError("FinalAnswer SQL-only claims must include warehouse_sql in evidence_channels.")
        if web_refs and not query_refs and "web_search" not in channel_set:
            raise ValueError("FinalAnswer web-only claims must include web_search in evidence_channels.")
        for evaluation_ref in evaluation_refs:
            if not isinstance(evaluation_ref, str) or not evaluation_ref:
                raise ValueError("FinalAnswer.evaluation_refs entries must be non-empty strings.")
            if evaluation_ref not in known_evaluation_refs:
                raise ValueError(
                    f"FinalAnswer supported claim references unknown evaluation lineage: {evaluation_ref}."
                )


def _validate_contradictions(contradictions: Any) -> None:
    if not isinstance(contradictions, list):
        raise ValueError("FinalAnswer.contradictions must be an array.")
    for contradiction in contradictions:
        if isinstance(contradiction, str):
            if not contradiction:
                raise ValueError("FinalAnswer.contradictions string entries must be non-empty.")
            continue
        if not isinstance(contradiction, dict):
            raise ValueError("FinalAnswer.contradictions entries must be strings or objects.")
        text = contradiction.get("text") or contradiction.get("claim") or contradiction.get("summary")
        if not isinstance(text, str) or not text:
            raise ValueError(
                "FinalAnswer.contradictions object entries must include non-empty text, claim, or summary."
            )
        if "web_refs" in contradiction:
            web_refs = contradiction["web_refs"]
            if not isinstance(web_refs, list):
                raise ValueError("FinalAnswer.contradictions.web_refs must be a list when provided.")
            for web_ref in web_refs:
                if not isinstance(web_ref, dict):
                    raise ValueError("FinalAnswer.contradictions.web_refs entries must be objects.")
                if not isinstance(web_ref.get("round_id"), str) or not isinstance(web_ref.get("search_id"), str):
                    raise ValueError("FinalAnswer.contradictions.web_refs entries must include round_id and search_id.")


def _require_non_empty_string(value: Any, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")


def _require_boolean(value: Any, *, label: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean.")


def _require_numeric(value: Any, *, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric.")


def _require_string_list(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list.")
    for index, item in enumerate(value, start=1):
        _require_non_empty_string(item, label=f"{label}[{index}]")


def validate_final_answer(
    final_answer: dict[str, Any],
    *,
    slug: str | None = None,
    latest_evaluation: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> None:
    missing = [field for field in FINAL_ANSWER_REQUIRED_FIELDS if field not in final_answer]
    if missing:
        raise ValueError(f"FinalAnswer missing required fields: {', '.join(missing)}")

    if slug is not None and final_answer["session_slug"] != slug:
        raise ValueError("FinalAnswer.session_slug must match the session slug.")

    _require_non_empty_string(final_answer["headline_conclusion"], label="FinalAnswer.headline_conclusion")
    _require_boolean(final_answer["correction_mode"], label="FinalAnswer.correction_mode")
    _require_string_list(final_answer["recommended_follow_up"], label="FinalAnswer.recommended_follow_up")

    if final_answer["conclusion_state"] not in CONCLUSION_STATES:
        raise ValueError("FinalAnswer.conclusion_state is invalid.")
    if final_answer["incompleteness_category"] not in INCOMPLETENESS_CATEGORIES:
        raise ValueError("FinalAnswer.incompleteness_category is invalid.")

    residual_summary = final_answer["residual_summary"]
    if not isinstance(residual_summary, dict):
        raise ValueError("FinalAnswer.residual_summary must be an object.")
    for field in ("residual_score", "residual_band", "current_unexplained_ratio", "open_questions"):
        if field not in residual_summary:
            raise ValueError(f"FinalAnswer.residual_summary missing field: {field}")
    _require_numeric(residual_summary["residual_score"], label="FinalAnswer.residual_summary.residual_score")
    _require_non_empty_string(residual_summary["residual_band"], label="FinalAnswer.residual_summary.residual_band")
    _require_numeric(
        residual_summary["current_unexplained_ratio"],
        label="FinalAnswer.residual_summary.current_unexplained_ratio",
    )
    normalize_open_questions(
        residual_summary["open_questions"],
        label="FinalAnswer.residual_summary.open_questions",
    )

    if latest_evaluation is not None:
        if final_answer["conclusion_state"] != latest_evaluation.get("conclusion_state"):
            raise ValueError(
                "FinalAnswer.conclusion_state must match the latest RoundEvaluationResult.conclusion_state."
            )
        if latest_evaluation.get("recommended_next_action") == "restart" or latest_evaluation.get("conclusion_state") == "restart_required":
            raise ValueError(
                "FinalAnswer is illegal when the latest RoundEvaluationResult requires restart."
            )
    supported_claims = final_answer["supported_claims"]
    if not isinstance(supported_claims, list):
        raise ValueError("FinalAnswer.supported_claims must be an array.")
    if slug is not None:
        _validate_supported_claim_lineage(slug, supported_claims, session_id=session_id)
    _validate_contradictions(final_answer["contradictions"])


def persist_final_answer(
    slug: str,
    final_answer: dict[str, Any],
    *,
    session_id: str | None = None,
) -> str:
    """Validate FinalAnswer against the latest round evaluation and persist it."""
    validate_final_answer(
        final_answer,
        slug=slug,
        latest_evaluation=get_latest_round_evaluation(slug, session_id=session_id),
        session_id=session_id,
    )
    return persist_artifact(
        slug,
        "final_answer.json",
        final_answer,
        session_id=session_id,
        strict_session=bool(session_id),
    )


def build_final_answer_context(slug: str, *, session_id: str | None = None) -> dict[str, Any]:
    """Return the artifact-backed context needed by a final-answer producer."""
    session_evidence = load_session_evidence(
        slug,
        session_id=session_id,
        strict_session=bool(session_id),
    )
    session_evidence["latest_round_evaluation"] = get_latest_round_evaluation(slug, session_id=session_id)
    return session_evidence
