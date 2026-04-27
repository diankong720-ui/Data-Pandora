from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryResult:
    """Return type for every warehouse query."""
    rows: list[dict[str, Any]]
    columns: list[str]
    error: str | None = None
    timed_out: bool = False
    row_count: int = field(init=False)

    def __post_init__(self) -> None:
        self.row_count = len(self.rows)

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out

    @classmethod
    def from_error(cls, message: str, timed_out: bool = False) -> "QueryResult":
        return cls(rows=[], columns=[], error=message, timed_out=timed_out)


class WarehouseClient(ABC):
    """
    Abstract warehouse connection. Implement this class to connect
    the deep-research skill to your data warehouse.

    Only two members are required:
      - execute(): run a SQL string and return a QueryResult
      - identity:  a stable string identifying this warehouse+schema
                   (used as the cache namespace)

    See runtime/example_clients/ for reference implementations.
    """

    @abstractmethod
    def execute(
        self,
        sql: str,
        *,
        timeout: float = 30.0,
        max_rows: int = 10_000,
    ) -> QueryResult:
        """
        Execute a single SQL statement.

        Args:
            sql:      The complete SQL string to execute. Never rewrite or modify it.
            timeout:  Seconds before treating the query as timed out.
            max_rows: Truncate result to this many rows.

        Returns:
            QueryResult with rows, columns, and error/timed_out flags.
        """

    @property
    @abstractmethod
    def identity(self) -> str:
        """
        Stable identifier for this warehouse connection, e.g.
        "prod-mysql://mydb" or "bigquery://project/dataset".

        Used as the cache namespace — must be the same across restarts
        for the same logical warehouse.
        """

    def quote_identifier(self, name: str) -> str:
        """
        Quote a validated identifier path for this warehouse dialect.

        The default implementation returns the identifier unchanged after
        validation in the caller. Clients may override this to apply
        dialect-specific quoting such as backticks or double quotes.
        """
        return name
