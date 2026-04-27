from __future__ import annotations

from typing import Any, Sequence


def escape_sql_string(value: str) -> str:
    """Escape backslash and single-quote for SQL string literals."""
    return value.replace("\\", "\\\\").replace("'", "''")


def compile_sql_literal(value: Any) -> str:
    """Convert a Python value to a safe SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + escape_sql_string(str(value)) + "'"


def render_parameterized_sql(sql: str, params: Sequence[Any] | None = None) -> str:
    """
    Substitute %s placeholders in a SQL template with safely compiled literals.

    Uses sequential string partitioning (not Python %-formatting) so that
    literal % characters in SQL — e.g. LIKE '%pattern%' — are never treated
    as format specifiers.

    Example:
        render_parameterized_sql(
            "SELECT * FROM orders WHERE channel = %s AND year = %s",
            ["mobile", 2026]
        )
        # → "SELECT * FROM orders WHERE channel = 'mobile' AND year = 2026"
    """
    if not params:
        return sql

    literals = [compile_sql_literal(v) for v in params]
    segments: list[str] = []
    remaining = sql

    for i, literal in enumerate(literals):
        before, found, remaining = remaining.partition('%s')
        if not found:
            raise ValueError(
                f"SQL has fewer '%s' placeholders ({i}) than parameters ({len(literals)})."
            )
        segments.append(before)
        segments.append(literal)

    if '%s' in remaining:
        raise ValueError(
            f"SQL has more '%s' placeholders than parameters ({len(literals)})."
        )

    segments.append(remaining)
    return ''.join(segments)
