"""Safe Page XML transformation and comparison for experimental Copy tools."""

from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any, Iterable
from urllib.parse import quote
import xml.etree.ElementTree as ET

from ..constants import ONE_NS
from .parser import collect_page_objects, local_name, parse_xml, text_from_page_xml


ET.register_namespace("one", ONE_NS)

# These nodes can be submitted to UpdatePageContent as a new-page payload. A
# content type is not considered lossless until its isolated real-backend
# scenario has been confirmed and it is deliberately added below.
COPYABLE_CONTENT_ROOTS = {
    "Outline",
    "Image",
    "InkDrawing",
    "FileAttachment",
    "InsertedFile",
    "MediaFile",
}
SUPPORTING_ROOTS = {"Title", "PageSettings", "QuickStyleDef", "TagDef"}
VALIDATED_COPY_CONTENT_TYPES: frozenset[str] = frozenset()

# OneNote's COM API does not expose a runtime schema-introspection API.  Keep
# this list deliberately conservative: a future/extension element must not be
# passed through merely because its top-level Outline/Image container is known.
# The names below cover the OneNote 2013 Page structures handled by this
# module (text/list/table/layout/tag/metadata and binary content payloads).
KNOWN_PAGE_XML_NODES = {
    "Title",
    "PageSettings",
    "PageSize",
    "Automatic",
    "RuleLines",
    "QuickStyleDef",
    "TagDef",
    "Outline",
    "Position",
    "Size",
    "OEChildren",
    "OE",
    "List",
    "Bullet",
    "Number",
    "T",
    "Table",
    "Columns",
    "Column",
    "Row",
    "Cell",
    "Image",
    "Data",
    "InkDrawing",
    "Ink",
    "FileAttachment",
    "InsertedFile",
    "MediaFile",
    "MediaPlaylist",
    "Tag",
    "Meta",
    "MeetingInfo",
    "MeetingInfoItem",
}

GENERATED_OBJECT_ATTRIBUTES = {"objectID", "callbackID"}
VOLATILE_ATTRIBUTES = {
    "dateTime",
    "lastModifiedTime",
    "isCurrentlyViewed",
    "selected",
    "isSelected",
    "path",
    "pathCache",
    "sourcePath",
    "localFilePath",
}
IGNORED_ATTRIBUTES = GENERATED_OBJECT_ATTRIBUTES | VOLATILE_ATTRIBUTES
ROOT_REGENERATED_ATTRIBUTES = {"ID", "name", "pageLevel"}
LINK_ATTRIBUTE_NAMES = {
    "href",
    "hyperlink",
    "link",
    "linkednoteuri",
    "linkedpageid",
    "linkedsectionid",
    "notebookid",
    "pageid",
    "sectionid",
    "sourceurl",
    "targetid",
    "uri",
    "url",
}


def _replace_ids(value: str | None, id_map: dict[str, str]) -> str | None:
    if value is None:
        return None
    result = value
    for old_id, new_id in id_map.items():
        variants = (
            (old_id, new_id),
            (quote(old_id, safe=""), quote(new_id, safe="")),
            (quote(old_id, safe="{}"), quote(new_id, safe="{}")),
        )
        for old_value, new_value in variants:
            result = re.sub(re.escape(old_value), lambda _match: new_value, result, flags=re.IGNORECASE)
    return result


def _is_link_attribute(key: str, value: str) -> bool:
    name = key.rsplit("}", 1)[-1].casefold()
    lowered = value.casefold()
    return (
        name in LINK_ATTRIBUTE_NAMES
        or "onenote:" in lowered
        or "href=" in lowered
    )


def _strip_identity_and_rewrite(node: ET.Element, id_map: dict[str, str]) -> None:
    for key in list(node.attrib):
        if key in GENERATED_OBJECT_ATTRIBUTES or key in VOLATILE_ATTRIBUTES:
            node.attrib.pop(key, None)
            continue
        if _is_link_attribute(key, node.attrib[key]):
            node.attrib[key] = _replace_ids(node.attrib[key], id_map) or ""
    if node.text and ("onenote:" in node.text.casefold() or "href=" in node.text.casefold()):
        node.text = _replace_ids(node.text, id_map)
    for child in list(node):
        _strip_identity_and_rewrite(child, id_map)


