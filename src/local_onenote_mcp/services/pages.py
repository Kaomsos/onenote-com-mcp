"""Page metadata/content read service and mutation verification helpers."""

from __future__ import annotations

import hashlib
from typing import Any
import xml.etree.ElementTree as ET

from ..bridge import OneNoteBridge
from ..constants import PAGE_INFO, XML_SCHEMA_2013
from ..domain import content_objects
from ..page import collect_page_objects, text_from_page_xml, title_from_page_xml
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
    "localFilePath",
}
ROOT_HIERARCHY_ATTRIBUTES = {"ID", "name", "pageLevel"}


def stable_page_content_digest(xml: str) -> str:
    """Hash in-place Page content while ignoring OneNote-owned clocks/view metadata."""

    root = ET.fromstring(xml)
    for node in root.iter():
        for attribute in VOLATILE_PAGE_ATTRIBUTES:
            node.attrib.pop(attribute, None)
    for attribute in ROOT_HIERARCHY_ATTRIBUTES:
        root.attrib.pop(attribute, None)
    return hashlib.sha256(ET.tostring(root, encoding="utf-8")).hexdigest()


class PageService(BaseService):
    def __init__(self, bridge: OneNoteBridge, hierarchy: HierarchyService, max_text_chars: int) -> None:
        super().__init__(bridge)
        self.hierarchy = hierarchy
        self.max_text_chars = max_text_chars

    def xml(self, page_id: str, page_info: str = "basic") -> str:
        return self.call(
            "get_page_content",
            page_id=page_id,
            page_info=self.enum("page_info", page_info, PAGE_INFO),
            schema=XML_SCHEMA_2013,
        )["xml"]

    @staticmethod
    def digest(xml: str) -> str:
        return stable_page_content_digest(xml)

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

    def get_objects(self, page_id: str) -> dict[str, Any]:
        self.hierarchy.resource(page_id, "page")
        objects = content_objects(page_id, collect_page_objects(self.xml(page_id, "all")))
        return {"objects": objects, "count": len(objects)}

    def get_binary(self, page_id: str, callback_id: str) -> dict[str, Any]:
        self.hierarchy.resource(page_id, "page")
        objects = content_objects(page_id, collect_page_objects(self.xml(page_id, "all")))
        matched = next((item for item in objects if item["callback_id"] == callback_id), None)
        if not matched:
            raise ValueError("callback_id was not found in the current page object snapshot.")
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
