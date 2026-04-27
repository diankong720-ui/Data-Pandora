from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DOMAIN_PACKS_ROOT = REPO_ROOT / "skills" / "deep-research" / "domain-packs"


def _iter_pack_paths() -> list[Path]:
    return sorted(
        path for path in DOMAIN_PACKS_ROOT.glob("*/pack.json")
        if path.is_file()
    )


def _read_pack(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def list_domain_packs() -> list[dict[str, str]]:
    """Return lightweight metadata for all available domain packs."""
    packs: list[dict[str, str]] = []
    for path in _iter_pack_paths():
        pack = _read_pack(path)
        packs.append(
            {
                "pack_id": str(pack["pack_id"]),
                "label": str(pack.get("label", pack["pack_id"])),
                "path": str(path),
            }
        )
    return packs


def load_domain_pack(pack_id: str) -> dict[str, Any]:
    """Load a specific pack by pack_id."""
    for path in _iter_pack_paths():
        pack = _read_pack(path)
        if pack.get("pack_id") == pack_id:
            return pack
    raise FileNotFoundError(f"Domain pack not found: {pack_id}")


def load_available_domain_packs() -> list[dict[str, Any]]:
    """Load all pack payloads for Stage 1 selection."""
    return [load_domain_pack(pack["pack_id"]) for pack in list_domain_packs()]


def deterministic_slug(label: str) -> str:
    """Normalize a business label into a stable ASCII snake_case slug."""
    ascii_label = label.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_label)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "generic"


def resolve_target_pack_id(
    active_pack_id: str,
    business_label: str,
    existing_packs: list[dict[str, Any]] | None = None,
) -> str:
    """
    Reuse an existing context pack when possible; otherwise generate the
    deterministic slug documented for new business contexts.
    """
    if existing_packs is None:
        existing_packs = list_domain_packs()

    known_ids = {str(pack["pack_id"]) for pack in existing_packs if "pack_id" in pack}
    if active_pack_id != "generic" and active_pack_id in known_ids:
        return active_pack_id

    slug = deterministic_slug(business_label)
    if slug in known_ids:
        return slug
    return slug
