"""Page content object domain model and mapper."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class PageContentObject:
    id: str | None
    page_id: str
    kind: str
    parent_object_id: str | None
    container_object_id: str | None
    callback_id: str | None
    media_type: str | None
    can_delete: bool
    delete_target_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def content_objects(page_id: str, objects: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize Page XML parser output to the stable content model."""

    return [
        PageContentObject(
            id=item.get("object_id"),
            page_id=page_id,
            kind=item.get("type", "Unknown"),
            parent_object_id=item.get("parent_object_id"),
            container_object_id=item.get("container_object_id"),
            callback_id=item.get("callback_id"),
            media_type=item.get("format"),
            can_delete=bool(item.get("delete_supported")),
            delete_target_id=item.get("delete_object_id"),
        ).as_dict()
        for item in objects
    ]
