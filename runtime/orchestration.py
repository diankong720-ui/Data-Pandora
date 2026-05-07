from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from typing import Callable

from runtime.contracts import (
    validate_investigation_contract,
    validate_web_recall_assessment,
)
from runtime.evaluation import persist_round_evaluation
from runtime.final_answer import persist_final_answer
from runtime.interface import WarehouseClient
from runtime.tools import execute_query_request
from runtime.web_search import WebSearchClient, execute_web_search_request


def execute_investigation_contract(
    client: WarehouseClient,
    contract: dict[str, Any],
    *,
    slug: str | None = None,
    session_id: str | None = None,
    timeout: float = 30.0,
    max_rows: int = 10_000,
    max_cache_age_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """
    Execute every QueryExecutionRequest in an InvestigationContract in order.

    This is the runtime-facing handoff for upper-layer orchestrators: the LLM
    authors the contract, and runtime executes that explicit contract without
    filling in any missing SQL semantics.
    """
    validate_investigation_contract(contract)
    queries = contract["queries"]

    executed_queries: list[dict[str, Any]] = []
    seen_query_ids: set[str] = set()
    seen_output_names: set[str] = set()

    for request in queries:
        if not isinstance(request, dict):
            raise ValueError("Each InvestigationContract query must be an object.")

        query_id = request.get("query_id")
        output_name = request.get("output_name")
        if not isinstance(query_id, str) or not query_id:
            raise ValueError("Each InvestigationContract query must include a non-empty query_id.")
        if query_id in seen_query_ids:
            raise ValueError(f"Duplicate query_id in InvestigationContract: {query_id}")
        seen_query_ids.add(query_id)

        if not isinstance(output_name, str) or not output_name:
            raise ValueError("Each InvestigationContract query must include a non-empty output_name.")
        if output_name in seen_output_names:
            raise ValueError(f"Duplicate output_name in InvestigationContract: {output_name}")
        seen_output_names.add(output_name)

        runtime_request = dict(request)
        runtime_request["persist_result_rows"] = True
        executed_queries.append(
            execute_query_request(
                client,
                runtime_request,
                slug=slug,
                session_id=session_id,
                contract_id=str(contract["contract_id"]),
                round_number=int(contract["round_number"]),
                timeout=timeout,
                max_rows=max_rows,
                max_cache_age_seconds=max_cache_age_seconds,
                temporary_full_rows_max=max_rows,
            )
        )

    return executed_queries


def execute_web_searches_for_contract(
    web_client: WebSearchClient | None,
    contract: dict[str, Any],
    *,
    produce_web_recall_assessment: Callable[..., dict[str, Any]] | None = None,
    timeout: float = 30.0,
    max_results: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """
    Execute WebSearchRequest objects from an InvestigationContract.

    Initial searches are contract-authored. Same-round refinements are legal
    only when a WebRecallAssessment returns explicit refinement_requests and
    budget remains.
    """
    validate_investigation_contract(contract)
    web_searches = list(contract.get("web_searches") or [])
    web_refinement_budget = int(contract.get("web_refinement_budget") or 0)
    executed: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    queued = list(web_searches)
    seen_search_ids = {
        search["search_id"]
        for search in web_searches
        if isinstance(search, dict) and isinstance(search.get("search_id"), str)
    }
    executed_search_ids: set[str] = set()
    refinement_count = sum(
        1
        for search in web_searches
        if isinstance(search, dict) and isinstance(search.get("parent_search_id"), str)
    )

    while queued:
        request = queued.pop(0)
        search_id = request.get("search_id")
        if not isinstance(search_id, str) or search_id in executed_search_ids:
            continue
        result = execute_web_search_request(
            web_client,
            request,
            timeout=timeout,
            max_results=max_results,
        )
        executed.append(result)
        executed_search_ids.add(search_id)
        if produce_web_recall_assessment is None:
            continue
        assessment = produce_web_recall_assessment(
            contract=contract,
            request=request,
            result=result,
            executed_web_searches=list(executed),
            prior_assessments=list(assessments),
            remaining_refinement_budget=max(0, web_refinement_budget - refinement_count),
        )
        validate_web_recall_assessment(assessment)
        if assessment.get("search_id") != search_id:
            raise ValueError("WebRecallAssessment.search_id must match the executed WebSearchRequest.")
        assessments.append(assessment)
        for refinement in assessment.get("refinement_requests", []):
            if refinement_count >= web_refinement_budget:
                break
            refinement_id = refinement.get("search_id")
            if not isinstance(refinement_id, str) or refinement_id in seen_search_ids:
                continue
            validate_investigation_contract(
                {
                    **contract,
                    "web_searches": web_searches + [refinement],
                    "web_search_budget": max(
                        int(contract.get("web_search_budget") or 0),
                        len(web_searches) + 1,
                    ),
                }
            )
            seen_search_ids.add(refinement_id)
            web_searches.append(refinement)
            queued.append(refinement)
            refinement_count += 1
    return {
        "executed_web_searches": executed,
        "web_recall_assessments": assessments,
    }


def execute_evidence_contract(
    client: WarehouseClient,
    contract: dict[str, Any],
    *,
    web_client: WebSearchClient | None = None,
    produce_web_recall_assessment: Callable[..., dict[str, Any]] | None = None,
    slug: str | None = None,
    session_id: str | None = None,
    timeout: float = 30.0,
    max_rows: int = 10_000,
    max_cache_age_seconds: float | None = None,
    web_timeout: float = 30.0,
    web_max_results: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Run SQL and web evidence lanes for one frozen contract."""
    validate_investigation_contract(contract)
    has_queries = bool(contract.get("queries"))
    has_web = bool(contract.get("web_searches"))
    if has_queries and has_web:
        with ThreadPoolExecutor(max_workers=2) as executor:
            query_future = executor.submit(
                execute_investigation_contract,
                client,
                contract,
                slug=slug,
                session_id=session_id,
                timeout=timeout,
                max_rows=max_rows,
                max_cache_age_seconds=max_cache_age_seconds,
            )
            web_future = executor.submit(
                execute_web_searches_for_contract,
                web_client,
                contract,
                produce_web_recall_assessment=produce_web_recall_assessment,
                timeout=web_timeout,
                max_results=web_max_results,
            )
            executed_queries = query_future.result()
            web_bundle = web_future.result()
    else:
        executed_queries = (
            execute_investigation_contract(
                client,
                contract,
                slug=slug,
                session_id=session_id,
                timeout=timeout,
                max_rows=max_rows,
                max_cache_age_seconds=max_cache_age_seconds,
            )
            if has_queries
            else []
        )
        web_bundle = (
            execute_web_searches_for_contract(
                web_client,
                contract,
                produce_web_recall_assessment=produce_web_recall_assessment,
                timeout=web_timeout,
                max_results=web_max_results,
            )
            if has_web
            else {"executed_web_searches": [], "web_recall_assessments": []}
        )
    return {
        "executed_queries": executed_queries,
        "executed_web_searches": web_bundle["executed_web_searches"],
        "web_recall_assessments": web_bundle["web_recall_assessments"],
    }


def execute_round_and_persist(
    client: WarehouseClient,
    contract: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    slug: str,
    session_id: str | None = None,
    timeout: float = 30.0,
    max_rows: int = 10_000,
    max_cache_age_seconds: float | None = None,
    web_client: WebSearchClient | None = None,
    produce_web_recall_assessment: Callable[..., dict[str, Any]] | None = None,
    web_timeout: float = 30.0,
    web_max_results: int | None = None,
) -> dict[str, Any]:
    """
    Execute one InvestigationContract, validate/persist the provided
    RoundEvaluationResult, and return the round bundle.
    """
    evidence_bundle = execute_evidence_contract(
        client,
        contract,
        slug=slug,
        session_id=session_id,
        timeout=timeout,
        max_rows=max_rows,
        max_cache_age_seconds=max_cache_age_seconds,
        web_client=web_client,
        produce_web_recall_assessment=produce_web_recall_assessment,
        web_timeout=web_timeout,
        web_max_results=web_max_results,
    )
    persist_round_evaluation(
        slug,
        evaluation,
        contract=contract,
        executed_queries=evidence_bundle["executed_queries"],
        executed_web_searches=evidence_bundle["executed_web_searches"],
        web_recall_assessments=evidence_bundle["web_recall_assessments"],
        session_id=session_id,
    )
    return {
        "contract": contract,
        **evidence_bundle,
        "evaluation": evaluation,
    }


def finalize_session(
    slug: str,
    final_answer: dict[str, Any],
    *,
    session_id: str | None = None,
) -> str:
    """
    Persist the final answer against the latest round evaluation.

    Runtime does not generate conclusions; it only validates and stores the
    explicit FinalAnswer object authored upstream.
    """
    return persist_final_answer(slug, final_answer, session_id=session_id)
