"""SectionGroup domain model."""

from __future__ import annotations

from dataclasses import dataclass, field

from .resource import Resource


@dataclass(frozen=True)
class SectionGroup(Resource):
    notebook_id: str | None = None
    parent_section_group_id: str | None = None
    section_group_ids: list[str] = field(default_factory=list)
    section_ids: list[str] = field(default_factory=list)
