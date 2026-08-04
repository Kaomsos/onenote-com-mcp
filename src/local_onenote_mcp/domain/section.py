"""Section domain model."""

from __future__ import annotations

from dataclasses import dataclass

from .resource import Resource


@dataclass(frozen=True)
class Section(Resource):
    notebook_id: str | None = None
    parent_section_group_id: str | None = None
    page_count: int | None = None
    is_locked: bool | None = None
    is_read_only: bool | None = None
