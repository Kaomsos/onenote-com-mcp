"""Base hierarchy resource model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Resource:
    resource_type: str
    id: str
    name: str
    path: str
    parent_id: str | None
    depth: int
    created: str | None
    modified: str | None
    is_in_recycle_bin: bool
    relationship_source: str = "com"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
