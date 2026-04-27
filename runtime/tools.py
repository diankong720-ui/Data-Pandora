from __future__ import annotations

"""
Tool 3 — SQL Execution

This module wires together the WarehouseClient, cache, and admission control
into the single LLM-callable SQL execution tool.

The LLM calls execute_sql() with a fully formed SQL string.
This function handles:
  - admission control (checks warehouse load state)
  - cache lookup (returns cached result if available and allowed)
  - live execution (calls client.execute() on admission)
  - cache write (persists live results for future lookups)
  - load tracking (records outcome to update admission state)

The LLM receives a plain dict describing the outcome. It decides
what the result means for the current hypothesis — this function does not.
"""

import re
import time
import hashlib
from typing import Any

from runtime.contracts import validate_query_execution_request
from runtime.interface import WarehouseClient
from runtime.admission import check_admission, record_query_outcome, get_warehouse_snapshot
from runtime.cache import lookup_cache, write_cache, load_cached_rows
from runtime.persistence import append_execution_log
from runtime.sql_helpers import render_parameterized_sql


SAFE_TABLE_WHITELIST: list[str] | None = None  # None = allow all tables
RESULT_ROW_RETENTION_POLICIES: list[dict[str, Any]] = []


def set_table_whitelist(tables: list[str] | None) -> None:
    """
    Optionally restrict which tables the LLM may query.
    Call once at startup with your allowed table list.
    Pass None to disable the whitelist.
    """
    global SAFE_TABLE_WHITELIST
    SAFE_TABLE_WHITELIST = tables


def _sql_fingerprint(sql: str) -> str:
    return hashlib.sha256(_normalize_sql_for_validation(sql).encode("utf-8")).hexdigest()


def set_result_row_retention_policies(policies: list[dict[str, Any]] | None) -> None:
    """
    Configure runtime-owned rules for query row retention.

    Each policy is runtime-authored and matched by SQL fingerprint plus optional
    workspace / warehouse identity scoping. Supported retention modes:
      - deny
      - preview_only
      - full_rows
      - redacted_rows
    """
    global RESULT_ROW_RETENTION_POLICIES
    normalized_policies: list[dict[str, Any]] = []
    for policy in policies or []:
        if not isinstance(policy, dict):
            continue
        sql = policy.get("sql")
        sql_fingerprint = policy.get("sql_fingerprint")
        if isinstance(sql, str) and sql.strip():
            fingerprint = _sql_fingerprint(sql)
        elif isinstance(sql_fingerprint, str) and sql_fingerprint.strip():
            fingerprint = sql_fingerprint.strip()
        else:
            continue
        retention_mode = policy.get("retention_mode")
        if retention_mode not in {"deny", "preview_only", "full_rows", "redacted_rows"}:
            continue
        normalized_policy = {
            "sql_fingerprint": fingerprint,
            "retention_mode": retention_mode,
            "sensitivity_class": str(policy.get("sensitivity_class") or "unspecified"),
        }
        workspace = policy.get("workspace")
        warehouse_identity = policy.get("warehouse_identity")
        redaction_profile = policy.get("redaction_profile")
        max_rows = policy.get("max_rows")
        if isinstance(workspace, str) and workspace.strip():
            normalized_policy["workspace"] = workspace.strip()
        if isinstance(warehouse_identity, str) and warehouse_identity.strip():
            normalized_policy["warehouse_identity"] = warehouse_identity.strip()
        if isinstance(redaction_profile, dict):
            normalized_policy["redaction_profile"] = dict(redaction_profile)
        if isinstance(max_rows, int) and max_rows > 0:
            normalized_policy["max_rows"] = max_rows
        normalized_policies.append(normalized_policy)
    RESULT_ROW_RETENTION_POLICIES = normalized_policies


