"""Page metadata/content read service and mutation verification helpers."""

from __future__ import annotations

import hashlib
from typing import Any
import xml.etree.ElementTree as ET

from ..bridge import OneNoteBridge
from ..constants import PAGE_INFO, XML_SCHEMA_2013
from ..domain import content_objects
from ..page import (
    canonical_page_digest,
    collect_page_objects,
    text_from_page_xml,
    title_from_page_xml,
)
from ..page.copying import is_empty_selection_text_node
from .base import BaseService
from .hierarchy import HierarchyService


VOLATILE_PAGE_ATTRIBUTES = {
    "author",
    "authorInitials",
    "authorResolutionID",
    "creationTime",
    "dateTime",
    "lastModifiedTime",
    "lastModifiedBy",
    "lastModifiedByInitials",
    "lastModifiedByResolutionID",
    "isCurrentlyViewed",
    "selected",
    "isSelected",
    "path",
    "pathCache",
    "sourcePath",
    "pathSource",
    "localFilePath",
}
ROOT_HIERARCHY_ATTRIBUTES = {"ID", "name", "pageLevel"}
GENERATED_CONTENT_ID_ATTRIBUTES = ("objectID", "callbackID")


def stable_page_content_digest(xml: str) -> str:
    """Hash in-place Page content while ignoring OneNote-owned clocks/view metadata."""

    root = ET.fromstring(xml)
    for parent in root.iter():
        for child in list(parent):
            if is_empty_selection_text_node(child):
                parent.remove(child)
    for node in root.iter():
        for attribute in VOLATILE_PAGE_ATTRIBUTES:
            node.attrib.pop(attribute, None)
    for attribute in ROOT_HIERARCHY_ATTRIBUTES:
        root.attrib.pop(attribute, None)
    return hashlib.sha256(ET.tostring(root, encoding="utf-8")).hexdigest()


def reparent_page_content_digest(xml: str) -> str:
    """Hash rich Page semantics while allowing native ID and Tag-index remapping."""

    root = ET.fromstring(xml)
    tag_definitions: dict[str, tuple[str, str]] = {}
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "TagDef":
            continue
        index = node.attrib.get("index")
        if index is not None:
            tag_definitions[index] = (
                node.attrib.get("type", ""),
                node.attrib.get("symbol", ""),
            )
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "Tag":
            continue
        index = node.attrib.pop("index", "")
        semantic_type, semantic_symbol = tag_definitions.get(index, ("", ""))
        node.attrib["semanticType"] = semantic_type
        node.attrib["semanticSymbol"] = semantic_symbol
    for parent in root.iter():
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1] == "TagDef":
                parent.remove(child)
    return canonical_page_digest(ET.tostring(root, encoding="unicode"))


def observable_content_id_map(before_xml: str, after_xml: str) -> dict[str, str]:
    """Map COM-generated content IDs by their verified structural positions."""

    before_nodes = [
        node
        for node in ET.fromstring(before_xml).iter()
        if node.tag.rsplit("}", 1)[-1] != "TagDef"
    ]
    after_nodes = [
        node
        for node in ET.fromstring(after_xml).iter()
        if node.tag.rsplit("}", 1)[-1] != "TagDef"
    ]
    if len(before_nodes) != len(after_nodes) or any(
        before.tag.rsplit("}", 1)[-1] != after.tag.rsplit("}", 1)[-1]
        for before, after in zip(before_nodes, after_nodes, strict=True)
    ):
        raise RuntimeError("Page reparent changed the content-object structure.")

    id_map: dict[str, str] = {}
    reverse: dict[str, str] = {}
    for before, after in zip(before_nodes, after_nodes, strict=True):
        for attribute in GENERATED_CONTENT_ID_ATTRIBUTES:
            old_id = before.attrib.get(attribute)
            new_id = after.attrib.get(attribute)
            if bool(old_id) != bool(new_id):
                raise RuntimeError("Page reparent changed observable content-object identity coverage.")
            if not old_id or not new_id:
                continue
            if old_id in id_map and id_map[old_id] != new_id:
                raise RuntimeError("Page reparent produced an ambiguous content-object ID mapping.")
            if new_id in reverse and reverse[new_id] != old_id:
                raise RuntimeError("Page reparent produced a non-bijective content-object ID mapping.")
            id_map[old_id] = new_id
            reverse[new_id] = old_id
    return id_map


