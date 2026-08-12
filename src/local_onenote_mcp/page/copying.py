"""Safe Page XML transformation and comparison for experimental Copy tools."""

from __future__ import annotations

import base64
from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
from urllib.parse import quote
import xml.etree.ElementTree as ET

from ..constants import ONE_NS
from .parser import (
    collect_page_objects,
    html_fragment_to_text,
    local_name,
    parse_xml,
    text_from_page_xml,
)


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
SUPPORTING_ROOTS = {
    "Title",
    "PageSettings",
    "QuickStyleDef",
    "TagDef",
    "MediaPlaylist",
}
VALIDATED_COPY_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "DisplayEquation",
        "Image",
        "InkDrawing",
        "InsertedFile",
        "List",
        "MediaFile",
        "Outline",
        "RichText",
        "Table",
        "Tag",
        "UIShape",
    }
)
STRICT_CANONICAL_VERIFICATION = "strict_canonical"
SEMANTIC_MATHML_VERIFICATION = "semantic_mathml"
SEMANTIC_DISPLAY_EQUATION_VERIFICATION = "semantic_display_equation"
SEMANTIC_LIST_TAG_VERIFICATION = "semantic_list_tag"
SEMANTIC_LIST_TAG_PAGE_TYPES = frozenset({"Outline", "RichText", "List", "Tag"})
SEMANTIC_INK_DRAWING_VERIFICATION = "semantic_ink_drawing"
SEMANTIC_UI_SHAPE_VERIFICATION = "semantic_ui_shape"
SEMANTIC_INK_DRAWING_PAGE_TYPES = frozenset({"Outline", "InkDrawing"})
SEMANTIC_UI_SHAPE_PAGE_TYPES = frozenset({"Outline", "UIShape"})
INK_GEOMETRY_FIELDS = {
    "Position": frozenset({"x", "y", "z"}),
    "Size": frozenset({"width", "height"}),
}
INK_GEOMETRY_ABSOLUTE_TOLERANCE = Decimal("0.0001")
UI_SHAPE_GEOMETRY_ABSOLUTE_TOLERANCE = Decimal("0.02")
MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML"
MATHML_TAGS = frozenset(
    {"math", "mfrac", "mi", "mn", "mo", "mrow", "msqrt", "msup"}
)
MATHML_FRAGMENT_PATTERN = re.compile(
    r"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\b[^>]*>"
    r"(?:(?!<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\b).)*?"
    r"</(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
MATHML_START_PATTERN = re.compile(
    r"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\b",
    flags=re.IGNORECASE,
)
MATHML_PLACEHOLDER = "[[local-onenote-mcp:mathml]]"
DISPLAY_MATHML_LOOKAHEAD = (
    r"(?=\s*(?:<!--\s*\[if\s+mathML\]\s*>)?\s*"
    r"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\b"
    r"(?=[^>]*\bdisplay\s*=\s*(?P<display_quote>['\"])block"
    r"(?P=display_quote))[^>]*>)"
)
DISPLAY_EQUATION_EMPTY_SPAN_SEQUENCE_PATTERN = re.compile(
    r"(?P<spans>(?:<span\b[^>]*>\s*(?:<br\s*/?>\s*)+</span>\s*)+)"
    + DISPLAY_MATHML_LOOKAHEAD,
    flags=re.IGNORECASE,
)
REDUNDANT_BREAK_BEFORE_DISPLAY_MATHML_PATTERN = re.compile(
    r"(?P<breaks>(?:<br\s*/?>\s*)+)" + DISPLAY_MATHML_LOOKAHEAD,
    flags=re.IGNORECASE,
)
SPAN_ATTRIBUTE_PATTERN = re.compile(
    r"\s*(?P<name>[A-Za-z_:][A-Za-z0-9:_.-]*)\s*=\s*"
    r"(?:(?P<quote>['\"])(?P<quoted_value>.*?)(?P=quote)"
    r"|(?P<unquoted_value>[A-Za-z0-9_-]+))",
    flags=re.DOTALL,
)
SPAN_START_PATTERN = re.compile(
    r"<span\b(?P<attributes>[^>]*)>",
    flags=re.IGNORECASE,
)

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
    "OCRData",
    "OCRText",
    "OCRToken",
    "InkDrawing",
    "Ink",
    "ShapeInfo",
    "AnchorPoint",
    "FileAttachment",
    "InsertedFile",
    "MediaFile",
    "MediaPlaylist",
    "MediaIndex",
    "MediaReference",
    "Tag",
    "Meta",
    "MeetingInfo",
    "MeetingInfoItem",
}

UI_SHAPE_CAPABILITY = "UIShape"
UI_SHAPE_STRUCTURAL_NODES = frozenset({"ShapeInfo", "AnchorPoint"})
IMAGE_OCR_STRUCTURAL_NODES = frozenset({"OCRData", "OCRText", "OCRToken"})