def _redact_rows(rows: list[dict[str, Any]], redaction_profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    profile = redaction_profile if isinstance(redaction_profile, dict) else {}
    drop_fields = profile.get("drop_fields")
    if not isinstance(drop_fields, list):
        return rows
    fields_to_drop = {field for field in drop_fields if isinstance(field, str)}
    redacted_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        redacted_rows.append({key: value for key, value in row.items() if key not in fields_to_drop})
    return redacted_rows


def _derive_columns_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    columns: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return columns


def _resolve_cache_write_payload(
    result_rows: list[dict[str, Any]],
    *,
    retention_mode: str,
    retention_policy: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    if retention_mode == "full_rows":
        return list(result_rows), None
    if retention_mode == "redacted_rows":
        return _redact_rows(
            list(result_rows),
            retention_policy.get("redaction_profile") if retention_policy else None,
        ), None
    return None, "cache write skipped because row retention policy did not allow local row persistence"


def _resolve_result_row_retention(
    request: dict[str, Any],
    *,
    warehouse_identity: str,
    temporary_full_rows_max: int | None = None,
) -> tuple[str, dict[str, Any] | None, str | None]:
    requested = bool(request.get("persist_result_rows"))
    if not requested:
        return "preview_only", None, None
    sql = request.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return "preview_only", None, "row retention denied because request sql is missing"
    fingerprint = _sql_fingerprint(sql)
    request_workspace = request.get("workspace")
    for policy in RESULT_ROW_RETENTION_POLICIES:
        if policy["sql_fingerprint"] != fingerprint:
            continue
        if "workspace" in policy and policy["workspace"] != request_workspace:
            continue
        if "warehouse_identity" in policy and policy["warehouse_identity"] != warehouse_identity:
            continue
        return str(policy["retention_mode"]), policy, None
    if isinstance(temporary_full_rows_max, int) and temporary_full_rows_max > 0:
        return (
            "full_rows",
            {
                "retention_mode": "full_rows",
                "sensitivity_class": "temporary_visualization",
                "max_rows": temporary_full_rows_max,
            },
            None,
        )
    return "preview_only", None, "row retention denied by runtime policy"


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r'/\*.*?\*/', ' ', sql, flags=re.DOTALL)
    return re.sub(r'--[^\n]*', ' ', sql)


def _normalize_sql_for_validation(sql: str) -> str:
    """Strip SQL comments and collapse whitespace before keyword scanning."""
    sql = _strip_sql_comments(sql)
    return re.sub(r'\s+', ' ', sql).strip().upper()


def _extract_referenced_tables(sql: str) -> set[str]:
    stripped_sql = _strip_sql_comments(sql)
    pattern = re.compile(
        r"""
        \b(?:FROM|JOIN)\s+
        (?:
            (?:[`"](?P<schema_quoted>[^`"]+)[`"]|(?P<schema_unquoted>\w+))
            \s*\.\s*
        )?
        (?:[`"](?P<table_quoted>[^`"]+)[`"]|(?P<table_unquoted>\w+))
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    )
    referenced: set[str] = set()
    for match in pattern.finditer(stripped_sql):
        table_name = match.group("table_quoted") or match.group("table_unquoted")
        if table_name:
            referenced.add(table_name.upper())
    return referenced


def _validate_sql(sql: str) -> str | None:
    """Return an error string if the SQL is unsafe, else None."""
    normalized = _normalize_sql_for_validation(sql)

    statements = [part.strip() for part in sql.split(";") if part.strip()]
    if len(statements) > 1:
        return "SQL must contain exactly one statement."

    if not (normalized.startswith("SELECT ") or normalized == "SELECT" or normalized.startswith("WITH ")):
        return "SQL must be a read-only SELECT or WITH query."

    forbidden = (
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "TRUNCATE",
        "ALTER",
        "CREATE",
        "GRANT",
        "REVOKE",
        "CALL",
        "COPY",
        "MERGE",
        "UPSERT",
        "EXECUTE",
        "EXPORT",
        "UNLOAD",
        "INTO",
    )
    for keyword in forbidden:
        # Match at start, after a semicolon, or after whitespace — with word boundary.
        # This catches semicolon chains, newline-separated statements, and comment-embedded keywords.
        if re.search(rf'(?:^|;|\s){re.escape(keyword)}\b', normalized):
            return f"SQL contains forbidden keyword: {keyword}"

    if SAFE_TABLE_WHITELIST is not None:
        referenced = _extract_referenced_tables(sql)
        blocked = referenced - {t.upper() for t in SAFE_TABLE_WHITELIST}
        if blocked:
            return f"Query references tables not in the whitelist: {', '.join(blocked)}"

    return None


def _resolve_cache_behavior(cache_policy: str) -> dict[str, bool]:
    if cache_policy == "bypass":
        return {
            "allow_cache_lookup": False,
            "allow_cache_fallback": False,
            "require_cache_hit": False,
        }
    if cache_policy == "allow_read":
        return {
            "allow_cache_lookup": True,
            "allow_cache_fallback": True,
            "require_cache_hit": False,
        }
    if cache_policy == "require_read":
        return {
            "allow_cache_lookup": True,
            "allow_cache_fallback": False,
            "require_cache_hit": True,
        }
    raise ValueError(f"Unsupported cache_policy: {cache_policy}")


def execute_sql(
    client: WarehouseClient,
    sql: str,
    *,
    output_name: str = "result",
    cost_class: str = "standard",
    allow_cache: bool = True,
    params: list[Any] | None = None,
    timeout: float = 30.0,
    max_rows: int = 10_000,
    max_cache_age_seconds: float | None = None,
) -> dict[str, Any]:
    """
    LLM-callable SQL execution tool.

    The LLM must provide the complete SQL. This function will not rewrite,
    infer joins, or fill in missing filters.

    Args:
        client:                 Initialised WarehouseClient.
        sql:                    The exact SQL to execute (may contain %s placeholders).
        output_name:            Identifier for this query result in the investigation record.
        cost_class:             "cheap" (scalar) or "standard" (GROUP BY / JOIN).
        allow_cache:            Whether a cache hit may substitute for live execution.
        params:                 Optional positional parameters for %s placeholders in sql.
        timeout:                Per-query timeout in seconds.
        max_rows:               Truncate result to this many rows.
        max_cache_age_seconds:  If set, reject cache entries older than this many seconds.

    Returns a plain dict with:
        status:       "success" | "cached" | "degraded_to_cache" |
                      "blocked" | "failed" | "timeout"
        output_name:  Echo of the output_name argument.
        rows_preview: First 10 rows (or [] on failure).
        row_count:    Total rows returned.
        cost_class:   Echo of cost_class.
        warehouse_snapshot: Current load state snapshot.
        error:        Error message string, or null.
    """
    rendered_sql = render_parameterized_sql(sql, params) if params else sql
    result = _execute_sql_detailed(
        client=client,
        sql=rendered_sql,
        output_name=output_name,
        cost_class=cost_class,
        cache_policy="allow_read" if allow_cache else "bypass",
        workspace="default",
        timeout=timeout,
        max_rows=max_rows,
        max_cache_age_seconds=max_cache_age_seconds,
    )
    return _legacy_result(result)

def execute_query_request(
    client: WarehouseClient,
    request: dict[str, Any],
    *,
    slug: str | None = None,
    session_id: str | None = None,
    contract_id: str | None = None,
    round_number: int | None = None,
    timeout: float = 30.0,
    max_rows: int = 10_000,
    max_cache_age_seconds: float | None = None,
    temporary_full_rows_max: int | None = None,
) -> dict[str, Any]:
    """
    Execute a full QueryExecutionRequest without rewriting or inferring SQL.
    """
    validate_query_execution_request(request)

    detailed = _execute_sql_detailed(
        client=client,
        sql=request["sql"],
        output_name=request["output_name"],
        cost_class=request["cost_class"],
        cache_policy=request["cache_policy"],
        workspace=str(request["workspace"]),
        timeout=timeout,
        max_rows=max_rows,
        max_cache_age_seconds=max_cache_age_seconds,
    )
    result = {
        "query_id": request["query_id"],
        "description": request["description"],
        "output_name": request["output_name"],
        "status": detailed["status"],
        "rows_preview": detailed["rows_preview"],
        "row_count": detailed["row_count"],
        "cost_class": detailed["cost_class"],
        "source": detailed["source"],
        "notes": list(detailed["notes"]),
    }
    retention_mode, retention_policy, retention_denial_reason = _resolve_result_row_retention(
        request,
        warehouse_identity=client.identity,
        temporary_full_rows_max=temporary_full_rows_max,
    )
    retained_rows: list[dict[str, Any]] | None = None
    if retention_mode == "full_rows":
        retained_rows = list(detailed["result_rows"])
    elif retention_mode == "redacted_rows":
        retained_rows = _redact_rows(list(detailed["result_rows"]), retention_policy.get("redaction_profile") if retention_policy else None)
    if isinstance(retained_rows, list):
        max_rows_for_retention = retention_policy.get("max_rows") if isinstance(retention_policy, dict) else None
        if isinstance(max_rows_for_retention, int) and max_rows_for_retention > 0:
            retained_rows = retained_rows[:max_rows_for_retention]
        result["result_rows"] = retained_rows
        result["result_rows_persisted"] = True
    else:
        result["result_rows_persisted"] = False
    result["retention_mode_applied"] = retention_mode
    result["sensitivity_class"] = (
        str(retention_policy.get("sensitivity_class"))
        if isinstance(retention_policy, dict)
        else "unspecified"
    )
    if isinstance(retention_policy, dict) and isinstance(retention_policy.get("redaction_profile"), dict):
        result["redaction_profile"] = dict(retention_policy["redaction_profile"])
    if retention_denial_reason:
        result["notes"].append(retention_denial_reason)
    cache_write_rows: list[dict[str, Any]] | None = None
    cache_skip_reason: str | None = None
    if bool(request.get("persist_result_rows")):
        cache_write_rows, cache_skip_reason = _resolve_cache_write_payload(
            detailed["result_rows"],
            retention_mode=retention_mode,
            retention_policy=retention_policy,
        )
    cache_write_key = None
    if cache_write_rows is not None:
        if isinstance(retention_policy, dict) and isinstance(retention_policy.get("max_rows"), int) and retention_policy["max_rows"] > 0:
            cache_write_rows = cache_write_rows[: retention_policy["max_rows"]]
        cache_write_key = write_cache(
            client.identity,
            request["sql"],
            cache_write_rows,
            _derive_columns_from_rows(cache_write_rows),
        )
        if cache_write_key is None:
            result["notes"].append("cache write skipped by secure default")
    elif cache_skip_reason:
        result["notes"].append(cache_skip_reason)
    if slug:
        append_execution_log(
            slug,
            {
                "query_id": request["query_id"],
                "description": request["description"],
                "contract_id": contract_id,
                "round_number": round_number,
                "workspace": request["workspace"],
                "output_name": request["output_name"],
                "status": detailed["status"],
                "source": detailed["source"],
                "warehouse_snapshot": detailed["warehouse_snapshot"],
                "error": detailed["error"],
                "executed_at": time.time(),
                "cache_policy": request["cache_policy"],
                "retention_requested": bool(request.get("persist_result_rows")),
                "retention_mode_applied": retention_mode,
                "sensitivity_class": result["sensitivity_class"],
                "redaction_profile": result.get("redaction_profile"),
                "retention_denial_reason": retention_denial_reason,
                "cache_write_key": cache_write_key,
                "cache_write_skipped_reason": cache_skip_reason if cache_write_key is None else None,
                "cache_hit": detailed["cache_hit"],
                "notes": result["notes"],
            },
            session_id=session_id,
            strict_session=bool(session_id),
        )
    return result


def _execute_sql_detailed(
    *,
    client: WarehouseClient,
    sql: str,
    output_name: str,
    cost_class: str,
    cache_policy: str,
    workspace: str,
    timeout: float,
    max_rows: int,
    max_cache_age_seconds: float | None,
) -> dict[str, Any]:
    cache_behavior = _resolve_cache_behavior(cache_policy)

    validation_error = _validate_sql(sql)
    if validation_error:
        notes = ["validation_blocked"]
        return _result(
            status="blocked",
            output_name=output_name,
            cost_class=cost_class,
            rows=[],
            error=validation_error,
            client=client,
            source="live",
            cache_hit=False,
            notes=notes,
            workspace=workspace,
        )

    hit = None
    if cache_behavior["allow_cache_lookup"]:
        hit = lookup_cache(
            client.identity,
            sql,
            max_age_seconds=max_cache_age_seconds,
        )
        if hit["status"] == "hit":
            rows = load_cached_rows(
                client.identity,
                sql,
                max_age_seconds=max_cache_age_seconds,
            ) or []
            status = "cached"
            return _result(
                status=status,
                output_name=output_name,
                cost_class=cost_class,
                rows=rows,
                error=None,
                client=client,
                source="cache",
                cache_hit=True,
                notes=[],
                workspace=workspace,
            )
        if cache_behavior["require_cache_hit"]:
            notes = ["cache required but no usable cache entry was found"]
            return _result(
                status="blocked",
                output_name=output_name,
                cost_class=cost_class,
                rows=[],
                error="Cache policy require_read blocked live execution because no usable cache entry exists.",
                client=client,
                source="cache",
                cache_hit=False,
                notes=notes,
                workspace=workspace,
            )

    decision = check_admission(
        cost_class,
        allow_cache_fallback=cache_behavior["allow_cache_fallback"],
    )

    if not decision.allowed:
        if decision.mode == "cache_only" and cache_behavior["allow_cache_lookup"]:
            cached_rows = load_cached_rows(
                client.identity,
                sql,
                max_age_seconds=max_cache_age_seconds,
            )
            if cached_rows is not None:
                return _result(
                    status="degraded_to_cache",
                    output_name=output_name,
                    cost_class=cost_class,
                    rows=cached_rows,
                    error=None,
                    client=client,
                    source="cache",
                    cache_hit=True,
                    notes=["live execution degraded to cache because of warehouse admission"],
                    workspace=workspace,
                )
        notes = []
        source = "cache" if decision.mode == "cache_only" else "live"
        return _result(
            status="blocked",
            output_name=output_name,
            cost_class=cost_class,
            rows=[],
            error=decision.reason,
            client=client,
            source=source,
            cache_hit=False,
            notes=notes,
            workspace=workspace,
        )

    query_result = client.execute(sql, timeout=timeout, max_rows=max_rows)
    record_query_outcome(timed_out=query_result.timed_out)

    if query_result.ok:
        return _result(
            status="success",
            output_name=output_name,
            cost_class=cost_class,
            rows=query_result.rows,
            error=None,
            client=client,
            source="live",
            cache_hit=False,
            notes=[],
            workspace=workspace,
        )

    status = "timeout" if query_result.timed_out else "failed"
    return _result(
        status=status,
        output_name=output_name,
        cost_class=cost_class,
        rows=[],
        error=query_result.error,
        client=client,
        source="live",
        cache_hit=False,
        notes=[],
        workspace=workspace,
    )


def _result(
    *,
    status: str,
    output_name: str,
    cost_class: str,
    rows: list[dict[str, Any]],
    error: str | None,
    client: WarehouseClient,
    source: str,
    cache_hit: bool,
    notes: list[str],
    workspace: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "output_name": output_name,
        "rows_preview": rows[:10],
        "result_rows": rows,
        "row_count": len(rows),
        "cost_class": cost_class,
        "source": source,
        "cache_hit": cache_hit,
        "notes": notes,
        "workspace": workspace,
        "warehouse_snapshot": get_warehouse_snapshot(),
        "error": error,
    }


def _legacy_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result["status"],
        "output_name": result["output_name"],
        "rows_preview": result["rows_preview"],
        "row_count": result["row_count"],
        "cost_class": result["cost_class"],
        "warehouse_snapshot": result["warehouse_snapshot"],
        "error": result["error"],
    }
