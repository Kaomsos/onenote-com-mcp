"""Notebook domain model."""

from __future__ import annotations

from dataclasses import dataclass, field

from .resource import Resource


@dataclass(frozen=True)
class Notebook(Resource):
    section_group_ids: list[str] = field(default_factory=list)
    section_ids: list[str] = field(default_factory=list)
    is_open: bool | None = None
