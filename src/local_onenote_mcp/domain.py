"""Stable OneNote domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


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


@dataclass(frozen=True)
class Notebook(Resource):
    section_group_ids: list[str] = field(default_factory=list)
    section_ids: list[str] = field(default_factory=list)
    is_open: bool | None = None


@dataclass(frozen=True)
class SectionGroup(Resource):
    notebook_id: str | None = None
    parent_section_group_id: str | None = None
    section_group_ids: list[str] = field(default_factory=list)
    section_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Section(Resource):
    notebook_id: str | None = None
    parent_section_group_id: str | None = None
    page_count: int | None = None
    is_locked: bool | None = None
    is_read_only: bool | None = None


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

    result = []
    for item in objects:
        result.append(
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
        )
    return result
