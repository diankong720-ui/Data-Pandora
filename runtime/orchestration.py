from __future__ import annotations

from typing import Any

from runtime.contracts import validate_investigation_contract
from runtime.evaluation import persist_round_evaluation
from runtime.final_answer import persist_final_answer
from runtime.interface import WarehouseClient
from runtime.tools import execute_query_request


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

        executed_queries.append(
            execute_query_request(
                client,
                dict(request),
                slug=slug,
                session_id=session_id,
                contract_id=str(contract["contract_id"]),
                round_number=int(contract["round_number"]),
                timeout=timeout,
                max_rows=max_rows,
                max_cache_age_seconds=max_cache_age_seconds,
            )
        )

    return executed_queries


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
) -> dict[str, Any]:
    """
    Execute one InvestigationContract, validate/persist the provided
    RoundEvaluationResult, and return the round bundle.
    """
    executed_queries = execute_investigation_contract(
        client,
        contract,
        slug=slug,
        session_id=session_id,
        timeout=timeout,
        max_rows=max_rows,
        max_cache_age_seconds=max_cache_age_seconds,
    )
    persist_round_evaluation(
        slug,
        evaluation,
        contract=contract,
        executed_queries=executed_queries,
        session_id=session_id,
    )
    return {
        "contract": contract,
        "executed_queries": executed_queries,
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