GENERATED_OBJECT_ATTRIBUTES = {"objectID", "callbackID"}
VOLATILE_ATTRIBUTES = {
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
EMPTY_SELECTION_ATTRIBUTES = {"selected", "isSelected"}
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


def is_empty_selection_text_node(node: ET.Element) -> bool:
    """Return whether OneNote emitted a content-free selection placeholder."""

    return (
        local_name(node.tag) == "T"
        and not list(node)
        and not (node.text or "").strip()
        and bool(node.attrib)
        and set(node.attrib) <= EMPTY_SELECTION_ATTRIBUTES
    )


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


def _known_display_equation_span_attributes(attributes: str) -> bool:
    """Accept only the observed, presentation-only font/language attributes."""

    position = 0
    observed: set[str] = set()
    while position < len(attributes):
        match = SPAN_ATTRIBUTE_PATTERN.match(attributes, position)
        if match is None:
            return not attributes[position:].strip()
        name = match.group("name").casefold()
        value = match.group("quoted_value") or match.group("unquoted_value") or ""
        if name in observed or name not in {"style", "lang"}:
            return False
        observed.add(name)
        if name == "style":
            if match.group("unquoted_value") is not None:
                return False
            declarations = [
                declaration.strip()
                for declaration in value.split(";")
                if declaration.strip()
            ]
            if len(declarations) != 1 or not declarations[0].casefold().startswith(
                "font-family:"
            ):
                return False
        elif not value or any(character in value for character in "<>"):
            return False
        position = match.end()
    return True


def _known_display_equation_empty_span_sequence(markup: str) -> bool:
    spans = list(SPAN_START_PATTERN.finditer(markup))
    return bool(spans) and all(
        _known_display_equation_span_attributes(match.group("attributes"))
        for match in spans
    )


def _normalize_display_equation_outbound_markup(text: str) -> tuple[str, int, int]:
    """Remove only empty markup immediately preceding block MathML.

    Real OneNote COM read-back has been observed to add one empty ``span``
    containing one ``br`` before a standalone equation.  Removing the whole
    known-empty wrapper before every write bounds that normalization instead
    of allowing it to accumulate across chained copies.
    """

    removed_breaks = 0
    removed_spans = 0

    def remove_spans(match: re.Match[str]) -> str:
        nonlocal removed_breaks, removed_spans
        markup = match.group("spans")
        if not _known_display_equation_empty_span_sequence(markup):
            return match.group(0)
        removed_spans += len(re.findall(r"<span\b", markup, flags=re.IGNORECASE))
        removed_breaks += len(re.findall(r"<br\s*/?>", markup, flags=re.IGNORECASE))
        return ""

    normalized = DISPLAY_EQUATION_EMPTY_SPAN_SEQUENCE_PATTERN.sub(
        remove_spans,
        text,
    )

    def remove_direct_breaks(match: re.Match[str]) -> str:
        nonlocal removed_breaks
        removed_breaks += len(
            re.findall(r"<br\s*/?>", match.group("breaks"), flags=re.IGNORECASE)
        )
        return ""

    normalized = REDUNDANT_BREAK_BEFORE_DISPLAY_MATHML_PATTERN.sub(
        remove_direct_breaks,
        normalized,
    )
    return normalized, removed_breaks, removed_spans


def _strip_identity_and_rewrite(
    node: ET.Element,
    id_map: dict[str, str],
) -> tuple[int, int]:
    display_mathml_breaks_removed = 0
    display_equation_spans_removed = 0
    for key in list(node.attrib):
        if key in GENERATED_OBJECT_ATTRIBUTES or key in VOLATILE_ATTRIBUTES:
            # Path-backed insertion needs a live local pathSource.  It is
            # prepared from an existing source/cache file immediately before
            # this pass; retain it in the outbound payload while canonical
            # comparisons continue to treat machine-local paths as volatile.
            if (
                local_name(node.tag) in {"InsertedFile", "MediaFile"}
                and key == "pathSource"
            ):
                continue
            node.attrib.pop(key, None)
            continue
        if _is_link_attribute(key, node.attrib[key]):
            node.attrib[key] = _replace_ids(node.attrib[key], id_map) or ""
    if node.text and ("onenote:" in node.text.casefold() or "href=" in node.text.casefold()):
        node.text = _replace_ids(node.text, id_map)
    if local_name(node.tag) == "T" and node.text:
        node.text, removed_breaks, removed_spans = _normalize_display_equation_outbound_markup(
            node.text,
        )
        display_mathml_breaks_removed += removed_breaks
        display_equation_spans_removed += removed_spans
    for child in list(node):
        # OneNote can place an empty selection marker before the real Title T.
        # Remove it before stripping volatile attributes; otherwise it becomes
        # an indistinguishable empty T and _set_title may rename the marker
        # while leaving the old visible title in the outbound payload.
        if is_empty_selection_text_node(child):
            node.remove(child)
            continue
        child_breaks, child_spans = _strip_identity_and_rewrite(child, id_map)
        display_mathml_breaks_removed += child_breaks
        display_equation_spans_removed += child_spans
    return display_mathml_breaks_removed, display_equation_spans_removed


def _prepare_local_insertion_source_paths(node: ET.Element) -> None:
    """Bind path-backed objects to one readable local insertion source.

    ``InsertedFile`` has no inline ``Data`` payload in the observed public Page
    XML shape.  OneNote therefore needs a readable path when the object is
    reconstructed through ``UpdatePageContent``.  Keep the XML kinds distinct:
    this only shares path preparation with ``MediaFile`` and does not alias
    ``InsertedFile`` to ``FileAttachment``.
    """

    for candidate in node.iter():
        kind = local_name(candidate.tag)
        if kind not in {"InsertedFile", "MediaFile"}:
            continue
        path_names = (
            ("pathSource", "pathCache", "path")
            if kind == "InsertedFile"
            else ("pathSource", "pathCache")
        )
        readable_source = next(
            (
                candidate.attrib[name]
                for name in path_names
                if candidate.attrib.get(name)
                and Path(candidate.attrib[name]).is_file()
            ),
            "",
        )
        if readable_source:
            candidate.attrib["pathSource"] = readable_source
            continue
        if kind == "InsertedFile":
            raise ValueError(
                "InsertedFile Copy requires a readable local pathSource, "
                "pathCache, or path."
            )


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
    media_file_in_subtree = any(
        local_name(node.tag) == "MediaFile" for node in root.iter()
    )

    def visit(node: ET.Element, ancestors: tuple[str, ...]) -> None:
        tag = node.tag
        namespace = tag[1:].split("}", 1)[0] if tag.startswith("{") and "}" in tag else ""
        kind = local_name(tag)
        context_known = True
        if kind in UI_SHAPE_STRUCTURAL_NODES:
            context_known = "InkDrawing" in ancestors
        elif kind in IMAGE_OCR_STRUCTURAL_NODES:
            context_known = "Image" in ancestors and (
                kind == "OCRData" or "OCRData" in ancestors
            )
        elif kind == "MediaIndex":
            context_known = media_file_in_subtree and "OE" in ancestors
        elif kind == "MediaReference":
            context_known = bool({"MediaFile", "MediaPlaylist"} & set(ancestors)) or (
                media_file_in_subtree and "MediaIndex" in ancestors
            )
        if (
            namespace != ONE_NS
            or kind not in KNOWN_PAGE_XML_NODES
            or not context_known
        ):
            unknown.add(f"{{{namespace}}}{kind}" if namespace else kind)
        for child in list(node):
            visit(child, (*ancestors, kind))

    visit(root, ())
    return sorted(unknown)


def _content_capabilities(
    root: ET.Element,
    source_objects: list[dict[str, Any]],
) -> list[str]:
    media_timeline_text_nodes: set[int] = set()
    for outline in root.iter():
        if local_name(outline.tag) != "Outline" or not any(
            local_name(node.tag) == "MediaFile" for node in outline.iter()
        ):
            continue
        for oe in outline.iter():
            if local_name(oe.tag) != "OE":
                continue
            children = list(oe)
            if [local_name(child.tag) for child in children] != ["MediaIndex", "T"]:
                continue
            text = children[1].text or ""
            tags = set(re.findall(r"</?([A-Za-z0-9]+)\b", text))
            if tags == {"span"}:
                media_timeline_text_nodes.add(id(children[1]))

    capabilities = {
        str(item.get("type"))
        for item in source_objects
        if item.get("type") in COPYABLE_CONTENT_ROOTS
        and item.get("type") != "InkDrawing"
    }
    display_equation_observed = False
    for node in root.iter():
        kind = local_name(node.tag)
        if kind == "InkDrawing":
            capabilities.add(
                UI_SHAPE_CAPABILITY
                if any(local_name(child.tag) == "ShapeInfo" for child in node.iter())
                else "InkDrawing"
            )
        elif kind in COPYABLE_CONTENT_ROOTS:
            capabilities.add(kind)
        if kind == "Table":
            capabilities.add("Table")
        elif kind == "List":
            capabilities.add("List")
        elif kind == "Tag":
            capabilities.add("Tag")
        elif kind in {"MeetingInfo", "MeetingInfoItem"}:
            capabilities.add("MeetingInfo")
        elif kind == "T" and id(node) not in media_timeline_text_nodes and node.text:
            for match in MATHML_FRAGMENT_PATTERN.finditer(node.text):
                try:
                    math = ET.fromstring(match.group(0))
                except ET.ParseError:
                    continue
                if (
                    local_name(math.tag).casefold() == "math"
                    and math.attrib.get("display") == "block"
                ):
                    display_equation_observed = True
            if re.search(
                r"</?(?:a|b|strong|i|em|u|span|font|sup|sub|math|mrow|mi|mo|mn|msup|mfrac|msqrt)\b",
                node.text,
                flags=re.IGNORECASE,
            ):
                capabilities.add("RichText")
    if display_equation_observed:
        capabilities.add("DisplayEquation")
    return sorted(capabilities)


def _embedded_markup_structure(root: ET.Element) -> tuple[Counter[str], Counter[str]]:
    """Count HTML-like tags and attribute names without retaining payload values."""

    tags: Counter[str] = Counter()
    attribute_names: Counter[str] = Counter()
    for node in root.iter():
        if local_name(node.tag) != "T" or not node.text:
            continue
        for match in re.finditer(
            r"<\s*(?!/|!|\?)([A-Za-z][A-Za-z0-9:_-]*)([^>]*)>",
            node.text,
        ):
            tag = match.group(1).casefold()
            tags[tag] += 1
            for attribute in re.finditer(
                r"([A-Za-z_:][A-Za-z0-9:_.-]*)\s*=",
                match.group(2),
            ):
                attribute_names[f"{tag}@{attribute.group(1).casefold()}"] += 1
    return tags, attribute_names


def page_content_capability_projection(source_xml: str) -> dict[str, Any]:
    """Return a content-free, fail-closed capability summary for one Page.

    The projection deliberately excludes text, attributes, IDs, paths, and
    binary payloads. It shares the XML vocabulary used by experimental Copy so
    manual-validation recipes cannot silently invent a second schema.
    """

    root = parse_xml(source_xml)
    source_objects = collect_page_objects(source_xml)
    object_kind_counts = Counter(
        str(item.get("type") or "Unknown") for item in source_objects
    )
    structural_marker_counts: Counter[str] = Counter()
    for ink in root.iter():
        if local_name(ink.tag) != "InkDrawing":
            continue
        structural_marker_counts.update(
            local_name(node.tag)
            for node in ink.iter()
            if local_name(node.tag) in UI_SHAPE_STRUCTURAL_NODES
        )
    embedded_markup_tags, embedded_markup_attribute_names = (
        _embedded_markup_structure(root)
    )
    unsupported_roots: set[str] = set()
    unknown_nodes: set[str] = set()
    for child in list(root):
        kind = local_name(child.tag)
        if kind not in SUPPORTING_ROOTS and kind not in COPYABLE_CONTENT_ROOTS:
            unsupported_roots.add(kind)
        unknown_nodes.update(_unknown_page_nodes(child))
    return {
        "schema_version": 4,
        "capabilities": _content_capabilities(root, source_objects),
        "object_kind_counts": {
            kind: object_kind_counts[kind] for kind in sorted(object_kind_counts)
        },
        "structural_marker_counts": {
            kind: structural_marker_counts[kind]
            for kind in sorted(structural_marker_counts)
        },
        "embedded_markup_tag_counts": {
            tag: embedded_markup_tags[tag] for tag in sorted(embedded_markup_tags)
        },
        "embedded_markup_attribute_name_counts": {
            name: embedded_markup_attribute_names[name]
            for name in sorted(embedded_markup_attribute_names)
        },
        "unknown_nodes": sorted(unknown_nodes),
        "unsupported_page_roots": sorted(unsupported_roots),
        "complete": not unknown_nodes and not unsupported_roots,
    }


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
    display_mathml_breaks_removed = 0
    display_equation_empty_spans_removed = 0
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
        _prepare_local_insertion_source_paths(clone)
        removed_breaks, removed_spans = _strip_identity_and_rewrite(clone, id_map)
        display_mathml_breaks_removed += removed_breaks
        display_equation_empty_spans_removed += removed_spans
        target.append(clone)
        copied_roots.append(kind)

    if title is not None:
        _set_title(target, title)

    source_objects = collect_page_objects(source_xml)
    object_types = _content_capabilities(source, source_objects)
    for kind in object_types:
        if kind in validated:
            continue
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
        "normalizations": {
            "redundant_breaks_before_display_mathml_removed": (
                display_mathml_breaks_removed
            ),
            "display_equation_empty_spans_removed": (
                display_equation_empty_spans_removed
            ),
        },
        "lossless_candidate": not issues,
    }


