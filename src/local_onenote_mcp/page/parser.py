"""Parse OneNote Page XML into visible text, title, and content objects."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any


DELETABLE_PAGE_OBJECT_TYPES = {"Outline", "Image", "InkDrawing", "FileAttachment", "InsertedFile", "MediaFile"}


class HTMLTextExtractor(HTMLParser):
    """Extract readable text from a OneNote T element's inline HTML fragment."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._newline()

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts)
        value = value.replace("\x00", "")
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    def _newline(self) -> None:
        if not self.parts or not self.parts[-1].endswith("\n"):
            self.parts.append("\n")


def html_fragment_to_text(fragment: str) -> str:
    parser = HTMLTextExtractor()
    parser.feed(fragment or "")
    parser.close()
    return parser.text()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_xml(xml: str) -> ET.Element:
    return ET.fromstring(xml.encode("utf-8"))


def text_from_page_xml(xml: str) -> str:
    root = parse_xml(xml)
    texts = []
    for node in root.iter():
        if local_name(node.tag) == "T" and node.text:
            texts.append(html_fragment_to_text(node.text))
    return "\n\n".join(text for text in texts if text).strip()


def title_from_page_xml(xml: str) -> str | None:
    root = parse_xml(xml)
    for title in root.iter():
        if local_name(title.tag) != "Title":
            continue
        for node in title.iter():
            if local_name(node.tag) == "T" and node.text:
                value = html_fragment_to_text(node.text)
                if value:
                    return value
    return None


def collect_page_objects(xml: str) -> list[dict[str, Any]]:
    root = parse_xml(xml)
    objects = []
    content_without_own_id = {"Image", "FileAttachment", "InsertedFile", "MediaFile"}

    def walk(
        node: ET.Element,
        container_object_id: str | None = None,
        deletable_container_id: str | None = None,
        in_title: bool = False,
    ) -> None:
        kind = local_name(node.tag)
        next_in_title = in_title or kind == "Title"
        object_id = node.attrib.get("objectID") or node.attrib.get("ID")
        next_container_id = object_id or container_object_id
        delete_supported = kind in DELETABLE_PAGE_OBJECT_TYPES and bool(object_id)
        next_deletable_container_id = object_id if delete_supported else deletable_container_id

        if not next_in_title and kind != "Page" and (object_id or kind in content_without_own_id):
            record: dict[str, Any] = {"type": kind}
            if object_id:
                record["object_id"] = object_id
            elif container_object_id:
                record["container_object_id"] = container_object_id
            if container_object_id and object_id != container_object_id:
                record["parent_object_id"] = container_object_id
            record["delete_supported"] = delete_supported
            if delete_supported and object_id:
                record["delete_object_id"] = object_id
            elif deletable_container_id:
                record["delete_object_id"] = deletable_container_id
            if "callbackID" in node.attrib:
                record["callback_id"] = node.attrib["callbackID"]
            if "format" in node.attrib:
                record["format"] = node.attrib["format"]
            objects.append(record)

        for child in list(node):
            walk(child, next_container_id, next_deletable_container_id, next_in_title)

    walk(root)
    return objects
