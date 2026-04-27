from __future__ import annotations

from typing import Any

from runtime.domain_packs import list_domain_packs, resolve_target_pack_id
from runtime.persistence import persist_artifact


DOMAIN_PACK_SUGGESTION_REQUIRED_FIELDS = (
    "session_slug",
    "active_pack_id",
    "target_pack_id",
    "suggested_updates",
    "note",
)


def validate_domain_pack_suggestions(suggestions: dict[str, Any], *, slug: str | None = None) -> None:
    missing = [field for field in DOMAIN_PACK_SUGGESTION_REQUIRED_FIELDS if field not in suggestions]
    if missing:
        raise ValueError(
            f"DomainPackSuggestions missing required fields: {', '.join(missing)}"
        )
    if slug is not None and suggestions["session_slug"] != slug:
        raise ValueError("DomainPackSuggestions.session_slug must match the session slug.")

    updates = suggestions["suggested_updates"]
    if not isinstance(updates, dict):
        raise ValueError("DomainPackSuggestions.suggested_updates must be an object.")
    for field in (
        "taxonomy",
        "lexicon",
        "performance_risks",
        "driver_family_templates",
        "domain_priors",
        "operator_preferences",
    ):
        if field not in updates:
            raise ValueError(f"DomainPackSuggestions.suggested_updates missing field: {field}")


def persist_domain_pack_suggestions(
    slug: str,
    suggestions: dict[str, Any],
    *,
    business_label: str | None = None,
    session_id: str | None = None,
) -> str:
    """
    Persist domain pack suggestions. If target_pack_id is blank and a business
    label is provided, fill it deterministically using the documented rule.
    """
    if not suggestions.get("target_pack_id"):
        if not business_label:
            raise ValueError(
                "persist_domain_pack_suggestions needs business_label when target_pack_id is absent."
            )
        suggestions = {
            **suggestions,
            "target_pack_id": resolve_target_pack_id(
                str(suggestions.get("active_pack_id", "generic")),
                business_label,
                existing_packs=list_domain_packs(),
            ),
        }

    validate_domain_pack_suggestions(suggestions, slug=slug)
    return persist_artifact(
        slug,
        "domain_pack_suggestions.json",
        suggestions,
        session_id=session_id,
        strict_session=bool(session_id),
    )