def _canonical_node(
    node: ET.Element,
    *,
    is_root: bool = False,
    normalize_mathml: bool = False,
) -> list[Any]:
    attributes = sorted(
        (key, value)
        for key, value in node.attrib.items()
        if key not in IGNORED_ATTRIBUTES
        and not (is_root and key in ROOT_REGENERATED_ATTRIBUTES)
    )
    text = (node.text or "").strip()
    if local_name(node.tag) == "Data":
        text = "".join(text.split())
    elif normalize_mathml and local_name(node.tag) == "T":
        text = MATHML_FRAGMENT_PATTERN.sub(MATHML_PLACEHOLDER, text)
    return [
        local_name(node.tag),
        attributes,
        text,
        [
            _canonical_node(child, normalize_mathml=normalize_mathml)
            for child in list(node)
            if not is_empty_selection_text_node(child)
        ],
    ]


def canonical_page_digest(xml: str) -> str:
    """Hash content while ignoring IDs, clocks, selection, and view state."""

    payload = json.dumps(
        _canonical_node(parse_xml(xml), is_root=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _canonical_page_digest_without_mathml(xml: str) -> str:
    """Hash a Page while replacing only complete MathML roots."""

    payload = json.dumps(
        _canonical_node(parse_xml(xml), is_root=True, normalize_mathml=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _mathml_node_projection(node: ET.Element) -> dict[str, Any]:
    namespace, separator, kind = node.tag.rpartition("}")
    if separator:
        namespace = namespace.removeprefix("{")
    else:
        namespace = ""
        kind = node.tag
    kind = kind.casefold()
    if namespace != MATHML_NAMESPACE or kind not in MATHML_TAGS:
        raise ValueError("MathML contains an unsupported namespace or element.")

    attributes: dict[str, str] = {}
    for name, value in node.attrib.items():
        attribute_kind = local_name(name).casefold()
        if kind != "math" or attribute_kind != "display" or value != "block":
            raise ValueError("MathML contains an unsupported attribute.")
        attributes[attribute_kind] = value
    text = "".join((node.text or "").split())
    tail = "".join((node.tail or "").split())
    return {
        "kind": kind,
        "attributes": attributes,
        "text_chars": len(text),
        "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "tail_chars": len(tail),
        "tail_sha256": sha256(tail.encode("utf-8")).hexdigest(),
        "children": [_mathml_node_projection(child) for child in list(node)],
    }


def semantic_mathml_projection(xml: str) -> dict[str, Any]:
    """Project embedded Presentation MathML without exposing equation tokens."""

    equations: list[dict[str, Any]] = []
    complete = True
    root = parse_xml(xml)
    candidate_count = 0
    declared_count = 0
    for node in root.iter():
        if local_name(node.tag) != "T" or not node.text:
            continue
        declared_count += len(MATHML_START_PATTERN.findall(node.text))
        for match in MATHML_FRAGMENT_PATTERN.finditer(node.text):
            candidate_count += 1
            try:
                equation = ET.fromstring(match.group(0))
                equations.append(_mathml_node_projection(equation))
            except (ET.ParseError, ValueError):
                complete = False
    return {
        "declared_count": declared_count,
        "candidate_count": candidate_count,
        "equation_count": len(equations),
        "complete": (
            complete
            and declared_count == candidate_count
            and candidate_count == len(equations)
        ),
        "equations": equations,
    }


def semantic_mathml_comparison(
    expected_xml: str,
    actual_xml: str,
) -> dict[str, Any]:
    source = semantic_mathml_projection(expected_xml)
    target = semantic_mathml_projection(actual_xml)
    source_complete = source["complete"] is True
    target_complete = target["complete"] is True
    projection_equal = source["equations"] == target["equations"]
    outside_mathml_canonical = (
        _canonical_page_digest_without_mathml(expected_xml)
        == _canonical_page_digest_without_mathml(actual_xml)
    )
    return {
        "source_equation_count": source["equation_count"],
        "target_equation_count": target["equation_count"],
        "source_complete": source_complete,
        "target_complete": target_complete,
        "projection_equal": projection_equal,
        "outside_mathml_canonical": outside_mathml_canonical,
        "source_projection_sha256": sha256(
            json.dumps(source["equations"], sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest(),
        "target_projection_sha256": sha256(
            json.dumps(target["equations"], sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest(),
        "passed": (
            source_complete
            and target_complete
            and source["equation_count"] > 0
            and projection_equal
            and outside_mathml_canonical
        ),
    }


def _display_equation_empty_markup_projection(xml: str) -> dict[str, int]:
    """Count only known-empty markup immediately before block MathML."""

    sequence_count = 0
    span_count = 0
    span_break_count = 0
    direct_break_count = 0
    for node in parse_xml(xml).iter():
        if local_name(node.tag) != "T" or not node.text:
            continue
        for match in DISPLAY_EQUATION_EMPTY_SPAN_SEQUENCE_PATTERN.finditer(node.text):
            markup = match.group("spans")
            if not _known_display_equation_empty_span_sequence(markup):
                continue
            sequence_count += 1
            span_count += len(re.findall(r"<span\b", markup, flags=re.IGNORECASE))
            span_break_count += len(
                re.findall(r"<br\s*/?>", markup, flags=re.IGNORECASE)
            )
        without_spans = DISPLAY_EQUATION_EMPTY_SPAN_SEQUENCE_PATTERN.sub(
            lambda match: (
                ""
                if _known_display_equation_empty_span_sequence(match.group("spans"))
                else match.group(0)
            ),
            node.text,
        )
        for match in REDUNDANT_BREAK_BEFORE_DISPLAY_MATHML_PATTERN.finditer(
            without_spans
        ):
            direct_break_count += len(
                re.findall(
                    r"<br\s*/?>",
                    match.group("breaks"),
                    flags=re.IGNORECASE,
                )
            )
    return {
        "span_sequence_count": sequence_count,
        "span_count": span_count,
        "span_break_count": span_break_count,
        "direct_break_count": direct_break_count,
    }


def _canonical_display_equation_page_digest(xml: str) -> str:
    """Canonicalize MathML and the one documented COM display wrapper."""

    root = parse_xml(xml)
    for node in root.iter():
        if local_name(node.tag) == "T" and node.text:
            node.text, _, _ = _normalize_display_equation_outbound_markup(node.text)
    payload = json.dumps(
        _canonical_node(root, is_root=True, normalize_mathml=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def semantic_display_equation_comparison(
    expected_xml: str,
    actual_xml: str,
) -> dict[str, Any]:
    """Compare DisplayEquation while admitting one empty COM wrapper per formula."""

    mathml = semantic_mathml_comparison(expected_xml, actual_xml)
    expected_mathml = semantic_mathml_projection(expected_xml)
    actual_mathml = semantic_mathml_projection(actual_xml)
    expected_display_count = sum(
        equation.get("attributes", {}).get("display") == "block"
        for equation in expected_mathml["equations"]
    )
    actual_display_count = sum(
        equation.get("attributes", {}).get("display") == "block"
        for equation in actual_mathml["equations"]
    )
    expected_markup = _display_equation_empty_markup_projection(expected_xml)
    actual_markup = _display_equation_empty_markup_projection(actual_xml)
    expected_outbound_clean = not any(expected_markup.values())
    actual_known_com_shape = (
        actual_markup["direct_break_count"] == 0
        and actual_markup["span_sequence_count"]
        == actual_markup["span_count"]
        == actual_markup["span_break_count"]
        and actual_markup["span_count"] <= actual_display_count
    )
    outside_mathml_canonical_after_normalization = (
        _canonical_display_equation_page_digest(expected_xml)
        == _canonical_display_equation_page_digest(actual_xml)
    )
    passed = (
        mathml["source_complete"]
        and mathml["target_complete"]
        and expected_display_count > 0
        and expected_display_count == actual_display_count
        and mathml["projection_equal"]
        and expected_outbound_clean
        and actual_known_com_shape
        and outside_mathml_canonical_after_normalization
    )
    return {
        **mathml,
        "expected_display_equation_count": expected_display_count,
        "actual_display_equation_count": actual_display_count,
        "expected_empty_markup": expected_markup,
        "actual_empty_markup": actual_markup,
        "expected_outbound_clean": expected_outbound_clean,
        "actual_known_com_shape": actual_known_com_shape,
        "outside_mathml_canonical_after_display_equation_normalization": (
            outside_mathml_canonical_after_normalization
        ),
        "passed": passed,
    }


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


def copy_verification_tier(
    content_types: Iterable[str],
    *,
    page_xml: str | None = None,
) -> str:
    """Select the narrowest read-back tier supported by a Page's content."""

    observed = set(content_types)
    if page_xml is not None:
        mathml = semantic_mathml_projection(page_xml)
        if mathml["complete"] is True and mathml["equation_count"] > 0:
            if any(
                equation.get("attributes", {}).get("display") == "block"
                for equation in mathml["equations"]
            ):
                return SEMANTIC_DISPLAY_EQUATION_VERIFICATION
            return SEMANTIC_MATHML_VERIFICATION
    if "UIShape" in observed and observed.issubset(SEMANTIC_UI_SHAPE_PAGE_TYPES):
        return SEMANTIC_UI_SHAPE_VERIFICATION
    if "InkDrawing" in observed and observed.issubset(
        SEMANTIC_INK_DRAWING_PAGE_TYPES
    ):
        return SEMANTIC_INK_DRAWING_VERIFICATION
    return (
        SEMANTIC_LIST_TAG_VERIFICATION
        if {"List", "Tag"}.intersection(observed)
        and observed.issubset(SEMANTIC_LIST_TAG_PAGE_TYPES)
        else STRICT_CANONICAL_VERIFICATION
    )


def semantic_list_tag_projection(xml: str) -> list[dict[str, Any]]:
    """Project List/Tag meaning while ignoring COM-generated indices and layout."""

    root = parse_xml(xml)
    tag_definitions: dict[str, dict[str, str]] = {}
    for node in root.iter():
        if local_name(node.tag) != "TagDef" or "index" not in node.attrib:
            continue
        tag_definitions[node.attrib["index"]] = {
            "type": node.attrib.get("type", ""),
            "symbol": node.attrib.get("symbol", ""),
        }

    projection: list[dict[str, Any]] = []
    for node in root.iter():
        if local_name(node.tag) != "OE":
            continue
        children = list(node)
        list_node = next((child for child in children if local_name(child.tag) == "List"), None)
        tag_node = next((child for child in children if local_name(child.tag) == "Tag"), None)
        if list_node is None and tag_node is None:
            continue
        list_kind: str | None = None
        if list_node is not None and list(list_node):
            list_kind = local_name(list(list_node)[0].tag).casefold()
        text = "\n".join(
            html_fragment_to_text(child.text or "")
            for child in children
            if local_name(child.tag) == "T"
        ).strip()
        tag: dict[str, Any] | None = None
        if tag_node is not None:
            tag = {
                **tag_definitions.get(tag_node.attrib.get("index", ""), {"type": "", "symbol": ""}),
                "completed": tag_node.attrib.get("completed", "false").casefold() == "true",
                "disabled": tag_node.attrib.get("disabled", "false").casefold() == "true",
            }
        projection.append({"list_kind": list_kind, "text": text, "tag": tag})
    return projection


def _ink_projection(xml: str) -> list[dict[str, Any]]:
    """Hash stable Ink subtrees while retaining bounded layout evidence."""

    ignored = GENERATED_OBJECT_ATTRIBUTES | VOLATILE_ATTRIBUTES

    def project_node(node: ET.Element) -> dict[str, Any]:
        attributes = dict(
            sorted(
                (local_name(key), value)
                for key, value in node.attrib.items()
                if local_name(key) not in ignored
            )
        )
        text = "".join((node.text or "").split())
        return {
            "kind": local_name(node.tag),
            "attributes": attributes,
            "text_chars": len(text),
            "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
            "children": [project_node(child) for child in list(node)],
        }

    return [
        project_node(node)
        for node in parse_xml(xml).iter()
        if local_name(node.tag) == "InkDrawing"
    ]


def _compare_ink_projections(
    source: list[dict[str, Any]],
    target: list[dict[str, Any]],
    *,
    geometry_absolute_tolerance: Decimal,
) -> dict[str, Any]:
    geometry_deltas: list[dict[str, Any]] = []
    mismatch_paths: list[str] = []
    structure_and_data_equal = len(source) == len(target)
    geometry_within_tolerance = True

    def compare_node(
        left: Mapping[str, Any],
        right: Mapping[str, Any],
        path: str,
    ) -> None:
        nonlocal structure_and_data_equal, geometry_within_tolerance
        left_kind = str(left.get("kind", ""))
        right_kind = str(right.get("kind", ""))
        if left_kind != right_kind:
            structure_and_data_equal = False
            mismatch_paths.append(f"{path}#kind")
            return

        left_attributes = left.get("attributes", {})
        right_attributes = right.get("attributes", {})
        if not isinstance(left_attributes, Mapping) or not isinstance(
            right_attributes, Mapping
        ):
            structure_and_data_equal = False
            mismatch_paths.append(f"{path}#attribute-schema")
            return
        if set(left_attributes) != set(right_attributes):
            structure_and_data_equal = False
            mismatch_paths.append(f"{path}#attribute-names")

        geometry_names = INK_GEOMETRY_FIELDS.get(left_kind, frozenset())
        for name in sorted(set(left_attributes) & set(right_attributes)):
            left_value = str(left_attributes[name])
            right_value = str(right_attributes[name])
            if name not in geometry_names:
                if left_value != right_value:
                    structure_and_data_equal = False
                    mismatch_paths.append(f"{path}@{name}")
                continue
            try:
                left_number = Decimal(left_value)
                right_number = Decimal(right_value)
                if not left_number.is_finite() or not right_number.is_finite():
                    raise InvalidOperation
                delta = abs(left_number - right_number)
            except InvalidOperation:
                geometry_within_tolerance = False
                mismatch_paths.append(f"{path}@{name}#non-numeric")
                geometry_deltas.append(
                    {
                        "path": path,
                        "field": name,
                        "source": left_value,
                        "target": right_value,
                        "absolute_delta": None,
                        "within_tolerance": False,
                    }
                )
                continue
            within_tolerance = delta <= geometry_absolute_tolerance
            geometry_within_tolerance = geometry_within_tolerance and within_tolerance
            if not within_tolerance:
                mismatch_paths.append(f"{path}@{name}#outside-tolerance")
            geometry_deltas.append(
                {
                    "path": path,
                    "field": name,
                    "source": left_value,
                    "target": right_value,
                    "absolute_delta": str(delta),
                    "within_tolerance": within_tolerance,
                }
            )

        for field in ("text_chars", "text_sha256"):
            if left.get(field) != right.get(field):
                structure_and_data_equal = False
                mismatch_paths.append(f"{path}#{field}")

        left_children = left.get("children", ())
        right_children = right.get("children", ())
        if not isinstance(left_children, list) or not isinstance(right_children, list):
            structure_and_data_equal = False
            mismatch_paths.append(f"{path}#children-schema")
            return
        if len(left_children) != len(right_children):
            structure_and_data_equal = False
            mismatch_paths.append(f"{path}#children-count")
        for index, (left_child, right_child) in enumerate(
            zip(left_children, right_children, strict=False)
        ):
            compare_node(left_child, right_child, f"{path}/child[{index}]")

    for index, (left, right) in enumerate(zip(source, target, strict=False)):
        compare_node(left, right, f"/InkDrawing[{index}]")

    numeric_deltas = [
        Decimal(str(value["absolute_delta"]))
        for value in geometry_deltas
        if value["absolute_delta"] is not None
    ]
    return {
        "geometry_absolute_tolerance": str(geometry_absolute_tolerance),
        "geometry_deltas": geometry_deltas,
        "max_geometry_absolute_delta": (
            str(max(numeric_deltas)) if numeric_deltas else None
        ),
        "structure_and_data_equal": structure_and_data_equal,
        "geometry_within_tolerance": geometry_within_tolerance,
        "mismatch_paths": sorted(set(mismatch_paths)),
        "passed": structure_and_data_equal and geometry_within_tolerance,
    }


def semantic_ink_drawing_comparison(
    expected_xml: str,
    actual_xml: str,
    *,
    geometry_absolute_tolerance: Decimal,
) -> dict[str, Any]:
    return _compare_ink_projections(
        _ink_projection(expected_xml),
        _ink_projection(actual_xml),
        geometry_absolute_tolerance=geometry_absolute_tolerance,
    )


def page_equivalence(
    expected_xml: str,
    actual_xml: str,
    *,
    verification_tier: str = STRICT_CANONICAL_VERIFICATION,
) -> dict[str, Any]:
    """Return the stable content checks used by Copy and Page Move."""

    checks = {
        "canonical_xml": canonical_page_digest(expected_xml) == canonical_page_digest(actual_xml),
        "visible_text": text_from_page_xml(expected_xml) == text_from_page_xml(actual_xml),
        "content_objects": page_content_type_counts(expected_xml)
        == page_content_type_counts(actual_xml),
        "binary_sha256": page_binary_hashes(expected_xml) == page_binary_hashes(actual_xml),
    }
    if verification_tier == STRICT_CANONICAL_VERIFICATION:
        acceptance_checks = list(checks)
    elif verification_tier == SEMANTIC_MATHML_VERIFICATION:
        mathml_comparison = semantic_mathml_comparison(expected_xml, actual_xml)
        checks["semantic_mathml"] = (
            mathml_comparison["source_complete"]
            and mathml_comparison["target_complete"]
            and mathml_comparison["source_equation_count"] > 0
            and mathml_comparison["projection_equal"]
        )
        checks["outside_mathml_canonical"] = mathml_comparison[
            "outside_mathml_canonical"
        ]
        acceptance_checks = [
            "visible_text",
            "content_objects",
            "binary_sha256",
            "semantic_mathml",
            "outside_mathml_canonical",
        ]
    elif verification_tier == SEMANTIC_DISPLAY_EQUATION_VERIFICATION:
        display_equation_comparison = semantic_display_equation_comparison(
            expected_xml,
            actual_xml,
        )
        checks["semantic_mathml"] = (
            display_equation_comparison["source_complete"]
            and display_equation_comparison["target_complete"]
            and display_equation_comparison["projection_equal"]
        )
        checks["display_equation_com_normalization"] = (
            display_equation_comparison["passed"]
        )
        checks["outside_mathml_canonical"] = display_equation_comparison[
            "outside_mathml_canonical_after_display_equation_normalization"
        ]
        acceptance_checks = [
            "visible_text",
            "content_objects",
            "binary_sha256",
            "semantic_mathml",
            "display_equation_com_normalization",
            "outside_mathml_canonical",
        ]
    elif verification_tier == SEMANTIC_LIST_TAG_VERIFICATION:
        checks["semantic_list_tag"] = (
            semantic_list_tag_projection(expected_xml)
            == semantic_list_tag_projection(actual_xml)
        )
        acceptance_checks = ["visible_text", "binary_sha256", "semantic_list_tag"]
    elif verification_tier in {
        SEMANTIC_INK_DRAWING_VERIFICATION,
        SEMANTIC_UI_SHAPE_VERIFICATION,
    }:
        tolerance = (
            UI_SHAPE_GEOMETRY_ABSOLUTE_TOLERANCE
            if verification_tier == SEMANTIC_UI_SHAPE_VERIFICATION
            else INK_GEOMETRY_ABSOLUTE_TOLERANCE
        )
        ink_comparison = semantic_ink_drawing_comparison(
            expected_xml,
            actual_xml,
            geometry_absolute_tolerance=tolerance,
        )
        checks[verification_tier] = ink_comparison["passed"]
        acceptance_checks = [
            "visible_text",
            "content_objects",
            "binary_sha256",
            verification_tier,
        ]
    else:
        raise ValueError(f"Unsupported Copy verification tier: {verification_tier}")
    result = {
        "equivalent": all(checks[name] for name in acceptance_checks),
        "verification_tier": verification_tier,
        "acceptance_checks": acceptance_checks,
        "checks": checks,
    }
    if verification_tier in {
        SEMANTIC_INK_DRAWING_VERIFICATION,
        SEMANTIC_UI_SHAPE_VERIFICATION,
    }:
        result["ink_projection_comparison"] = ink_comparison
    elif verification_tier == SEMANTIC_MATHML_VERIFICATION:
        result["mathml_projection_comparison"] = mathml_comparison
    elif verification_tier == SEMANTIC_DISPLAY_EQUATION_VERIFICATION:
        result["display_equation_comparison"] = display_equation_comparison
    return result