def _link_payload(root: ET.Element) -> str:
    values: list[str] = []
    for node in root.iter():
        values.extend(
            value
            for key, value in node.attrib.items()
            if _is_link_attribute(key, value)
        )
        if node.text and ("onenote:" in node.text.casefold() or "href=" in node.text.casefold()):
            values.append(node.text)
    return "\n".join(values)


def _set_title(root: ET.Element, title: str) -> None:
    for title_node in root.iter():
        if local_name(title_node.tag) != "Title":
            continue
        for text_node in title_node.iter():
            if local_name(text_node.tag) == "T":
                text_node.text = title
                return
    title_node = ET.Element(f"{{{ONE_NS}}}Title")
    oe_node = ET.SubElement(title_node, f"{{{ONE_NS}}}OE")
    text_node = ET.SubElement(oe_node, f"{{{ONE_NS}}}T")
    text_node.text = title
    insert_at = 1 if list(root) and local_name(list(root)[0].tag) == "PageSettings" else 0
    root.insert(insert_at, title_node)


def _unknown_page_nodes(root: ET.Element) -> list[str]:
    unknown: set[str] = set()
    for node in root.iter():
        tag = node.tag
        namespace = tag[1:].split("}", 1)[0] if tag.startswith("{") and "}" in tag else ""
        kind = local_name(tag)
        if namespace != ONE_NS or kind not in KNOWN_PAGE_XML_NODES:
            unknown.add(f"{{{namespace}}}{kind}" if namespace else kind)
    return sorted(unknown)


def _content_capabilities(
    root: ET.Element,
    source_objects: list[dict[str, Any]],
) -> list[str]:
    capabilities = {
        str(item.get("type"))
        for item in source_objects
        if item.get("type") in COPYABLE_CONTENT_ROOTS
    }
    for node in root.iter():
        kind = local_name(node.tag)
        if kind in COPYABLE_CONTENT_ROOTS:
            capabilities.add(kind)
        if kind == "Table":
            capabilities.add("Table")
        elif kind == "List":
            capabilities.add("List")
        elif kind == "Tag":
            capabilities.add("Tag")
        elif kind in {"MeetingInfo", "MeetingInfoItem"}:
            capabilities.add("MeetingInfo")
        elif kind == "T" and node.text and re.search(
            r"</?(?:a|b|strong|i|em|u|span|font|sup|sub)\b",
            node.text,
            flags=re.IGNORECASE,
        ):
            capabilities.add("RichText")
    return sorted(capabilities)


