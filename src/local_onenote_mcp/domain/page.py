"""Page hierarchy metadata model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .resource import Resource


@dataclass(frozen=True)
class Page(Resource):
    title: str = ""
    notebook_id: str | None = None
    section_id: str | None = None
    page_level: int = 1
    order: int = 0
    parent_page_id: str | None = None
    has_children: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("name", None)
        return data
