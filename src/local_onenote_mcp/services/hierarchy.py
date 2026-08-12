"""Hierarchy snapshot, query, relationship, and update-XML service."""

from __future__ import annotations

import time
from typing import Any, Callable
import xml.etree.ElementTree as ET

from ..bridge import OneNoteBridge
from ..constants import HIERARCHY_SCOPES, ONE_NS, XML_SCHEMA_2013
from ..hierarchy import (
    display_name,
    filter_resources,
    find_resource_by_id,
    find_resource_by_path,
    find_resources_by_path,
    find_unique_resource_by_path,
    parse_hierarchy,
    resolve_resource,
)
from .base import BaseService


IDENTIFIER_RESOLUTION_ORDER = ["id", "exact_path", "unique_name"]
RESOURCE_TYPES = {"notebook", "section_group", "section", "page"}


class HierarchyService(BaseService):
    def __init__(self, bridge: OneNoteBridge) -> None:
        super().__init__(bridge)
        ET.register_namespace("one", ONE_NS)

    def hierarchy_xml(self, start_id: str = "", scope: str = "pages") -> str:
        return self.call(
            "get_hierarchy",
            start_id=start_id,
            scope=self.enum("scope", scope, HIERARCHY_SCOPES),
            schema=XML_SCHEMA_2013,
        )["xml"]

    def resources(self, include_recycle_bin: bool = False) -> list[dict[str, Any]]:
        items = parse_hierarchy(self.hierarchy_xml("", "pages"))
        return items if include_recycle_bin else self.without_recycle_bin(items)

    def resource(self, object_id: str, resource_type: str | None = None) -> dict[str, Any]:
        if not object_id:
            raise ValueError("An object ID is required.")
        item = find_resource_by_id(self.resources(include_recycle_bin=True), object_id, resource_type)
        if item is None:
            label = resource_type or "object"
            raise ValueError(f"No {label} found for ID '{object_id}'.")
        return item

    def resolve(self, identifier: str, resource_type: str | None = None) -> dict[str, Any]:
        return resolve_resource(self.resources(include_recycle_bin=True), identifier, resource_type)

    def find_path(self, path: str, resource_type: str | None = None) -> dict[str, Any] | None:
        """Compatibility selector for read-only callers; returns the first exact match."""

        return find_resource_by_path(self.resources(include_recycle_bin=True), path, resource_type)

    def find_unique_path(
        self,
        path: str,
        resource_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Return a unique exact-path match and reject duplicate friendly paths."""

        return find_unique_resource_by_path(
            self.resources(include_recycle_bin=True), path, resource_type
        )

    def wait_for(
        self,
        object_id: str,
        resource_type: str,
        *,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        retries: int = 8,
        delay_seconds: float = 0.5,
    ) -> dict[str, Any] | None:
        for attempt in range(retries):
            try:
                item = self.resource(object_id, resource_type)
            except ValueError:
                item = None
            if item is not None and (predicate is None or predicate(item)):
                return item
            if attempt + 1 < retries:
                time.sleep(delay_seconds)
        return None

    def wait_for_created(
        self,
        expected_path: str,
        resource_type: str,
        allocated_id: str,
        *,
        expected_parent_id: str | None = None,
        validate_parent: bool = False,
        before_ids: set[str] | None = None,
        retries: int = 8,
        delay_seconds: float = 0.5,
    ) -> dict[str, Any] | None:
        """Verify a created target by allocated ID, or by one fresh path remap.

        The COM-returned ID is authoritative when it resolves to the expected active
        type/path/parent.  A path fallback is accepted only when the allocated ID is
        absent and exactly one eligible candidate exists; public Create callers pass
        ``before_ids`` so that fallback also proves the candidate is newly observed.
        """

        for attempt in range(retries):
            resources = self.resources(include_recycle_bin=True)
            allocated = find_resource_by_id(resources, allocated_id)

            def eligible(candidate: dict[str, Any]) -> bool:
                return (
                    candidate.get("resource_type") == resource_type
                    and candidate.get("path", "").casefold() == expected_path.casefold()
                    and candidate.get("is_in_recycle_bin") is not True
                    and (
                        not validate_parent
                        or candidate.get("parent_id") == expected_parent_id
                    )
                )

            if allocated is not None:
                if eligible(allocated) and (
                    before_ids is None or allocated_id not in before_ids
                ):
                    return allocated
                # A visible allocated ID with the wrong type/path/parent/state is
                # not evidence for remapping another same-path object.
            else:
                path_matches = [
                    candidate
                    for candidate in find_resources_by_path(
                        resources, expected_path, resource_type
                    )
                    if eligible(candidate)
                    and (before_ids is None or candidate.get("id") not in before_ids)
                ]
                if len(path_matches) == 1:
                    return path_matches[0]
            if attempt + 1 < retries:
                time.sleep(delay_seconds)
        return None

    @staticmethod
    def friendly_child_path(parent_path: str, child_name: str) -> str:
        normalized = child_name.replace("\\", "/").strip("/")
        if normalized.lower().endswith(".one"):
            normalized = normalized[:-4]
        return f"{parent_path}/{normalized}" if normalized else parent_path

    @staticmethod
    def without_recycle_bin(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [item for item in items if item.get("is_in_recycle_bin") is not True]

    def list_hierarchy(
        self,
        start_identifier: str = "",
        scope: str = "pages",
        include_xml: bool = False,
        include_recycle_bin: bool = False,
    ) -> dict[str, Any]:
        xml = self.hierarchy_xml("", "pages")
        items = parse_hierarchy(xml)
        if not include_recycle_bin:
            items = self.without_recycle_bin(items)
        root = None
        if start_identifier:
            root = resolve_resource(items, start_identifier)
            items = [
                item
                for item in items
                if item["id"] == root["id"] or item["path"].startswith(root["path"] + "/")
            ]
        scope_types = {
            "self": set(),
            "children": RESOURCE_TYPES,
            "notebooks": {"notebook"},
            "sections": {"notebook", "section_group", "section"},
            "pages": RESOURCE_TYPES,
        }
        if scope not in scope_types:
            self.enum("scope", scope, HIERARCHY_SCOPES)
        if scope == "self":
            items = [root] if root else []
        elif scope == "children":
            parent_id = root["id"] if root else None
            items = [item for item in items if item["parent_id"] == parent_id]
        else:
            items = [item for item in items if item["resource_type"] in scope_types[scope]]
        result: dict[str, Any] = {"items": items, "count": len(items)}
        if include_xml:
            result["xml"] = xml
        return result

    def list_notebooks(self, include_recycle_bin: bool = False) -> dict[str, Any]:
        notebooks = filter_resources(self.resources(include_recycle_bin), "notebook")
        return {"notebooks": notebooks, "count": len(notebooks)}

    def list_section_groups(
        self,
        parent_id: str = "",
        recursive: bool = True,
        include_recycle_bin: bool = False,
    ) -> dict[str, Any]:
        items = self.resources(include_recycle_bin)
        groups = filter_resources(items, "section_group")
        if parent_id:
            parent = find_resource_by_id(items, parent_id)
            if not parent or parent["resource_type"] not in {"notebook", "section_group"}:
                raise ValueError("parent_id must identify a notebook or section_group.")
            if recursive:
                prefix = parent["path"] + "/"
                groups = [item for item in groups if item["path"].startswith(prefix)]
            else:
                groups = [item for item in groups if item["parent_id"] == parent_id]
        return {"items": groups, "count": len(groups)}

    def list_sections(
        self,
        parent_id: str = "",
        recursive: bool = True,
        include_recycle_bin: bool = False,
    ) -> dict[str, Any]:
        items = self.resources(include_recycle_bin)
        sections = filter_resources(items, "section")
        if parent_id:
            parent = find_resource_by_id(items, parent_id)
            if not parent or parent["resource_type"] not in {"notebook", "section_group"}:
                raise ValueError("parent_id must identify a notebook or section_group.")
            if recursive:
                prefix = parent["path"] + "/"
                sections = [item for item in sections if item["path"].startswith(prefix)]
            else:
                sections = [item for item in sections if item["parent_id"] == parent_id]
        return {"sections": sections, "count": len(sections)}

    def list_pages(self, section_id: str, include_recycle_bin: bool = False) -> dict[str, Any]:
        section = self.resource(section_id, "section")
        pages = [
            item
            for item in self.resources(include_recycle_bin)
            if item["resource_type"] == "page" and item["section_id"] == section_id
        ]
        return {"section": section, "pages": pages, "count": len(pages)}

    def query(
        self,
        resource_type: str,
        name_equals: str = "",
        name_contains: str = "",
        parent_id: str = "",
        modified_after: str = "",
        modified_before: str = "",
        include_recycle_bin: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        normalized_type = resource_type.strip().casefold()
        if normalized_type not in RESOURCE_TYPES:
            raise ValueError("resource_type must be one of: notebook, section_group, section, page.")
        items = filter_resources(self.resources(include_recycle_bin), normalized_type)
        if name_equals:
            target = name_equals.casefold()
            items = [item for item in items if display_name(item).casefold() == target]
        if name_contains:
            target = name_contains.casefold()
            items = [item for item in items if target in display_name(item).casefold()]
        if parent_id:
            if normalized_type == "page":
                items = [
                    item
                    for item in items
                    if item["section_id"] == parent_id or item["parent_page_id"] == parent_id
                ]
            else:
                items = [item for item in items if item["parent_id"] == parent_id]
        if modified_after:
            items = [item for item in items if item.get("modified") and item["modified"] > modified_after]
        if modified_before:
            items = [item for item in items if item.get("modified") and item["modified"] < modified_before]
        bounded = items[: max(1, min(limit, 1000))]
        return {
            "items": bounded,
            "count": len(bounded),
            "total_matches": len(items),
            "truncated": len(bounded) < len(items),
        }

    def path(self, object_id: str) -> dict[str, Any]:
        items = self.resources(include_recycle_bin=True)
        by_id = {item["id"]: item for item in items}
        item = by_id.get(object_id)
        if item is None:
            raise ValueError(f"No object found for ID '{object_id}'.")
        ancestors = []
        parent_id = item.get("parent_id")
        while parent_id:
            parent = by_id.get(parent_id)
            if parent is None:
                break
            ancestors.append(parent)
            parent_id = parent.get("parent_id")
        ancestors.reverse()
        return {"item": item, "path": item["path"], "ancestors": ancestors}

    def tree(self, root_id: str, max_depth: int = 8, include_recycle_bin: bool = False) -> dict[str, Any]:
        items = self.resources(include_recycle_bin)
        by_id = {item["id"]: item for item in items}
        root = by_id.get(root_id)
        if root is None:
            raise ValueError(f"No object found for ID '{root_id}'.")
        children: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            if item["id"] == root_id:
                continue
            parent = item.get("parent_page_id") if item["resource_type"] == "page" else item.get("parent_id")
            if item["resource_type"] == "page" and parent is None:
                parent = item.get("section_id")
            if parent:
                children.setdefault(parent, []).append(item)

        def build(item: dict[str, Any], depth: int) -> dict[str, Any]:
            node = {"item": item, "children": []}
            if depth < max(0, max_depth):
                node["children"] = [build(child, depth + 1) for child in children.get(item["id"], [])]
            return node

        return {"tree": build(root, 0)}

    def update_xml(
        self,
        item: dict[str, Any],
        *,
        catalog: list[dict[str, Any]] | None = None,
        **attributes: str,
    ) -> str:
        all_items = catalog if catalog is not None else self.resources(include_recycle_bin=True)
        by_id = {candidate["id"]: candidate for candidate in all_items}
        chain = [item]
        parent_id = item.get("parent_id")
        while parent_id:
            parent = by_id.get(parent_id)
            if parent is None:
                raise RuntimeError(f"Cannot build hierarchy update: missing ancestor {parent_id}.")
            chain.append(parent)
            parent_id = parent.get("parent_id")
        chain.reverse()
        root = ET.Element(f"{{{ONE_NS}}}Notebooks")
        current = root
        tags = {"notebook": "Notebook", "section_group": "SectionGroup", "section": "Section", "page": "Page"}
        for candidate in chain:
            attrs = {"ID": candidate["id"], "name": display_name(candidate)}
            if candidate is item:
                attrs.update(attributes)
            current = ET.SubElement(current, f"{{{ONE_NS}}}{tags[candidate['resource_type']]}", attrs)
        return ET.tostring(root, encoding="unicode")

    def reparent_xml(
        self,
        target: dict[str, Any],
        destination: dict[str, Any],
        *,
        catalog: list[dict[str, Any]],
    ) -> str:
        """Build an ancestor-complete typed reparent update for one active target."""

        target_type = target.get("resource_type")
        allowed_destinations = {
            "page": {"section"},
            "section": {"notebook", "section_group"},
            "section_group": {"notebook", "section_group"},
        }
        if target_type not in allowed_destinations:
            raise ValueError("Reparent target must be a page, section, or section_group.")
        if destination.get("resource_type") not in allowed_destinations[target_type]:
            raise ValueError(
                f"Invalid destination type for {target_type} reparent: "
                f"{destination.get('resource_type')}."
            )
        all_items = catalog
        by_id = {candidate["id"]: candidate for candidate in all_items}
        chain = [destination]
        parent_id = destination.get("parent_id")
        while parent_id:
            parent = by_id.get(parent_id)
            if parent is None:
                raise RuntimeError(f"Cannot build reparent update: missing ancestor {parent_id}.")
            chain.append(parent)
            parent_id = parent.get("parent_id")
        chain.reverse()
        root = ET.Element(f"{{{ONE_NS}}}Notebooks")
        current = root
        tags = {
            "notebook": "Notebook",
            "section_group": "SectionGroup",
            "section": "Section",
        }
        for candidate in chain:
            current = ET.SubElement(
                current,
                f"{{{ONE_NS}}}{tags[candidate['resource_type']]}",
                {"ID": candidate["id"], "name": display_name(candidate)},
            )
        target_attributes = {"ID": target["id"], "name": display_name(target)}
        if target_type == "page":
            target_attributes["pageLevel"] = str(max(1, int(target.get("page_level") or 1)))
        target_tags = {
            "page": "Page",
            "section": "Section",
            "section_group": "SectionGroup",
        }
        ET.SubElement(
            current,
            f"{{{ONE_NS}}}{target_tags[target_type]}",
            target_attributes,
        )
        return ET.tostring(root, encoding="unicode")

    def reparent_page_scope_xml(
        self,
        pages: list[dict[str, Any]],
        destination: dict[str, Any],
        *,
        catalog: list[dict[str, Any]],
    ) -> str:
        """Build one typed Page-scope reparent update normalized below a Section."""

        if not pages or any(page.get("resource_type") != "page" for page in pages):
            raise ValueError("Page reparent scope must contain one or more Pages.")
        if destination.get("resource_type") != "section":
            raise ValueError("Page reparent destination must be a Section.")
        source_section_ids = {page.get("section_id") for page in pages}
        if len(source_section_ids) != 1 or None in source_section_ids:
            raise ValueError("Page reparent scope must come from one Section.")

        by_id = {candidate["id"]: candidate for candidate in catalog}
        chain = [destination]
        parent_id = destination.get("parent_id")
        while parent_id:
            parent = by_id.get(parent_id)
            if parent is None:
                raise RuntimeError(f"Cannot build reparent update: missing ancestor {parent_id}.")
            chain.append(parent)
            parent_id = parent.get("parent_id")
        chain.reverse()

        root = ET.Element(f"{{{ONE_NS}}}Notebooks")
        current = root
        tags = {
            "notebook": "Notebook",
            "section_group": "SectionGroup",
            "section": "Section",
        }
        for candidate in chain:
            current = ET.SubElement(
                current,
                f"{{{ONE_NS}}}{tags[candidate['resource_type']]}",
                {"ID": candidate["id"], "name": display_name(candidate)},
            )

        root_level = int(pages[0].get("page_level") or 1)
        for page in pages:
            normalized_level = int(page.get("page_level") or 1) - root_level + 1
            if normalized_level < 1:
                raise ValueError("Page reparent scope has invalid relative indentation.")
            ET.SubElement(
                current,
                f"{{{ONE_NS}}}Page",
                {
                    "ID": page["id"],
                    "name": display_name(page),
                    "pageLevel": str(normalized_level),
                },
            )
        return ET.tostring(root, encoding="unicode")

    def page_order_xml(self, section: dict[str, Any], pages: list[dict[str, Any]]) -> str:
        root = ET.fromstring(self.update_xml(section))
        section_node = next(node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Section")
        for page in pages:
            ET.SubElement(
                section_node,
                f"{{{ONE_NS}}}Page",
                {"ID": page["id"], "name": display_name(page), "pageLevel": str(page["page_level"])},
            )
        return ET.tostring(root, encoding="unicode")

    def container_order_xml(
        self,
        parent: dict[str, Any],
        ordered_children: list[dict[str, Any]],
        *,
        catalog: list[dict[str, Any]],
    ) -> str:
        """Build an ancestor-complete update containing every direct container child."""

        if parent["resource_type"] not in {"notebook", "section_group"}:
            raise ValueError("Container reorder parent must be a notebook or section_group.")
        if any(
            child.get("parent_id") != parent["id"]
            or child.get("resource_type") not in {"section_group", "section"}
            for child in ordered_children
        ):
            raise ValueError("Container reorder children must be direct section/section_group siblings.")
        child_ids = [child["id"] for child in ordered_children]
        if len(child_ids) != len(set(child_ids)):
            raise ValueError("Container reorder children must have unique IDs.")

        root = ET.fromstring(self.update_xml(parent, catalog=catalog))
        parent_node = next(
            node
            for node in root.iter()
            if node.attrib.get("ID") == parent["id"]
        )
        tags = {"section_group": "SectionGroup", "section": "Section"}
        for child in ordered_children:
            ET.SubElement(
                parent_node,
                f"{{{ONE_NS}}}{tags[child['resource_type']]}",
                {"ID": child["id"], "name": display_name(child)},
            )
        return ET.tostring(root, encoding="unicode")
