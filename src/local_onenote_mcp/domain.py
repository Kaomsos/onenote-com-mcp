"""Stable OneNote domain models and hierarchy mapping.

The COM hierarchy XML contains version- and installation-specific attributes.
Only fields represented by these models are allowed to cross the MCP boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
import xml.etree.ElementTree as ET

from .constants import ONE_NS


RESOURCE_TAGS = {
    "Notebook": "notebook",
    "SectionGroup": "section_group",
    "Section": "section",
    "Page": "page",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _bool(value: str | None) -> bool:
    return (value or "").casefold() == "true"


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


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


def parse_domain_hierarchy(xml: str) -> list[dict[str, Any]]:
    """Map hierarchy XML to stable typed dictionaries.

    Relationships and page indentation are derived only from the complete XML
    sequence supplied by the caller and are explicitly marked as such.
    """

    root = ET.fromstring(xml)
    records: list[dict[str, Any]] = []
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    page_order_by_section: dict[str, int] = {}

    def walk(
        node: ET.Element,
        *,
        names: list[str],
        parent_id: str | None,
        depth: int,
        notebook_id: str | None,
        section_group_id: str | None,
        section_id: str | None,
    ) -> None:
        local = _local_name(node.tag)
        resource_type = RESOURCE_TAGS.get(local)
        next_names = names
        next_parent_id = parent_id
        next_depth = depth
        next_notebook_id = notebook_id
        next_section_group_id = section_group_id
        next_section_id = section_id

        if resource_type:
            object_id = node.attrib.get("ID", "")
            name = node.attrib.get("name") or node.attrib.get("nickname") or ""
            path = "/".join([*names, name])
            common: dict[str, Any] = {
                "resource_type": resource_type,
                "id": object_id,
                "name": name,
                "path": path,
                "parent_id": parent_id,
                "depth": depth,
                "created": node.attrib.get("dateTime") or node.attrib.get("createdTime"),
                "modified": node.attrib.get("lastModifiedTime"),
                "is_in_recycle_bin": _bool(node.attrib.get("isInRecycleBin"))
                or _bool(node.attrib.get("isRecycleBin"))
                or "OneNote_RecycleBin" in path.split("/"),
                "relationship_source": "com",
            }
            if resource_type == "notebook":
                is_open = not _bool(node.attrib.get("isClosed")) if "isClosed" in node.attrib else None
                item = Notebook(**common, is_open=is_open).as_dict()
                next_notebook_id = object_id
                next_section_group_id = None
                next_section_id = None
            elif resource_type == "section_group":
                item = SectionGroup(
                    **common,
                    notebook_id=notebook_id,
                    parent_section_group_id=section_group_id,
                ).as_dict()
                next_section_group_id = object_id
                next_section_id = None
            elif resource_type == "section":
                item = Section(
                    **common,
                    notebook_id=notebook_id,
                    parent_section_group_id=section_group_id,
                    is_locked=_bool(node.attrib.get("locked")) if "locked" in node.attrib else None,
                    is_read_only=_bool(node.attrib.get("isReadOnly")) if "isReadOnly" in node.attrib else None,
                ).as_dict()
                next_section_id = object_id
            else:
                order = page_order_by_section.get(section_id or "", 0)
                page_order_by_section[section_id or ""] = order + 1
                common["relationship_source"] = "derived"
                item = Page(
                    **common,
                    title=name,
                    notebook_id=notebook_id,
                    section_id=section_id,
                    page_level=max(1, _int(node.attrib.get("pageLevel")) or 1),
                    order=order,
                ).as_dict()
            records.append(item)
            if parent_id:
                children_by_parent.setdefault(parent_id, []).append(item)
            next_names = [*names, name]
            next_parent_id = object_id
            next_depth = depth + 1

        for child in list(node):
            walk(
                child,
                names=next_names,
                parent_id=next_parent_id,
                depth=next_depth,
                notebook_id=next_notebook_id,
                section_group_id=next_section_group_id,
                section_id=next_section_id,
            )

    walk(
        root,
        names=[],
        parent_id=None,
        depth=0,
        notebook_id=None,
        section_group_id=None,
        section_id=None,
    )

    # Complete direct-child expansions and accurate page counts.
    by_id = {item["id"]: item for item in records if item["id"]}
    for parent, children in children_by_parent.items():
        item = by_id.get(parent)
        if not item:
            continue
        if item["resource_type"] in {"notebook", "section_group"}:
            item["section_group_ids"] = [c["id"] for c in children if c["resource_type"] == "section_group"]
            item["section_ids"] = [c["id"] for c in children if c["resource_type"] == "section"]
        elif item["resource_type"] == "section":
            item["page_count"] = sum(c["resource_type"] == "page" for c in children)

    _derive_page_tree(records)
    return records


def _derive_page_tree(records: list[dict[str, Any]]) -> None:
    pages_by_section: dict[str, list[dict[str, Any]]] = {}
    for item in records:
        if item["resource_type"] == "page":
            pages_by_section.setdefault(item.get("section_id") or "", []).append(item)

    for pages in pages_by_section.values():
        stack: list[dict[str, Any]] = []
        for page in sorted(pages, key=lambda value: value["order"]):
            level = page["page_level"]
            while stack and stack[-1]["page_level"] >= level:
                stack.pop()
            page["parent_page_id"] = stack[-1]["id"] if stack else None
            if stack:
                stack[-1]["has_children"] = True
            stack.append(page)


def filter_resources(items: Iterable[dict[str, Any]], resource_type: str) -> list[dict[str, Any]]:
    return [item for item in items if item.get("resource_type") == resource_type]


def content_objects(page_id: str, objects: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize the legacy page parser output to the stable content model."""

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
