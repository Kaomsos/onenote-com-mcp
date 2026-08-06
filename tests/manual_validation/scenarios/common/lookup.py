"""Exact-name hierarchy lookup helpers shared by fixture builders."""

from __future__ import annotations

from typing import Any, Iterable

from ...runtime import RunnerFailure
from ...test_utils import display_name

def exact_matches(items: Iterable[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    folded = name.casefold()
    return [item for item in items if display_name(item).casefold() == folded]

def exactly_one(items: Iterable[dict[str, Any]], name: str, label: str) -> dict[str, Any] | None:
    matches = exact_matches(items, name)
    if len(matches) > 1:
        paths = ", ".join(str(item.get("path")) for item in matches)
        raise RunnerFailure(f"Duplicate {label} named '{name}': {paths}")
    return matches[0] if matches else None
