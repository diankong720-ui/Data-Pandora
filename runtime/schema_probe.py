from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from runtime.interface import WarehouseClient
from runtime.tools import _normalize_sql_for_validation, _validate_sql


def _validate_identifier_path(name: str) -> None:
    """Allow only alphanumeric identifier paths such as schema.table."""
    if not re.match(r'^[\w.]+$', name):
        raise ValueError(f"Unsafe table name rejected: {name!r}")


def _safe_table_reference(client: WarehouseClient, name: str) -> str:
    """
    Validate a table path and then delegate dialect-specific quoting to the
    warehouse client.
    """
    _validate_identifier_path(name)
    return client.quote_identifier(name)


def _validate_table_listing_sql(sql: str) -> str | None:
    """Allow read-only table-listing introspection plus SELECT/WITH queries."""
    normalized = _normalize_sql_for_validation(sql)
    statements = [part.strip() for part in sql.split(";") if part.strip()]
    if len(statements) > 1:
        return "SQL must contain exactly one statement."
    if re.match(r"^SHOW\s+(?:FULL\s+)?TABLES\b", normalized):
        return None
    return _validate_sql(sql)


@dataclass
class TableProfile:
    name: str
    columns: list[str]
    sample_rows: list[dict[str, Any]]
    probe_error: str | None = None


@dataclass
class SchemaSnapshot:
    visible_tables: list[str]
    table_profiles: dict[str, TableProfile]
    probe_error: str | None = None


# ---------------------------------------------------------------------------
# Tool 1 — Schema Probe
# ---------------------------------------------------------------------------

def probe_schema(
    client: WarehouseClient,
    *,
    tables: list[str] | None = None,
    sample_limit: int = 3,
    list_tables_sql: str = "SHOW TABLES",
) -> dict[str, Any]:
    """
    LLM-callable schema probe tool.

    When `tables` is None, lists all visible tables first, then probes
    each one for column names and sample rows.

    When `tables` is a list, skips the table listing step and probes
    only the named tables.

    Returns a plain dict (JSON-serialisable) of raw warehouse facts.
    The LLM interprets the facts — this function does not recommend
    candidate tables, joins, fields, or metric bindings.

    Args:
        client:           An initialised WarehouseClient.
        tables:           Specific tables to probe; None = probe all visible.
        sample_limit:     Rows to fetch per table (default 3).
        list_tables_sql:  SQL to list visible tables. Dialect examples:
                            MySQL / MariaDB : "SHOW TABLES"  (default)
                            PostgreSQL      : "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                            BigQuery        : "SELECT table_name FROM <dataset>.INFORMATION_SCHEMA.TABLES"
                            Snowflake       : "SHOW TABLES"
                            SQLite          : "SELECT name FROM sqlite_master WHERE type='table'"
    """
    snapshot: dict[str, Any] = {
        "warehouse_identity": client.identity,
        "visible_tables": [],
        "table_profiles": {},
        "probe_error": None,
    }

    # Step 1 — list visible tables (if not provided explicitly)
    if tables is None:
        validation_error = _validate_table_listing_sql(list_tables_sql)
        if validation_error:
            snapshot["probe_error"] = validation_error
            raise ValueError(validation_error)
        list_result = client.execute(list_tables_sql, timeout=10.0, max_rows=500)
        if not list_result.ok:
            snapshot["probe_error"] = list_result.error or "table listing failed"
            return snapshot
        tables = [
            list(row.values())[0]
            for row in list_result.rows
            if row
        ]

    snapshot["visible_tables"] = tables

    # Step 2 — probe each table for headers + sample rows
    for table in tables:
        profile: dict[str, Any] = {
            "name": table,
            "columns": [],
            "sample_rows": [],
            "probe_error": None,
        }
        try:
            quoted = _safe_table_reference(client, table)
        except ValueError as exc:
            profile["probe_error"] = str(exc)
            snapshot["table_profiles"][table] = profile
            continue

        result = client.execute(
            f"SELECT * FROM {quoted} LIMIT {int(sample_limit)}",
            timeout=15.0,
            max_rows=sample_limit,
        )
        if result.ok:
            profile["columns"] = result.columns
            profile["sample_rows"] = result.rows
        else:
            profile["probe_error"] = result.error or "probe failed"

        snapshot["table_profiles"][table] = profile

    return snapshot


def probe_table(
    client: WarehouseClient,
    table: str,
    *,
    sample_limit: int = 5,
) -> dict[str, Any]:
    """
    Probe a single named table. Lighter alternative to full schema probe.
    Returns the same structure as a single entry in `table_profiles`.
    """
    try:
        quoted = _safe_table_reference(client, table)
    except ValueError as exc:
        return {
            "name": table,
            "columns": [],
            "sample_rows": [],
            "probe_error": str(exc),
        }

    result = client.execute(
        f"SELECT * FROM {quoted} LIMIT {int(sample_limit)}",
        timeout=15.0,
        max_rows=sample_limit,
    )
    return {
        "name": table,
        "columns": result.columns if result.ok else [],
        "sample_rows": result.rows if result.ok else [],
        "probe_error": None if result.ok else (result.error or "probe failed"),
    }
