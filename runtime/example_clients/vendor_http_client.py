from __future__ import annotations

"""
Generic signed HTTP warehouse client.

Environment variables:
    VENDOR_WAREHOUSE_BASE_URL   HTTPS endpoint origin, e.g. https://<warehouse-host>
    VENDOR_WAREHOUSE_PATH       SQL endpoint path, e.g. /api/sql
    VENDOR_WAREHOUSE_CHANNEL    Channel / app id.
    VENDOR_WAREHOUSE_SECRET     Request signing secret.

Optional:
    VENDOR_WAREHOUSE_IDENTITY   Stable cache namespace label.
    VENDOR_WAREHOUSE_CONNECT_TIMEOUT
    VENDOR_WAREHOUSE_QUERY_TIMEOUT
    VENDOR_WAREHOUSE_MAX_ROWS
"""

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

try:
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:  # type: ignore[misc]
        return False

from runtime.interface import QueryResult, WarehouseClient


DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_QUERY_TIMEOUT = 60.0
DEFAULT_MAX_ROWS = 200_000


def _load_env() -> None:
    load_dotenv()


def _env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name) or default


def _scrub_credentials(message: str) -> str:
    for secret_key in ("VENDOR_WAREHOUSE_SECRET",):
        secret = os.getenv(secret_key)
        if secret:
            message = message.replace(secret, "<secret>")
    return re.sub(r'("X-Warehouse-Signature"\s*:\s*")[^"]+', r'\1<signature>', message)


@dataclass(frozen=True)
class VendorHttpConfig:
    base_url: str
    path: str
    channel: str
    secret: str
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    query_timeout: float = DEFAULT_QUERY_TIMEOUT
    max_rows: int = DEFAULT_MAX_ROWS

    @classmethod
    def from_env(cls) -> "VendorHttpConfig":
        _load_env()
        base_url = _env("VENDOR_WAREHOUSE_BASE_URL")
        path = _env("VENDOR_WAREHOUSE_PATH")
        channel = _env("VENDOR_WAREHOUSE_CHANNEL")
        secret = _env("VENDOR_WAREHOUSE_SECRET")
        missing = [
            key
            for key, value in (
                ("VENDOR_WAREHOUSE_BASE_URL", base_url),
                ("VENDOR_WAREHOUSE_PATH", path),
                ("VENDOR_WAREHOUSE_CHANNEL", channel),
                ("VENDOR_WAREHOUSE_SECRET", secret),
            )
            if not value
        ]
        if missing:
            raise ValueError("Missing vendor warehouse env vars: " + ", ".join(missing))

        return cls(
            base_url=str(base_url).rstrip("/"),
            path=str(path),
            channel=str(channel),
            secret=str(secret),
            connect_timeout=float(_env("VENDOR_WAREHOUSE_CONNECT_TIMEOUT", str(DEFAULT_CONNECT_TIMEOUT))),
            query_timeout=float(_env("VENDOR_WAREHOUSE_QUERY_TIMEOUT", str(DEFAULT_QUERY_TIMEOUT))),
            max_rows=int(_env("VENDOR_WAREHOUSE_MAX_ROWS", str(DEFAULT_MAX_ROWS))),
        )


class VendorHttpWarehouseClient(WarehouseClient):
    """Adapter from a signed HTTP SQL API to Pandora's WarehouseClient."""

    def __init__(self, config: VendorHttpConfig | None = None) -> None:
        self.config = config or VendorHttpConfig.from_env()
        identity = _env("VENDOR_WAREHOUSE_IDENTITY")
        self._identity = identity or f"vendor-http://{self.config.base_url}{self.config.path}#{self.config.channel}"

    @property
    def identity(self) -> str:
        return self._identity

    def quote_identifier(self, name: str) -> str:
        return ".".join(f"`{part}`" for part in name.split("."))

    def _sign(self, body: str, ts: int) -> str:
        payload = f"POST\n{self.config.path}\n{self.config.channel}\n{ts}\n{self.config.secret}\n{body}\n"
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def _post_sqls(
        self,
        sqls: dict[str, str],
        *,
        timeout: tuple[float, float],
    ) -> dict[str, Any]:
        if _requests is None:
            raise RuntimeError("requests package is required for VendorHttpWarehouseClient.")

        body = json.dumps({"sqls": sqls}, ensure_ascii=False, separators=(",", ":"))
        ts = int(time.time())
        response = _requests.post(
            self.config.base_url + self.config.path,
            data=body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Warehouse-Channel": self.config.channel,
                "X-Warehouse-Ts": str(ts),
                "X-Warehouse-Signature": self._sign(body, ts),
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Unexpected warehouse response: top-level payload is not an object.")
        return payload

    def execute(
        self,
        sql: str,
        *,
        timeout: float = 30.0,
        max_rows: int = 10_000,
    ) -> QueryResult:
        output_name = "pandora_result"
        try:
            payload = self._post_sqls(
                {output_name: sql},
                timeout=(self.config.connect_timeout, timeout or self.config.query_timeout),
            )
            data = payload.get("data", {})
            if not isinstance(data, dict):
                raise ValueError("Unexpected warehouse response: `data` must be an object.")
            rows = data.get(output_name)
            if rows is None and len(data) == 1:
                rows = next(iter(data.values()))
            if rows is None:
                rows = []
            if not isinstance(rows, list):
                raise ValueError("Unexpected warehouse response: query rows must be a list.")
        except Exception as exc:
            message = _scrub_credentials(str(exc))
            timed_out = "timeout" in message.lower() or "timed out" in message.lower()
            return QueryResult.from_error(message, timed_out=timed_out)

        normalized_rows = [dict(row) for row in rows if isinstance(row, dict)]
        limit = min(max_rows, self.config.max_rows) if max_rows > 0 else self.config.max_rows
        if limit > 0:
            normalized_rows = normalized_rows[:limit]

        columns: list[str] = []
        for row in normalized_rows:
            for key in row.keys():
                if key not in columns:
                    columns.append(key)
        return QueryResult(rows=normalized_rows, columns=columns)


def create_client() -> VendorHttpWarehouseClient:
    return VendorHttpWarehouseClient()