def transform_page_for_copy(
    source_xml: str,
    target_page_id: str,
    id_map: dict[str, str],
    *,
    title: str | None = None,
    validated_content_types: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a best-effort new-page payload and a structured fidelity report."""

    source = parse_xml(source_xml)
    target_attributes = {"ID": target_page_id}
    for key, value in source.attrib.items():
        if (
            key in ROOT_REGENERATED_ATTRIBUTES
            or key in GENERATED_OBJECT_ATTRIBUTES
            or key in VOLATILE_ATTRIBUTES
        ):
            continue
        target_attributes[key] = (
            _replace_ids(value, id_map) or ""
            if _is_link_attribute(key, value)
            else value
        )
    target = ET.Element(f"{{{ONE_NS}}}Page", target_attributes)
    issues: list[dict[str, Any]] = []
    copied_roots: list[str] = []
    skipped_roots: list[str] = []
    validated = set(
        VALIDATED_COPY_CONTENT_TYPES if validated_content_types is None else validated_content_types
    )

    for child in list(source):
        kind = local_name(child.tag)
        if kind not in SUPPORTING_ROOTS and kind not in COPYABLE_CONTENT_ROOTS:
            skipped_roots.append(kind)
            issues.append(
                {
                    "code": "unsupported_page_root",
                    "content_type": kind,
                    "action": "omitted",
                    "reason": "The top-level Page XML node is not in the experimental Copy allowlist.",
                }
            )
            continue
        unknown_nodes = _unknown_page_nodes(child)
        if unknown_nodes:
            skipped_roots.append(kind)
            issues.append(
                {
                    "code": "unsupported_nested_page_node",
                    "content_type": kind,
                    "unknown_nodes": unknown_nodes,
                    "action": "omitted",
                    "reason": (
                        "The top-level content block contains Page XML nodes outside the "
                        "experimental Copy allowlist."
                    ),
                }
            )
            continue
        clone = deepcopy(child)
        _strip_identity_and_rewrite(clone, id_map)
        target.append(clone)
        copied_roots.append(kind)

    if title is not None:
        _set_title(target, title)

    source_objects = collect_page_objects(source_xml)
    object_types = _content_capabilities(source, source_objects)
    for kind in object_types:
        if kind not in validated:
            issues.append(
                {
                    "code": "content_type_unverified",
                    "content_type": kind,
                    "action": "preserved_unverified",
                    "reason": "Real isolated OneNote Copy validation has not been confirmed for this type.",
                }
            )

    xml = ET.tostring(target, encoding="unicode")
    link_payload = _link_payload(target)
    linked_object_ids = sorted(
        {
            str(item["object_id"])
            for item in source_objects
            if item.get("object_id")
            and any(
                re.search(re.escape(variant), link_payload, flags=re.IGNORECASE)
                for variant in (
                    str(item["object_id"]),
                    quote(str(item["object_id"]), safe=""),
                    quote(str(item["object_id"]), safe="{}"),
                )
            )
        }
    )
    for object_id in linked_object_ids:
        issues.append(
            {
                "code": "content_object_link_not_rewritable",
                "source_object_id": object_id,
                "action": "preserved_unverified",
                "reason": "A reference to a regenerated Page content object ID could not be rewritten safely.",
            }
        )
    unresolved_internal_ids = sorted(
        old_id
        for old_id in id_map
        if any(
            re.search(re.escape(variant), link_payload, flags=re.IGNORECASE)
            for variant in (old_id, quote(old_id, safe=""), quote(old_id, safe="{}"))
        )
    )
    for old_id in unresolved_internal_ids:
        issues.append(
            {
                "code": "internal_link_not_rewritten",
                "source_id": old_id,
                "action": "preserved_unverified",
                "reason": "A copied-scope source ID remained in the transformed Page XML.",
            }
        )
    return {
        "xml": xml,
        "copied_roots": copied_roots,
        "skipped_roots": skipped_roots,
        "content_types": object_types,
        "issues": issues,
        "lossless_candidate": not issues,
    }


def _canonical_node(node: ET.Element, *, is_root: bool = False) -> list[Any]:
    attributes = sorted(
        (key, value)
        for key, value in node.attrib.items()
        if key not in IGNORED_ATTRIBUTES
        and not (is_root and key in ROOT_REGENERATED_ATTRIBUTES)
    )
    text = (node.text or "").strip()
    if local_name(node.tag) == "Data":
        text = "".join(text.split())
    return [
        local_name(node.tag),
        attributes,
        text,
        [_canonical_node(child) for child in list(node)],
    ]


def canonical_page_digest(xml: str) -> str:
    """Hash content while ignoring IDs, clocks, selection, and view state."""

    payload = json.dumps(
        _canonical_node(parse_xml(xml), is_root=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def page_binary_hashes(xml: str) -> list[str]:
    hashes = []
    for node in parse_xml(xml).iter():
        if local_name(node.tag) == "Data" and node.text:
            encoded = "".join(node.text.split())
            try:
                payload = base64.b64decode(encoded, validate=True)
            except (ValueError, base64.binascii.Error):
                payload = encoded.encode("ascii", errors="ignore")
            hashes.append(sha256(payload).hexdigest())
    return hashes


def page_content_type_counts(xml: str) -> dict[str, int]:
    counts = {kind: 0 for kind in COPYABLE_CONTENT_ROOTS}
    for node in parse_xml(xml).iter():
        kind = local_name(node.tag)
        if kind in counts:
            counts[kind] += 1
    return {kind: count for kind, count in sorted(counts.items()) if count}


def page_equivalence(expected_xml: str, actual_xml: str) -> dict[str, Any]:
    """Return the stable content checks used by Copy and reconstructive Move."""

    checks = {
        "canonical_xml": canonical_page_digest(expected_xml) == canonical_page_digest(actual_xml),
        "visible_text": text_from_page_xml(expected_xml) == text_from_page_xml(actual_xml),
        "content_objects": page_content_type_counts(expected_xml)
        == page_content_type_counts(actual_xml),
        "binary_sha256": page_binary_hashes(expected_xml) == page_binary_hashes(actual_xml),
    }
    return {"equivalent": all(checks.values()), "checks": checks}
