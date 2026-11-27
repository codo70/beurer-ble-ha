"""Assign guest weighings to a person by weight (no HA imports)."""

from __future__ import annotations

from .const import AUTO_ASSIGN_MARGIN, AUTO_ASSIGN_MAX_DIFF


def pick_by_weight(weight: float, candidates: dict[str, float]) -> str | None:
    """Return the person whose last weight is clearly closest, else None."""
    ranked = sorted(candidates.items(), key=lambda kv: abs(kv[1] - weight))
    if not ranked or abs(ranked[0][1] - weight) > AUTO_ASSIGN_MAX_DIFF:
        return None
    if len(ranked) > 1 and abs(ranked[1][1] - weight) - abs(ranked[0][1] - weight) < AUTO_ASSIGN_MARGIN:
        return None
    return ranked[0][0]
