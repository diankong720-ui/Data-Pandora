from __future__ import annotations

"""
Example: HTTP-based warehouse client.

Many internal data warehouses expose SQL via a JSON-over-HTTP endpoint.
Adapt this template to your warehouse's authentication scheme.

Required environment variables (put in .env):
    WAREHOUSE_BASE_URL   e.g. https://warehouse.example.com
    WAREHOUSE_PATH       e.g. /api/query
    WAREHOUSE_IDENTITY   a stable label for this warehouse, e.g. "prod-dw"

Optional:
    WAREHOUSE_API_KEY    bearer token or API key (passed as Authorization header)
    WAREHOUSE_TIMEOUT    per-query timeout in seconds (default 30)
    WAREHOUSE_MAX_ROWS   row limit (default 10000)
"""

import json
import os
import re
import time
from typing import Any

try:
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from runtime.interface import QueryResult, WarehouseClient


def _scrub_credentials(message: str) -> str:
    """Remove user:password from connection string patterns in error messages."""
    return re.sub(r'://[^:@\s]+:[^@\s]+@', '://<credentials>@', message)


class HttpSqlClient(WarehouseClient):
    """
    Generic HTTP SQL client. Sends a POST request with the SQL string
    and parses a JSON response.

    Override `_build_headers()` and `_parse_response()` for your specific
    warehouse API contract.
    """

    def __init__(self) -> None:
        self.base_url = os.environ["WAREHOUSE_BASE_URL"].rstrip("/")
        self.path = os.environ["WAREHOUSE_PATH"]
        self._identity = os.environ["WAREHOUSE_IDENTITY"]
        self.api_key = os.getenv("WAREHOUSE_API_KEY")
        self._timeout = float(os.getenv("WAREHOUSE_TIMEOUT", "30"))
        self._max_rows = int(os.getenv("WAREHOUSE_MAX_ROWS", "10000"))

    @property
    def identity(self) -> str:
        return self._identity

    def quote_identifier(self, name: str) -> str:
        # Default to ANSI-style quoting for generic HTTP SQL gateways.
        return ".".join(f'"{part}"' for part in name.split("."))

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _build_body(self, sql: str) -> dict[str, Any]:
        """
        Override this to match your warehouse's request schema.

        Default: {"sql": "<statement>"}
        """
        return {"sql": sql}

    def _parse_response(self, payload: dict[str, Any]) -> tuple[list[dict], list[str]]:
        """
        Override this to match your warehouse's response schema.

        Default expects: {"rows": [...], "columns": [...]}
        or             : {"data": [...]}  (columns inferred from first row)
        """
        if "rows" in payload and "columns" in payload:
            return payload["rows"], payload["columns"]
        data = payload.get("data") or payload.get("rows") or []
        cols = list(data[0].keys()) if data else []
        return data, cols

    def execute(
        self,
        sql: str,
        *,
        timeout: float = 30.0,
        max_rows: int = 10_000,
    ) -> QueryResult:
        if _requests is None:
            return QueryResult.from_error("requests package is not installed.")

        body = json.dumps(self._build_body(sql), ensure_ascii=False)
        try:
            response = _requests.post(
                self.base_url + self.path,
                data=body.encode("utf-8"),
                headers=self._build_headers(),
                timeout=(10.0, timeout),
            )
            response.raise_for_status()
            payload = response.json()
        except _requests.exceptions.Timeout:
            return QueryResult.from_error("Query timed out.", timed_out=True)
        except Exception as exc:
            return QueryResult.from_error(str(exc))

        try:
            rows, columns = self._parse_response(payload)
        except Exception as exc:
            return QueryResult.from_error(f"Response parse error: {exc}")

        if max_rows > 0 and len(rows) > max_rows:
            rows = rows[:max_rows]
        return QueryResult(rows=rows, columns=columns)


# ---------------------------------------------------------------------------
# Alternative: SQLAlchemy-based client (works with most SQL databases)
# ---------------------------------------------------------------------------

class SqlAlchemyClient(WarehouseClient):
    """
    Generic SQLAlchemy client. Works with PostgreSQL, MySQL, SQLite, BigQuery, etc.

    Install extras:
        pip install sqlalchemy psycopg2-binary   # PostgreSQL
        pip install sqlalchemy pymysql           # MySQL
        pip install sqlalchemy-bigquery          # BigQuery

    Required environment variables:
        WAREHOUSE_DSN       SQLAlchemy connection string
                            e.g. postgresql://user:pass@host:5432/dbname
        WAREHOUSE_IDENTITY  a stable label for this warehouse
    """

    def __init__(self) -> None:
        try:
            from sqlalchemy import create_engine, text
        except ImportError:
            raise RuntimeError("sqlalchemy is required for SqlAlchemyClient.")

        dsn = os.environ["WAREHOUSE_DSN"]
        self._identity = os.environ["WAREHOUSE_IDENTITY"]
        self._text = text
        self._engine = create_engine(dsn, pool_pre_ping=True)

    @property
    def identity(self) -> str:
        return self._identity

    def quote_identifier(self, name: str) -> str:
        preparer = self._engine.dialect.identifier_preparer
        return ".".join(preparer.quote(part) for part in name.split("."))

    def execute(
        self,
        sql: str,
        *,
        timeout: float = 30.0,
        max_rows: int = 10_000,
    ) -> QueryResult:
        # execution_options(timeout=...) is passed to the dialect as a hint.
        # Actual enforcement is driver-dependent:
        #   - psycopg2 (PostgreSQL): respected via statement_timeout
        #   - pymysql (MySQL): not natively enforced; consider SET SESSION wait_timeout
        #   - sqlalchemy-bigquery: respected via job timeout
        #   - sqlite: not enforced
        # Test timeout behavior for your specific driver before relying on it.
        try:
            with self._engine.connect() as conn:
                stmt = self._text(sql).execution_options(timeout=int(timeout))
                result = conn.execute(stmt)
                columns = list(result.keys())
                rows = [dict(zip(columns, row)) for row in result.fetchmany(max_rows)]
            return QueryResult(rows=rows, columns=columns)
        except Exception as exc:
            timed_out = "timeout" in str(exc).lower() or "timed out" in str(exc).lower()
            return QueryResult.from_error(_scrub_credentials(str(exc)), timed_out=timed_out)
