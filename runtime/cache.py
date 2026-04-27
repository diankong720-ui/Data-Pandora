from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CACHE_ROOT = Path("RESEARCH") / ".sql_cache"
CACHE_VERSION = 1
CACHE_WRITE_ENABLED = False


@dataclass
class CacheEntry:
    sql_sha256: str
    warehouse_identity: str
    rows: list[dict[str, Any]]
    columns: list[str]
    row_count: int
    cached_at: float
    cache_version: int = CACHE_VERSION


# ---------------------------------------------------------------------------
# Tool 2 — Cache Lookup
# ---------------------------------------------------------------------------

def _cache_key(warehouse_identity: str, sql: str) -> str:
    payload = f"{warehouse_identity}||{sql.strip()}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _cache_path(key: str) -> Path:
    return CACHE_ROOT / key[:2] / f"{key}.json"


def set_cache_write_enabled(enabled: bool) -> None:
    """Enable or disable persistence of live query results into the local cache."""
    global CACHE_WRITE_ENABLED
    CACHE_WRITE_ENABLED = bool(enabled)


def lookup_cache(
    warehouse_identity: str,
    sql: str,
    *,
    max_age_seconds: float | None = None,
) -> dict[str, Any]:
    """
    LLM-callable cache lookup tool.

    Checks whether `sql` was previously executed against `warehouse_identity`.
    Returns a plain dict with hit/miss and preview rows when available.

    The LLM decides whether cached evidence is sufficient for the current task.
    This function only reports what is in the cache.

    Args:
        warehouse_identity: The client.identity value for the target warehouse.
        sql:                The exact SQL string to look up.
        max_age_seconds:    If set, treat entries older than this as a miss.
    """
    key = _cache_key(warehouse_identity, sql)
    path = _cache_path(key)

    if not path.exists():
        return {
            "status": "miss",
            "sql_sha256": key,
            "warehouse_identity": warehouse_identity,
            "metadata_path": None,
            "preview_rows": [],
            "row_count": 0,
            "cached_at": None,
        }

    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "status": "miss",
            "sql_sha256": key,
            "warehouse_identity": warehouse_identity,
            "metadata_path": str(path),
            "preview_rows": [],
            "row_count": 0,
            "cached_at": None,
        }

    if max_age_seconds is not None:
        age = time.time() - entry.get("cached_at", 0)
        if age > max_age_seconds:
            return {
                "status": "miss",
                "sql_sha256": key,
                "warehouse_identity": warehouse_identity,
                "metadata_path": str(path),
                "preview_rows": [],
                "row_count": 0,
                "cached_at": entry.get("cached_at"),
                "stale": True,
            }

    rows = entry.get("rows", [])
    return {
        "status": "hit",
        "sql_sha256": key,
        "warehouse_identity": entry.get("warehouse_identity", warehouse_identity),
        "metadata_path": str(path),
        "preview_rows": rows[:10],
        "row_count": entry.get("row_count", len(rows)),
        "cached_at": entry.get("cached_at"),
    }


def write_cache(
    warehouse_identity: str,
    sql: str,
    rows: list[dict[str, Any]],
    columns: list[str],
) -> str | None:
    """
    Write a query result to the cache when cache writes are explicitly enabled.

    Returns the cache key (sha256) when persisted, otherwise None.
    """
    if not CACHE_WRITE_ENABLED:
        return None

    key = _cache_key(warehouse_identity, sql)
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "cache_version": CACHE_VERSION,
        "sql_sha256": key,
        "warehouse_identity": warehouse_identity,
        "rows": rows,
        "columns": columns,
        "row_count": len(rows),
        "cached_at": time.time(),
    }
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    return key


def load_cached_rows(
    warehouse_identity: str,
    sql: str,
    *,
    max_age_seconds: float | None = None,
) -> list[dict[str, Any]] | None:
    """Return the full cached row list, or None on miss or stale entry."""
    key = _cache_key(warehouse_identity, sql)
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        if max_age_seconds is not None:
            age = time.time() - entry.get("cached_at", 0)
            if age > max_age_seconds:
                return None
        return entry.get("rows", [])
    except (OSError, json.JSONDecodeError):
        return None
