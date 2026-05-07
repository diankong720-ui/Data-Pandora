from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from runtime.contracts import validate_web_search_request, validate_web_search_result


def _parse_positive_int(value: int | str, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{label} must be a positive integer.")
    return parsed


class WebSearchClient(ABC):
    """Provider-neutral web search client for contract-governed evidence retrieval."""

    @property
    @abstractmethod
    def provider(self) -> str:
        """Stable provider label, for example ``tavily``."""

    @abstractmethod
    def search(
        self,
        request: dict[str, Any],
        *,
        timeout: float = 30.0,
        max_results: int | None = None,
    ) -> dict[str, Any]:
        """
        Execute one WebSearchRequest and return provider-normalized payload.

        The runtime wraps this payload into WebSearchResult and persists only
        redacted, evidence-facing fields. API keys must remain provider-local.
        """


class TavilySearchClient(WebSearchClient):
    """Minimal Tavily adapter using stdlib HTTP so core runtime stays dependency-light."""

    endpoint = "https://api.tavily.com/search"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        search_depth: str | None = None,
        max_results: int | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("TAVILY_API_KEY", "")
        self.search_depth = search_depth or os.getenv("TAVILY_SEARCH_DEPTH", "advanced")
        raw_max_results = max_results if max_results is not None else os.getenv("TAVILY_MAX_RESULTS")
        self.max_results = _parse_positive_int(raw_max_results, "TAVILY_MAX_RESULTS") if raw_max_results else 5
        if not self.api_key:
            raise ValueError("TAVILY_API_KEY is required to use TavilySearchClient.")

    @property
    def provider(self) -> str:
        return "tavily"

    def search(
        self,
        request: dict[str, Any],
        *,
        timeout: float = 30.0,
        max_results: int | None = None,
    ) -> dict[str, Any]:
        source_policy = request.get("source_policy") if isinstance(request.get("source_policy"), dict) else {}
        payload = {
            "api_key": self.api_key,
            "query": request["query"],
            "search_depth": source_policy.get("search_depth") or self.search_depth,
            "max_results": int(source_policy.get("max_results") or max_results or self.max_results),
            "include_answer": False,
            "include_raw_content": True,
        }
        include_domains = source_policy.get("include_domains")
        if isinstance(include_domains, list) and include_domains:
            payload["include_domains"] = include_domains
        exclude_domains = source_policy.get("exclude_domains")
        if isinstance(exclude_domains, list) and exclude_domains:
            payload["exclude_domains"] = exclude_domains

        data = json.dumps(payload).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(http_request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
        decoded = json.loads(body)
        results = decoded.get("results", []) if isinstance(decoded, dict) else []
        normalized_results: list[dict[str, Any]] = []
        for item in results if isinstance(results, list) else []:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("url") or "Untitled result"
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            normalized_results.append(
                {
                    "title": str(title),
                    "url": url,
                    "content": item.get("content"),
                    "raw_content": item.get("raw_content"),
                    "published_date": item.get("published_date"),
                    "score": item.get("score"),
                    "source": item.get("source"),
                }
            )
        return {
            "results": normalized_results,
            "fetched_pages": [
                {"url": item["url"], "title": item["title"]}
                for item in normalized_results
            ],
            "source_quality_notes": [],
        }


def get_web_search_configuration_status(
    *,
    web_client: WebSearchClient | None = None,
    mode: str = "auto",
) -> dict[str, Any]:
    """Return a redacted web capability snapshot for manifests and preflight output."""
    if mode not in {"auto", "skip", "required"}:
        raise ValueError("web search mode must be auto, skip, or required.")
    if mode == "skip":
        return {
            "enabled": False,
            "mode": mode,
            "provider": None,
            "configured": False,
            "missing": [],
            "reason": "web search explicitly skipped",
        }
    if web_client is not None:
        return {
            "enabled": True,
            "mode": mode,
            "provider": web_client.provider,
            "configured": True,
            "missing": [],
            "reason": "host supplied web client",
        }
    configured = bool(os.getenv("TAVILY_API_KEY"))
    raw_max_results = os.getenv("TAVILY_MAX_RESULTS", "5")
    invalid: list[str] = []
    try:
        tavily_max_results = _parse_positive_int(raw_max_results, "TAVILY_MAX_RESULTS")
    except ValueError:
        tavily_max_results = None
        invalid.append("TAVILY_MAX_RESULTS")
    enabled = configured and not invalid
    return {
        "enabled": enabled,
        "mode": mode,
        "provider": "tavily" if enabled else None,
        "configured": enabled,
        "missing": [] if configured else ["TAVILY_API_KEY"],
        "invalid": invalid,
        "defaults": {
            "TAVILY_SEARCH_DEPTH": os.getenv("TAVILY_SEARCH_DEPTH", "advanced"),
            "TAVILY_MAX_RESULTS": tavily_max_results,
        }
        if configured and tavily_max_results is not None
        else {},
        "reason": (
            "tavily environment configured"
            if enabled
            else "invalid Tavily configuration"
            if configured and invalid
            else "web search provider is not configured"
        ),
    }


def resolve_default_web_client(
    *,
    web_client: WebSearchClient | None = None,
    mode: str = "auto",
) -> WebSearchClient | None:
    if mode not in {"auto", "skip", "required"}:
        raise ValueError("web search mode must be auto, skip, or required.")
    if mode == "skip":
        return None
    if web_client is not None:
        return web_client
    status = get_web_search_configuration_status(mode=mode)
    if status.get("enabled"):
        return TavilySearchClient()
    if mode == "required":
        missing = ", ".join(status.get("missing") or [])
        invalid = ", ".join(status.get("invalid") or [])
        details = "; ".join(
            item
            for item in (
                f"missing: {missing}" if missing else "",
                f"invalid: {invalid}" if invalid else "",
            )
            if item
        )
        raise ValueError(
            "Web search is required but no usable provider is configured."
            + (f" {details}." if details else "")
        )
    return None


def execute_web_search_request(
    web_client: WebSearchClient | None,
    request: dict[str, Any],
    *,
    timeout: float = 30.0,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Execute one WebSearchRequest and normalize all failures as evidence metadata."""
    validate_web_search_request(request)
    base = {
        "search_id": request["search_id"],
        "question": request["question"],
        "query": request["query"],
        "provider": web_client.provider if web_client is not None else "unavailable",
        "retrieved_at": time.time(),
        "results": [],
        "fetched_pages": [],
        "source_quality_notes": [],
        "notes": [],
    }
    if web_client is None:
        result = {
            **base,
            "status": "blocked",
            "error": "Web search provider is not configured.",
            "source_quality_notes": ["web_search_unavailable"],
        }
        validate_web_search_result(result)
        return result
    try:
        payload = web_client.search(request, timeout=timeout, max_results=max_results)
    except TimeoutError as exc:
        result = {**base, "status": "timeout", "error": str(exc)}
        validate_web_search_result(result)
        return result
    except urllib.error.URLError as exc:
        result = {**base, "status": "failed", "error": str(exc)}
        validate_web_search_result(result)
        return result
    except Exception as exc:
        result = {**base, "status": "failed", "error": str(exc)}
        validate_web_search_result(result)
        return result

    result = {
        **base,
        "status": "success",
        "provider": web_client.provider,
        "results": payload.get("results", []) if isinstance(payload, dict) else [],
        "fetched_pages": payload.get("fetched_pages", []) if isinstance(payload, dict) else [],
        "source_quality_notes": payload.get("source_quality_notes", []) if isinstance(payload, dict) else [],
    }
    validate_web_search_result(result)
    return result