class PageService(BaseService):
    def __init__(self, bridge: OneNoteBridge, hierarchy: HierarchyService, max_text_chars: int) -> None:
        super().__init__(bridge)
        self.hierarchy = hierarchy
        self.max_text_chars = max_text_chars

    def xml(
        self,
        page_id: str,
        page_info: str = "basic",
        *,
        _timeout_seconds: float | None = None,
    ) -> str:
        return self.call(
            "get_page_content",
            _timeout_seconds=_timeout_seconds,
            page_id=page_id,
            page_info=self.enum("page_info", page_info, PAGE_INFO),
            schema=XML_SCHEMA_2013,
        )["xml"]

    @staticmethod
    def digest(xml: str) -> str:
        return stable_page_content_digest(xml)

    @staticmethod
    def reparent_digest(xml: str) -> str:
        return reparent_page_content_digest(xml)

    @staticmethod
    def observable_id_map(before_xml: str, after_xml: str) -> dict[str, str]:
        return observable_content_id_map(before_xml, after_xml)

    @staticmethod
    def truncate(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n\n[truncated: {len(text) - max_chars} chars omitted]"

    def get(self, page_id: str) -> dict[str, Any]:
        return {"item": self.hierarchy.resource(page_id, "page")}

    def get_xml(self, page_id: str, page_info: str = "basic") -> dict[str, Any]:
        self.hierarchy.resource(page_id, "page")
        return {"xml": self.xml(page_id, page_info)}

    def get_text(self, page_id: str, max_chars: int | None = None) -> dict[str, Any]:
        self.hierarchy.resource(page_id, "page")
        text = text_from_page_xml(self.xml(page_id, "basic"))
        return {"text": self.truncate(text, max_chars or self.max_text_chars), "chars": len(text)}

    def _content_object_snapshot(self, page_id: str) -> list[dict[str, Any]]:
        """Read object metadata and callback IDs without embedding binary payloads."""

        return content_objects(
            page_id,
            collect_page_objects(self.xml(page_id, "file_type")),
        )

    def get_content_objects(self, page_id: str) -> dict[str, Any]:
        self.hierarchy.resource(page_id, "page")
        objects = self._content_object_snapshot(page_id)
        return {"objects": objects, "count": len(objects)}

    def get_content_object_binary(
        self, page_id: str, page_content_object_id: str
    ) -> dict[str, Any]:
        self.hierarchy.resource(page_id, "page")
        objects = self._content_object_snapshot(page_id)
        matched = next(
            (item for item in objects if item["id"] == page_content_object_id),
            None,
        )
        if not matched:
            raise ValueError(
                "page_content_object_id was not found in the current page object snapshot."
            )
        callback_id = matched.get("callback_id")
        if not callback_id:
            raise ValueError(
                "The selected PageContentObject has no callback ID for binary retrieval."
            )
        result = self.call("get_binary_page_content", page_id=page_id, callback_id=callback_id)
        return {"object": matched, "base64": result["base64"]}

    def confirm(
        self,
        page_id: str,
        *,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        item = self.hierarchy.resource(page_id, "page")
        if item["title"] != expected_title:
            raise ValueError(f"Confirmation mismatch: expected title '{expected_title}', found '{item['title']}'.")
        if item["section_id"] != expected_section_id:
            raise ValueError(
                f"Confirmation mismatch: expected section_id '{expected_section_id}', found '{item['section_id']}'."
            )
        if expected_modified is not None and item.get("modified") != expected_modified:
            raise ValueError(
                f"Confirmation mismatch: expected modified '{expected_modified}', found '{item.get('modified')}'."
            )
        return item

    def title(self, page_id: str) -> str | None:
        return title_from_page_xml(self.xml(page_id, "basic"))
