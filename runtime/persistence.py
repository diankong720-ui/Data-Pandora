from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

RESEARCH_ROOT = Path("RESEARCH")
SESSIONS_DIRNAME = "sessions"
LATEST_SESSION_FILENAME = "latest_session.json"
SESSION_STATE_FILENAME = "session_state.json"
DEFAULT_GENERATION_ID = "gen_1"


# ---------------------------------------------------------------------------
# Path traversal guard
# ---------------------------------------------------------------------------

def _assert_within_root(path: Path, root: Path, *, label: str) -> None:
    """Raise ValueError if path resolves outside the provided root."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"Path traversal blocked: {path!r} escapes {label}")


def _assert_within_research(path: Path) -> None:
    """Raise ValueError if path resolves outside RESEARCH_ROOT."""
    _assert_within_root(path, RESEARCH_ROOT, label="RESEARCH_ROOT")


def _validate_single_path_component(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    normalized = value.strip()
    candidate = Path(normalized)
    if candidate.is_absolute():
        raise ValueError(f"{label} must be a relative path component.")
    parts = candidate.parts
    if len(parts) != 1 or parts[0] in {"", ".", ".."}:
        raise ValueError(f"{label} must be a single safe path component.")
    return normalized


def _normalize_relative_subpath(value: str, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty relative path.")
    candidate = Path(value.strip())
    if candidate.is_absolute():
        raise ValueError(f"{label} must be relative.")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"{label} must not contain path traversal segments.")
    return candidate


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def get_slug_root(slug: str) -> Path:
    safe_slug = _validate_single_path_component(slug, label="slug")
    root = RESEARCH_ROOT / safe_slug
    _assert_within_research(root)
    return root


def get_latest_session_path(slug: str) -> Path:
    path = get_slug_root(slug) / LATEST_SESSION_FILENAME
    _assert_within_research(path)
    return path


def _read_json_file(path: Path) -> dict[str, Any] | None:
    _assert_within_research(path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def generate_session_id() -> str:
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"session_{timestamp}_{secrets.token_hex(4)}"


def get_session_root(slug: str, session_id: str) -> Path:
    safe_session_id = _validate_single_path_component(session_id, label="session_id")
    slug_root = get_slug_root(slug)
    root = slug_root / SESSIONS_DIRNAME / safe_session_id
    _assert_within_root(root, slug_root, label=f"slug_root({slug!r})")
    return root


def read_latest_session(slug: str) -> dict[str, Any] | None:
    return _read_json_file(get_latest_session_path(slug))


def resolve_session_id(slug: str, session_id: str | None = None) -> str | None:
    if isinstance(session_id, str) and session_id:
        return session_id
    latest = read_latest_session(slug)
    resolved = latest.get("session_id") if isinstance(latest, dict) else None
    return str(resolved) if isinstance(resolved, str) and resolved else None


def _resolve_session_id_strict(
    slug: str,
    session_id: str | None,
    *,
    strict_session: bool,
    legacy_layout: bool,
) -> str | None:
    if strict_session and not (isinstance(session_id, str) and session_id):
        raise ValueError(
            f"session_id is required for strict session access to slug {slug!r}."
        )
    if not legacy_layout and not (isinstance(session_id, str) and session_id):
        raise ValueError(
            f"session_id is required for session-scoped access to slug {slug!r}. "
            "Pass legacy_layout=True only for explicit legacy reads."
        )
    return resolve_session_id(slug, session_id=session_id)


def set_latest_session(
    slug: str,
    session_id: str,
    *,
    raw_question: str | None = None,
    created_at: float | None = None,
) -> str:
    now = time.time()
    session_root = get_session_root(slug, session_id)
    session_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "slug": slug,
        "session_id": session_id,
        "session_root": str(session_root),
        "created_at": created_at or now,
        "updated_at": now,
    }
    if raw_question:
        payload["raw_question"] = raw_question
    path = get_latest_session_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def start_session(
    slug: str,
    *,
    session_id: str | None = None,
    raw_question: str | None = None,
    created_at: float | None = None,
) -> dict[str, Any]:
    resolved_session_id = resolve_session_id(slug, session_id=session_id) if session_id else generate_session_id()
    session_root = get_session_root(slug, resolved_session_id)
    session_root.mkdir(parents=True, exist_ok=True)
    set_latest_session(
        slug,
        resolved_session_id,
        raw_question=raw_question,
        created_at=created_at,
    )
    return {
        "slug": slug,
        "session_id": resolved_session_id,
        "session_root": str(session_root),
    }


def _artifact_root(
    slug: str,
    *,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
    for_write: bool = False,
) -> tuple[Path, str | None]:
    resolved_session_id = _resolve_session_id_strict(
        slug,
        session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )
    if resolved_session_id is not None:
        root = get_session_root(slug, resolved_session_id)
        if for_write:
            root.mkdir(parents=True, exist_ok=True)
        return root, resolved_session_id

    root = get_slug_root(slug)
    if for_write:
        root.mkdir(parents=True, exist_ok=True)
    return root, None


def get_session_context(
    slug: str,
    session_id: str | None = None,
    *,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> dict[str, Any]:
    root, resolved_session_id = _artifact_root(
        slug,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
        for_write=False,
    )
    return {
        "slug": slug,
        "session_id": resolved_session_id,
        "session_root": str(root),
        "is_legacy_layout": resolved_session_id is None,
    }


# ---------------------------------------------------------------------------
# Tool 4 — Artifact Persistence
# ---------------------------------------------------------------------------

def get_active_generation_id(
    slug: str,
    *,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> str:
    """Return the active generation id for this session slug."""
    root, _ = _artifact_root(
        slug,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
        for_write=False,
    )
    state = _read_json_file(root / SESSION_STATE_FILENAME)
    generation_id = state.get("active_generation_id") if isinstance(state, dict) else None
    return str(generation_id) if isinstance(generation_id, str) and generation_id else DEFAULT_GENERATION_ID


def _rounds_root(
    slug: str,
    generation_id: str | None = None,
    *,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> Path:
    root, _ = _artifact_root(
        slug,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
        for_write=False,
    )
    resolved_generation_id = generation_id or get_active_generation_id(
        slug,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )
    resolved_generation_id = _validate_single_path_component(
        resolved_generation_id,
        label="generation_id",
    )
    generation_root = root / "rounds" / resolved_generation_id
    legacy_root = root / "rounds"
    _assert_within_root(generation_root, root, label="artifact root")
    _assert_within_root(legacy_root, root, label="artifact root")

    # Backward compatibility: some legacy sessions stored round files directly under rounds/.
    if resolved_generation_id == DEFAULT_GENERATION_ID and not generation_root.exists() and legacy_root.exists():
        return legacy_root
    return generation_root


def persist_artifact(
    slug: str,
    filename: str,
    content: Any,
    *,
    subdir: str | None = None,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> str:
    """
    Write content into the active session root when available.

    When the slug has no session index, fall back to the legacy layout
    `RESEARCH/<slug>/...` for backward compatibility.
    """
    root, _ = _artifact_root(
        slug,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
        for_write=True,
    )
    base_root = root
    if subdir:
        root = root / _normalize_relative_subpath(subdir, label="subdir")
        _assert_within_root(root, base_root, label="artifact root")

    path = root / _normalize_relative_subpath(filename, label="filename")
    _assert_within_root(path, root, label="artifact root")

    root.mkdir(parents=True, exist_ok=True)

    if isinstance(content, (dict, list)):
        path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        path.write_text(str(content), encoding="utf-8")

    return str(path)


def persist_binary_artifact(
    slug: str,
    filename: str,
    content: bytes,
    *,
    subdir: str | None = None,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> str:
    """Write raw bytes into the active session root when available."""
    root, _ = _artifact_root(
        slug,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
        for_write=True,
    )
    base_root = root
    if subdir:
        root = root / _normalize_relative_subpath(subdir, label="subdir")
        _assert_within_root(root, base_root, label="artifact root")

    path = root / _normalize_relative_subpath(filename, label="filename")
    _assert_within_root(path, root, label="artifact root")

    root.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def persist_manifest(
    slug: str,
    metadata: dict[str, Any],
    *,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> str:
    """Write or update the session manifest."""
    context = get_session_context(
        slug,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )
    manifest = {
        "slug": slug,
        "session_id": context["session_id"],
        "session_root": context["session_root"],
        "created_at": metadata.get("created_at", time.time()),
        "updated_at": time.time(),
        **metadata,
    }
    return persist_artifact(
        slug,
        "manifest.json",
        manifest,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )


def append_execution_log(
    slug: str,
    log_entry: dict[str, Any],
    *,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> str:
    """Append a runtime execution event to execution_log.json."""
    existing = read_artifact(
        slug,
        "execution_log.json",
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )
    if not isinstance(existing, dict):
        existing = {
            "version": 1,
            "entries": [],
        }

    entries = existing.get("entries")
    if not isinstance(entries, list):
        entries = []

    entries.append(log_entry)
    existing["entries"] = entries
    existing["updated_at"] = time.time()
    return persist_artifact(
        slug,
        "execution_log.json",
        existing,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )


def read_execution_log(
    slug: str,
    *,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> dict[str, Any]:
    """Read execution_log.json using the stable wrapper shape."""
    existing = read_artifact(
        slug,
        "execution_log.json",
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )
    if isinstance(existing, dict) and isinstance(existing.get("entries"), list):
        return existing
    return {
        "version": 1,
        "entries": [],
    }


def persist_round_bundle(
    slug: str,
    round_id: str,
    contract: dict[str, Any],
    executed_queries: list[dict[str, Any]],
    evaluation: dict[str, Any],
    *,
    generation_id: str | None = None,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> str:
    """Persist the round bundle contract used by the shared docs."""
    resolved_generation_id = generation_id or get_active_generation_id(
        slug,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )
    bundle = {
        "generation_id": resolved_generation_id,
        "contract": contract,
        "executed_queries": executed_queries,
        "evaluation": evaluation,
    }
    return persist_artifact(
        slug,
        f"{round_id}.json",
        bundle,
        subdir=f"rounds/{resolved_generation_id}",
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )


def read_round_bundle(
    slug: str,
    round_id: str,
    *,
    generation_id: str | None = None,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> dict[str, Any] | None:
    """Read one persisted round bundle by round id."""
    root = _rounds_root(
        slug,
        generation_id=generation_id,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )
    path = root / f"{round_id}.json"
    payload = _read_json_file(path)
    if payload is None:
        return None
    payload.setdefault(
        "generation_id",
        generation_id
        or get_active_generation_id(
            slug,
            session_id=session_id,
            strict_session=strict_session,
            legacy_layout=legacy_layout,
        ),
    )
    return payload


def list_round_bundles(
    slug: str,
    *,
    generation_id: str | None = None,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> list[dict[str, Any]]:
    """Return persisted round bundles for one generation sorted by round id filename."""
    resolved_generation_id = generation_id or get_active_generation_id(
        slug,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )
    root = _rounds_root(
        slug,
        generation_id=resolved_generation_id,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )
    _assert_within_research(root)
    if not root.exists():
        return []

    bundles: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        _assert_within_research(path)
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(content, dict):
            content.setdefault("generation_id", resolved_generation_id)
            bundles.append(content)
    return bundles


def load_session_evidence(
    slug: str,
    *,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> dict[str, Any]:
    """Aggregate the stable artifacts needed by downstream consumers."""
    context = get_session_context(
        slug,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )
    active_generation_id = get_active_generation_id(
        slug,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
    )
    return {
        **context,
        "active_generation_id": active_generation_id,
        "intent": read_artifact(slug, "intent.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "intent_sidecar": read_artifact(slug, "intent_sidecar.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "environment_scan": read_artifact(slug, "environment_scan.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "plan": read_artifact(slug, "plan.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "execution_log": read_execution_log(slug, session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "round_bundles": list_round_bundles(
            slug,
            generation_id=active_generation_id,
            session_id=session_id,
            strict_session=strict_session,
            legacy_layout=legacy_layout,
        ),
        "final_answer": read_artifact(slug, "final_answer.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "report_evidence": read_artifact(slug, "report_evidence.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "report_evidence_index": read_artifact(
            slug,
            "report_evidence_index.json",
            session_id=session_id,
            strict_session=strict_session,
            legacy_layout=legacy_layout,
        ),
        "chart_spec_bundle": read_artifact(slug, "chart_spec_bundle.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "descriptive_stats": read_artifact(slug, "descriptive_stats.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "visualization_manifest": read_artifact(slug, "visualization_manifest.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "report": read_artifact(slug, "report.md", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "domain_pack_suggestions": read_artifact(slug, "domain_pack_suggestions.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "protocol_trace": read_artifact(slug, "protocol_trace.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "evidence_graph": read_artifact(slug, "evidence_graph.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "compliance_report": read_artifact(slug, "compliance_report.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "session_state": read_artifact(slug, "session_state.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
        "manifest": read_artifact(slug, "manifest.json", session_id=session_id, strict_session=strict_session, legacy_layout=legacy_layout),
    }


def list_artifacts(
    slug: str,
    *,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> list[str]:
    """Return relative artifact paths for the resolved session or legacy slug root."""
    root, _ = _artifact_root(
        slug,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
        for_write=False,
    )
    if not root.exists():
        return []
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


def read_artifact(
    slug: str,
    filename: str,
    *,
    subdir: str | None = None,
    session_id: str | None = None,
    strict_session: bool = False,
    legacy_layout: bool = False,
) -> Any:
    """Read a persisted artifact as parsed JSON or raw text."""
    root, _ = _artifact_root(
        slug,
        session_id=session_id,
        strict_session=strict_session,
        legacy_layout=legacy_layout,
        for_write=False,
    )
    base_root = root
    if subdir:
        root = root / _normalize_relative_subpath(subdir, label="subdir")
        _assert_within_root(root, base_root, label="artifact root")

    path = root / _normalize_relative_subpath(filename, label="filename")
    _assert_within_root(path, root, label="artifact root")

    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
