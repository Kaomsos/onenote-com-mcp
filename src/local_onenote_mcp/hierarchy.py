"""Canonical OneNote hierarchy XML parser and typed resource selectors.

This module is deliberately independent from the COM bridge and MCP server.
It accepts XML strings and domain snapshots, and returns only stable fields.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
import xml.etree.ElementTree as ET

from .domain import Notebook, Page, Section, SectionGroup


RESOURCE_TAGS = {
    "Notebook": "notebook",
    "SectionGroup": "section_group",
    "Section": "section",
    "Page": "page",
}
RESOURCE_TYPES = frozenset(RESOURCE_TAGS.values())


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _bool(value: str | None) -> bool:
    return (value or "").casefold() == "true"


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _page_level(value: str | None) -> int:
    """Parse COM pageLevel without turning malformed input into the L1 default."""

    if value is None:
        return 1
    parsed = _int(value)
    return parsed if parsed is not None else 0


def display_name(item: dict[str, Any]) -> str:
    """Return the public display field shared by container and Page models."""

    return item.get("title") or item.get("name") or ""


def parse_hierarchy(
    xml: str,
    *,
    catalog: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Map complete or fragment hierarchy XML to stable typed dictionaries.

    When ``catalog`` is supplied (for example for FindPages XML fragments),
    matching IDs are hydrated from that complete snapshot so paths and typed
    relationships remain authoritative.
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
                    page_level=_page_level(node.attrib.get("pageLevel")),
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
    _complete_relationships(records, children_by_parent)

    if catalog is not None:
        by_id = {item["id"]: item for item in catalog if item.get("id")}
        records = [{**item} if item["id"] not in by_id else {**by_id[item["id"]]} for item in records]
    return records


def _complete_relationships(
    records: list[dict[str, Any]],
    children_by_parent: dict[str, list[dict[str, Any]]],
) -> None:
    by_id = {item["id"]: item for item in records if item["id"]}
    for parent, children in children_by_parent.items():
        item = by_id.get(parent)
        if not item:
            continue
        if item["resource_type"] in {"notebook", "section_group"}:
            item["section_group_ids"] = [child["id"] for child in children if child["resource_type"] == "section_group"]
            item["section_ids"] = [child["id"] for child in children if child["resource_type"] == "section"]
        elif item["resource_type"] == "section":
            item["page_count"] = sum(child["resource_type"] == "page" for child in children)
    _derive_page_tree(records)


def _derive_page_tree(records: list[dict[str, Any]]) -> None:
    pages_by_section: dict[str, list[dict[str, Any]]] = {}
    for item in records:
        if item["resource_type"] == "page":
            pages_by_section.setdefault(item.get("section_id") or "", []).append(item)
    for pages in pages_by_section.values():
        for page, parent in derive_page_relationships(pages):
            page["parent_page_id"] = str(parent["id"]) if parent is not None else None
            if parent is not None:
                parent["has_children"] = True


def derive_page_relationships(
    pages: Iterable[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Return ordered Pages paired with their nearest shallower ancestor."""

    stack: list[dict[str, Any]] = []
    relationships: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for page in sorted(pages, key=lambda value: int(value.get("order") or 0)):
        level = int(page.get("page_level") or 0)
        while stack and int(stack[-1].get("page_level") or 0) >= level:
            stack.pop()
        relationships.append((page, stack[-1] if stack else None))
        stack.append(page)
    return relationships


def filter_resources(items: Iterable[dict[str, Any]], resource_type: str) -> list[dict[str, Any]]:
    return [item for item in items if item.get("resource_type") == resource_type]


def find_resource_by_id(
    items: Iterable[dict[str, Any]],
    object_id: str,
    resource_type: str | None = None,
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in items
            if item.get("id") == object_id
            and (resource_type is None or item.get("resource_type") == resource_type)
        ),
        None,
    )


def find_resource_by_path(
    items: Iterable[dict[str, Any]],
    path: str,
    resource_type: str | None = None,
) -> dict[str, Any] | None:
    target = path.casefold()
    return next(
        (
            item
            for item in items
            if item.get("path", "").casefold() == target
            and (resource_type is None or item.get("resource_type") == resource_type)
        ),
        None,
    )


def find_resources_by_path(
    items: Iterable[dict[str, Any]],
    path: str,
    resource_type: str | None = None,
) -> list[dict[str, Any]]:
    """Return every typed exact-path match without choosing an occurrence."""

    target = path.casefold()
    return [
        item
        for item in items
        if item.get("path", "").casefold() == target
        and (resource_type is None or item.get("resource_type") == resource_type)
    ]


def find_unique_resource_by_path(
    items: Iterable[dict[str, Any]],
    path: str,
    resource_type: str | None = None,
) -> dict[str, Any] | None:
    """Return one exact-path match, fail closed when the path is ambiguous."""

    matches = find_resources_by_path(items, path, resource_type)
    if len(matches) > 1:
        label = resource_type or "object"
        ids = ", ".join(str(item.get("id", "")) for item in matches[:10])
        raise ValueError(
            f"Ambiguous {label} path '{path}'. Use an exact object ID. Matching IDs: {ids}"
        )
    return matches[0] if matches else None


def resolve_resource(
    items: Iterable[dict[str, Any]],
    identifier: str,
    resource_type: str | None = None,
) -> dict[str, Any]:
    """Resolve ID, then exact path, then unique display name."""

    candidates = [
        item
        for item in items
        if resource_type is None or item.get("resource_type") == resource_type
    ]
    type_label = resource_type or "object"
    by_id = [item for item in candidates if item.get("id") == identifier]
    if len(by_id) == 1:
        return by_id[0]
    lowered = identifier.casefold()
    by_path = [item for item in candidates if item.get("path", "").casefold() == lowered]
    if len(by_path) == 1:
        return by_path[0]
    if len(by_path) > 1:
        paths = ", ".join(item["path"] for item in by_path[:10])
        raise ValueError(f"Ambiguous {type_label} identifier '{identifier}'. Use an ID. Matches: {paths}")
    by_name = [item for item in candidates if display_name(item).casefold() == lowered]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        paths = ", ".join(item["path"] for item in by_name[:10])
        raise ValueError(f"Ambiguous {type_label} identifier '{identifier}'. Use an ID or exact path. Matches: {paths}")
    raise ValueError(f"No {type_label} found for '{identifier}'. Use an ID or exact path from a typed list tool.")
