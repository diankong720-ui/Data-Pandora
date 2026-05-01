from __future__ import annotations

from typing import Any


_EPHEMERAL_RESULT_ROWS: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}


def _session_key(session_id: str | None) -> str:
    return session_id if isinstance(session_id, str) and session_id else "legacy"


def register_ephemeral_result_rows(
    slug: str | None,
    *,
    session_id: str | None,
    round_number: int | None,
    query_id: str | None,
    rows: list[dict[str, Any]] | None,
) -> None:
    """Keep same-process query rows available for chart rendering without persisting them."""
    if not slug or not isinstance(query_id, str) or not query_id:
        return
    if not isinstance(round_number, int) or round_number <= 0:
        return
    if not isinstance(rows, list):
        return
    round_id = f"round_{round_number}"
    _EPHEMERAL_RESULT_ROWS[(slug, _session_key(session_id), round_id, query_id)] = [
        dict(row) for row in rows if isinstance(row, dict)
    ]


def get_ephemeral_result_rows(
    slug: str,
    *,
    session_id: str | None,
    round_id: str,
    query_id: str,
) -> list[dict[str, Any]] | None:
    rows = _EPHEMERAL_RESULT_ROWS.get((slug, _session_key(session_id), round_id, query_id))
    if rows is None:
        return None
    return [dict(row) for row in rows]


def clear_ephemeral_result_rows(
    slug: str,
    *,
    session_id: str | None,
    round_id: str,
    query_id: str,
) -> None:
    _EPHEMERAL_RESULT_ROWS.pop((slug, _session_key(session_id), round_id, query_id), None)


def clear_session_ephemeral_result_rows(slug: str, *, session_id: str | None) -> None:
    session = _session_key(session_id)
    for key in list(_EPHEMERAL_RESULT_ROWS):
        if key[0] == slug and key[1] == session:
            _EPHEMERAL_RESULT_ROWS.pop(key, None)
