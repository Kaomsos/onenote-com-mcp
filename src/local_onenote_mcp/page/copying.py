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
from html.parser import HTMLParser

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
SEMANTIC_CONTENT_VERIFICATION = "semantic_content_v1"
SEMANTIC_CONTENT_PAGE_TYPES = frozenset(
    {"Outline", "RichText", "List", "Tag", "Table", "Image"}
)
TABLE_COLUMN_WIDTH_RELATIVE_TOLERANCE = Decimal("0.05")
CONTENT_OBJECT_FAILURE_LIMIT = 24
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
MATHML_CONDITIONAL_FRAGMENT_PATTERN = re.compile(
    r"<!--\s*\[if\s+mathML\]\s*>\s*"
    r"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\b[^>]*>"
    r"(?:(?!<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\b).)*?"
    r"</(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\s*>"
    r"\s*<!\s*\[endif\]\s*-->",
    flags=re.IGNORECASE | re.DOTALL,
)
MATHML_START_PATTERN = re.compile(
    r"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\b",
    flags=re.IGNORECASE,
)
MATHML_PLACEHOLDER = "[[local-onenote-mcp:mathml]]"
DISPLAY_EQUATION_DERIVED_SIZE_PLACEHOLDER = "[[local-onenote-mcp:derived-size]]"
TITLE_OE_COM_STYLE_PLACEHOLDER = "[[local-onenote-mcp:title-oe-com-style]]"
TITLE_OE_COM_STYLE_ATTRIBUTES = frozenset(
    {"alignment", "quickStyleIndex", "style"}
)
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
        # OneNote may add, remove, or re-space its documented conditional
        # comment wrapper while reserializing the same complete MathML root.
        # Match the complete paired wrapper first.  Unrelated comments and
        # incomplete wrappers remain in the canonical text and fail closed.
        text = MATHML_CONDITIONAL_FRAGMENT_PATTERN.sub(MATHML_PLACEHOLDER, text)
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


def _normalized_display_equation_page_root(xml: str) -> ET.Element:
    """Build one Page tree with only the proven DisplayEquation normalizations."""

    root = parse_xml(xml)
    for node in root.iter():
        if local_name(node.tag) == "T" and node.text:
            node.text, _, _ = _normalize_display_equation_outbound_markup(node.text)
    _normalize_standalone_display_equation_outline_sizes(root)
    return root


def _canonical_display_equation_page_projection(xml: str) -> list[Any]:
    """Build the internal canonical tree used by DisplayEquation comparison."""

    root = _normalized_display_equation_page_root(xml)
    return _canonical_node(root, is_root=True, normalize_mathml=True)


def _title_text_for_com_style_normalization(root: ET.Element) -> str | None:
    titles = [child for child in list(root) if local_name(child.tag) == "Title"]
    if len(titles) != 1:
        return None
    title = titles[0]
    if any(local_name(node.tag) not in {"Title", "OE", "T"} for node in title.iter()):
        return None
    fragments = [
        node.text or ""
        for node in title.iter()
        if local_name(node.tag) == "T" and not is_empty_selection_text_node(node)
    ]
    return html_fragment_to_text("".join(fragments))


def _normalize_matching_title_oe_com_styles(
    expected_root: ET.Element,
    actual_root: ET.Element,
) -> dict[str, Any]:
    """Normalize only matching-shape, equal-text Title OE COM style values."""

    expected_title = _title_text_for_com_style_normalization(expected_root)
    actual_title = _title_text_for_com_style_normalization(actual_root)
    expected_oes = [
        node
        for title in list(expected_root)
        if local_name(title.tag) == "Title"
        for node in title.iter()
        if local_name(node.tag) == "OE"
    ]
    actual_oes = [
        node
        for title in list(actual_root)
        if local_name(title.tag) == "Title"
        for node in title.iter()
        if local_name(node.tag) == "OE"
    ]
    evidence: dict[str, Any] = {
        "applicable": False,
        "applied": False,
        "title_text_equal": expected_title is not None and expected_title == actual_title,
        "expected_title_oe_count": len(expected_oes),
        "actual_title_oe_count": len(actual_oes),
        "attribute_sets_equal": False,
        "normalized_attribute_names": [],
        "differing_attribute_names": [],
        "content_exposed": False,
    }
    if (
        expected_title is None
        or actual_title is None
        or expected_title != actual_title
        or not expected_oes
        or len(expected_oes) != len(actual_oes)
    ):
        return evidence
    if any(
        {name for name in expected.attrib if name not in IGNORED_ATTRIBUTES}
        != {name for name in actual.attrib if name not in IGNORED_ATTRIBUTES}
        for expected, actual in zip(expected_oes, actual_oes, strict=True)
    ):
        return evidence

    evidence["attribute_sets_equal"] = True
    evidence["applicable"] = True
    normalized_names: set[str] = set()
    differing_names: set[str] = set()
    for expected, actual in zip(expected_oes, actual_oes, strict=True):
        comparable_names = {
            name for name in expected.attrib if name not in IGNORED_ATTRIBUTES
        }
        for name in sorted(comparable_names & TITLE_OE_COM_STYLE_ATTRIBUTES):
            normalized_names.add(name)
            if expected.attrib[name] != actual.attrib[name]:
                differing_names.add(name)
            expected.attrib[name] = TITLE_OE_COM_STYLE_PLACEHOLDER
            actual.attrib[name] = TITLE_OE_COM_STYLE_PLACEHOLDER
    evidence["normalized_attribute_names"] = sorted(normalized_names)
    evidence["differing_attribute_names"] = sorted(differing_names)
    evidence["applied"] = bool(differing_names)
    return evidence


def _standalone_display_equation_outline(outline: ET.Element) -> bool:
    """Recognize an Outline whose only authored content is one block MathML root."""

    allowed_nodes = {"Outline", "Position", "Size", "OEChildren", "OE", "T"}
    if any(local_name(node.tag) not in allowed_nodes for node in outline.iter()):
        return False
    size_nodes = [
        child for child in list(outline) if local_name(child.tag) == "Size"
    ]
    if len(size_nodes) != 1 or set(size_nodes[0].attrib) != {"width", "height"}:
        return False

    equation_count = 0
    display_count = 0
    substantive_text_nodes = 0
    for node in outline.iter():
        if local_name(node.tag) != "T" or not node.text:
            continue
        text = node.text
        matches = list(MATHML_FRAGMENT_PATTERN.finditer(text))
        if matches:
            substantive_text_nodes += 1
        equation_count += len(matches)
        for match in matches:
            try:
                equation = ET.fromstring(match.group(0))
            except ET.ParseError:
                return False
            if equation.attrib.get("display") == "block":
                display_count += 1
        residual, _, _ = _normalize_display_equation_outbound_markup(text)
        residual = MATHML_CONDITIONAL_FRAGMENT_PATTERN.sub("", residual)
        residual = MATHML_FRAGMENT_PATTERN.sub("", residual)
        if residual.strip():
            return False
    return equation_count == display_count == substantive_text_nodes == 1


def _normalize_standalone_display_equation_outline_sizes(root: ET.Element) -> int:
    """Ignore only COM-derived bounds of a formula-only Outline."""

    normalized = 0
    for outline in root.iter():
        if (
            local_name(outline.tag) != "Outline"
            or not _standalone_display_equation_outline(outline)
        ):
            continue
        size = next(
            child for child in list(outline) if local_name(child.tag) == "Size"
        )
        size.attrib["width"] = DISPLAY_EQUATION_DERIVED_SIZE_PLACEHOLDER
        size.attrib["height"] = DISPLAY_EQUATION_DERIVED_SIZE_PLACEHOLDER
        normalized += 1
    return normalized


def _standalone_display_equation_outline_count(xml: str) -> int:
    return sum(
        _standalone_display_equation_outline(node)
        for node in parse_xml(xml).iter()
        if local_name(node.tag) == "Outline"
    )


def _mathml_conditional_wrapper_count(xml: str) -> int:
    """Count only complete, paired OneNote MathML conditional wrappers."""

    return sum(
        len(MATHML_CONDITIONAL_FRAGMENT_PATTERN.findall(node.text))
        for node in parse_xml(xml).iter()
        if local_name(node.tag) == "T" and node.text
    )


def _canonical_mismatch_projection(
    expected: list[Any],
    actual: list[Any],
    *,
    path: str = "Page",
) -> dict[str, Any] | None:
    """Return the first content-free canonical-tree mismatch."""

    expected_kind, expected_attributes, expected_text, expected_children = expected
    actual_kind, actual_attributes, actual_text, actual_children = actual
    if expected_kind != actual_kind:
        return {
            "path": path,
            "field": "kind",
            "expected_kind": expected_kind,
            "actual_kind": actual_kind,
        }
    if expected_attributes != actual_attributes:
        expected_payload = json.dumps(
            expected_attributes, ensure_ascii=False, separators=(",", ":")
        )
        actual_payload = json.dumps(
            actual_attributes, ensure_ascii=False, separators=(",", ":")
        )
        expected_by_name = dict(expected_attributes)
        actual_by_name = dict(actual_attributes)
        return {
            "path": path,
            "field": "attributes",
            "expected_attribute_names": [name for name, _ in expected_attributes],
            "actual_attribute_names": [name for name, _ in actual_attributes],
            "differing_attribute_names": sorted(
                name
                for name in set(expected_by_name) & set(actual_by_name)
                if expected_by_name[name] != actual_by_name[name]
            ),
            "expected_sha256": sha256(expected_payload.encode("utf-8")).hexdigest(),
            "actual_sha256": sha256(actual_payload.encode("utf-8")).hexdigest(),
        }
    if expected_text != actual_text:
        return {
            "path": path,
            "field": "text",
            "expected_chars": len(expected_text),
            "actual_chars": len(actual_text),
            "expected_sha256": sha256(expected_text.encode("utf-8")).hexdigest(),
            "actual_sha256": sha256(actual_text.encode("utf-8")).hexdigest(),
        }
    for index, (expected_child, actual_child) in enumerate(
        zip(expected_children, actual_children, strict=False)
    ):
        mismatch = _canonical_mismatch_projection(
            expected_child,
            actual_child,
            path=f"{path}/{expected_child[0]}[{index}]",
        )
        if mismatch is not None:
            return mismatch
    if len(expected_children) != len(actual_children):
        return {
            "path": path,
            "field": "children",
            "expected_count": len(expected_children),
            "actual_count": len(actual_children),
            "expected_kinds": [child[0] for child in expected_children],
            "actual_kinds": [child[0] for child in actual_children],
        }
    return None


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
    expected_root = _normalized_display_equation_page_root(expected_xml)
    actual_root = _normalized_display_equation_page_root(actual_xml)
    title_oe_com_style_normalization = _normalize_matching_title_oe_com_styles(
        expected_root,
        actual_root,
    )
    expected_canonical = _canonical_node(
        expected_root,
        is_root=True,
        normalize_mathml=True,
    )
    actual_canonical = _canonical_node(
        actual_root,
        is_root=True,
        normalize_mathml=True,
    )
    outside_mathml_canonical_after_normalization = (
        expected_canonical == actual_canonical
    )
    outside_mathml_mismatch = (
        None
        if outside_mathml_canonical_after_normalization
        else _canonical_mismatch_projection(expected_canonical, actual_canonical)
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
        "expected_conditional_mathml_wrapper_count": (
            _mathml_conditional_wrapper_count(expected_xml)
        ),
        "actual_conditional_mathml_wrapper_count": (
            _mathml_conditional_wrapper_count(actual_xml)
        ),
        "expected_derived_size_outline_count": (
            _standalone_display_equation_outline_count(expected_xml)
        ),
        "actual_derived_size_outline_count": (
            _standalone_display_equation_outline_count(actual_xml)
        ),
        "expected_display_equation_count": expected_display_count,
        "actual_display_equation_count": actual_display_count,
        "expected_empty_markup": expected_markup,
        "actual_empty_markup": actual_markup,
        "expected_outbound_clean": expected_outbound_clean,
        "actual_known_com_shape": actual_known_com_shape,
        "title_oe_com_style_normalization": title_oe_com_style_normalization,
        "outside_mathml_canonical_after_display_equation_normalization": (
            outside_mathml_canonical_after_normalization
        ),
        "outside_mathml_mismatch": outside_mathml_mismatch,
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


SEMANTIC_INLINE_TAG_ALIASES = {
    "b": "strong",
    "i": "em",
    "s": "del",
    "strike": "del",
}
SEMANTIC_INLINE_TAGS = frozenset(
    {
        "a",
        "b",
        "br",
        "code",
        "del",
        "em",
        "font",
        "i",
        "s",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "u",
    }
)
SEMANTIC_INLINE_ATTRIBUTES = {
    "a": frozenset({"href", "title"}),
    "font": frozenset({"color", "face", "size"}),
    "span": frozenset({"lang", "style", "title"}),
}


class _SemanticInlineParser(HTMLParser):
    """Project formatted text by effective runs, independent of T segmentation."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        self.runs: list[tuple[str, tuple[Any, ...]]] = []
        self.complete = True

    @staticmethod
    def _style_declarations(value: str) -> dict[str, str]:
        declarations: dict[str, str] = {}
        for declaration in value.split(";"):
            if ":" not in declaration:
                continue
            name, item = declaration.split(":", 1)
            name = name.strip().casefold()
            item = " ".join(item.strip().split())
            if name and item:
                # CSS applies the last declaration for a property. OneNote can
                # collapse nested or adjacent spans while retaining that
                # effective formatting, so preserve the computed declaration
                # rather than the wrapper shape.
                declarations[name] = item
        return declarations

    @classmethod
    def _style(cls, value: str) -> str:
        return ";".join(
            f"{name}:{item}"
            for name, item in sorted(cls._style_declarations(value).items())
        )

    def _effective_style(self) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
        """Return per-run formatting independent of redundant HTML wrappers."""

        semantic_tags: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        span_attributes: dict[str, str] = {}
        span_style: dict[str, str] = {}
        font_attributes: dict[str, str] = {}
        for tag, attributes in self.stack:
            if tag == "span":
                for name, value in attributes:
                    if name == "style":
                        span_style.update(self._style_declarations(value))
                    else:
                        span_attributes[name] = value
                continue
            if tag == "font":
                font_attributes.update(attributes)
                continue
            # Repeated strong/em/link wrappers have the same effective meaning
            # for a text run. Attribute changes remain visible in the set.
            semantic_tags.add((tag, attributes))

        if span_style:
            span_attributes["style"] = ";".join(
                f"{name}:{value}" for name, value in sorted(span_style.items())
            )
        if span_attributes:
            semantic_tags.add(("span", tuple(sorted(span_attributes.items()))))
        if font_attributes:
            semantic_tags.add(("font", tuple(sorted(font_attributes.items()))))
        return tuple(sorted(semantic_tags))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag not in SEMANTIC_INLINE_TAGS:
            self.complete = False
            return
        if tag == "br":
            self.handle_data("\n")
            return
        allowed = SEMANTIC_INLINE_ATTRIBUTES.get(tag, frozenset())
        projected: list[tuple[str, str]] = []
        for name, value in attrs:
            name = name.casefold()
            if name not in allowed or value is None:
                self.complete = False
                continue
            if name == "style":
                value = self._style(value)
                if not value:
                    continue
            projected.append((name, value))
        self.stack.append(
            (SEMANTIC_INLINE_TAG_ALIASES.get(tag, tag), tuple(sorted(projected)))
        )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() != "br":
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = SEMANTIC_INLINE_TAG_ALIASES.get(tag.casefold(), tag.casefold())
        if not self.stack or self.stack[-1][0] != tag:
            self.complete = False
            return
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        if not data:
            return
        style = self._effective_style()
        if self.runs and self.runs[-1][1] == style:
            previous, _ = self.runs[-1]
            self.runs[-1] = (previous + data, style)
        else:
            self.runs.append((data, style))

    def result(self) -> tuple[tuple[Any, ...], bool]:
        if self.stack:
            self.complete = False
        normalized_runs: list[tuple[str, tuple[Any, ...]]] = []
        for text, style in self.runs:
            # OneNote can move the boundary whitespace between adjacent formatted
            # spans during UpdatePageContent read-back.  Formatting ownership of
            # whitespace is not a stable rich-text semantic, while the exact text
            # sequence and the effective style of every non-whitespace character
            # remain meaningful.  Give whitespace a neutral style, then coalesce
            # adjacent equal-style pieces.  This accepts only boundary movement;
            # moving or losing formatting on visible characters still differs.
            for piece in re.split(r"(\s+)", text):
                if not piece:
                    continue
                effective_style = () if piece.isspace() else style
                if normalized_runs and normalized_runs[-1][1] == effective_style:
                    previous, _ = normalized_runs[-1]
                    normalized_runs[-1] = (previous + piece, effective_style)
                else:
                    normalized_runs.append((piece, effective_style))
        return tuple(normalized_runs), self.complete


def _semantic_inline_projection(fragments: Iterable[str]) -> tuple[tuple[Any, ...], bool]:
    parser = _SemanticInlineParser()
    for fragment in fragments:
        parser.feed(fragment or "")
    parser.close()
    return parser.result()


def _semantic_tag_definition_projection(root: ET.Element) -> dict[str, tuple[str, str]]:
    return {
        node.attrib["index"]: (
            node.attrib.get("type", ""),
            node.attrib.get("symbol", ""),
        )
        for node in root.iter()
        if local_name(node.tag) == "TagDef" and "index" in node.attrib
    }


def semantic_content_projection(xml: str) -> dict[str, Any]:
    """Project reviewed COM-stable meaning into typed, content-free structure."""

    root = parse_xml(xml)
    capability_projection = page_content_capability_projection(xml)
    capabilities = set(capability_projection.get("capabilities", ()))
    complete = bool(capability_projection.get("complete")) and capabilities.issubset(
        SEMANTIC_CONTENT_PAGE_TYPES
    )
    tag_definitions = _semantic_tag_definition_projection(root)
    next_table_ordinal = 0

    def tag_projection(node: ET.Element) -> dict[str, Any] | None:
        if local_name(node.tag) != "Tag":
            return None
        semantic_type, symbol = tag_definitions.get(
            node.attrib.get("index", ""), ("", "")
        )
        return {
            "type": semantic_type,
            "symbol": symbol,
            "completed": node.attrib.get("completed", "false").casefold() == "true",
            "disabled": node.attrib.get("disabled", "false").casefold() == "true",
        }

    def list_projection(node: ET.Element) -> str | None:
        if local_name(node.tag) != "List":
            return None
        marker = next(iter(node), None)
        return local_name(marker.tag).casefold() if marker is not None else "list"

    def stable_attributes(node: ET.Element) -> dict[str, str]:
        return dict(
            sorted(
                (local_name(name), value)
                for name, value in node.attrib.items()
                if local_name(name) not in IGNORED_ATTRIBUTES
            )
        )

    def table_projection(table: ET.Element) -> dict[str, Any]:
        nonlocal complete, next_table_ordinal
        table_ordinal = next_table_ordinal
        next_table_ordinal += 1
        columns = tuple(
            {
                "column_ordinal": column_ordinal,
                "attributes": stable_attributes(column),
            }
            for container in list(table)
            if local_name(container.tag) == "Columns"
            for column_ordinal, column in enumerate(
                child for child in list(container) if local_name(child.tag) == "Column"
            )
        )
        rows: list[dict[str, Any]] = []
        for row in (child for child in list(table) if local_name(child.tag) == "Row"):
            cells: list[dict[str, Any]] = []
            for cell in (child for child in list(row) if local_name(child.tag) == "Cell"):
                fragments = [
                    node.text or ""
                    for node in cell.iter()
                    if local_name(node.tag) == "T"
                ]
                rich, rich_complete = _semantic_inline_projection(fragments)
                complete = complete and rich_complete
                lists = tuple(
                    value
                    for value in (list_projection(node) for node in cell.iter())
                    if value is not None
                )
                tags = tuple(
                    value
                    for value in (tag_projection(node) for node in cell.iter())
                    if value is not None
                )
                nested_tables = tuple(
                    table_projection(node)
                    for node in cell.iter()
                    if node is not table and local_name(node.tag) == "Table"
                )
                cells.append(
                    {
                        "attributes": stable_attributes(cell),
                        "rich_text": rich,
                        "lists": lists,
                        "tags": tags,
                        "tables": nested_tables,
                    }
                )
            rows.append({"attributes": stable_attributes(row), "cells": tuple(cells)})
        return {
            "table_ordinal": table_ordinal,
            "attributes": stable_attributes(table),
            "columns": columns,
            "rows": tuple(rows),
        }

    def oe_projection(oe: ET.Element) -> dict[str, Any]:
        nonlocal complete
        fragments = [
            child.text or ""
            for child in list(oe)
            if local_name(child.tag) == "T"
        ]
        rich, rich_complete = _semantic_inline_projection(fragments)
        complete = complete and rich_complete
        list_value = next(
            (
                value
                for value in (list_projection(child) for child in list(oe))
                if value is not None
            ),
            None,
        )
        tags = tuple(
            value
            for value in (tag_projection(child) for child in list(oe))
            if value is not None
        )
        tables = tuple(
            table_projection(child)
            for child in list(oe)
            if local_name(child.tag) == "Table"
        )
        binary_kinds = tuple(
            local_name(child.tag)
            for child in list(oe)
            if local_name(child.tag) in {"Image"}
        )
        nested = tuple(
            oe_projection(child)
            for container in list(oe)
            if local_name(container.tag) == "OEChildren"
            for child in list(container)
            if local_name(child.tag) == "OE"
        )
        return {
            "rich_text": rich,
            "list": list_value,
            "tags": tags,
            "tables": tables,
            "binary_objects": binary_kinds,
            "children": nested,
        }

    title_fragments = [
        node.text or ""
        for title in root.iter()
        if local_name(title.tag) == "Title"
        for node in title.iter()
        if local_name(node.tag) == "T" and not is_empty_selection_text_node(node)
    ]
    title = html_fragment_to_text("".join(title_fragments))

    outlines: list[dict[str, Any]] = []
    for outline in (node for node in root.iter() if local_name(node.tag) == "Outline"):
        values = tuple(
            oe_projection(child)
            for container in list(outline)
            if local_name(container.tag) == "OEChildren"
            for child in list(container)
            if local_name(child.tag) == "OE"
        )
        meaningful = any(
            value["rich_text"]
            or value["list"] is not None
            or value["tags"]
            or value["tables"]
            or value["binary_objects"]
            or value["children"]
            for value in values
        )
        if meaningful:
            outlines.append({"outline_ordinal": len(outlines), "children": values})

    object_counts = page_content_type_counts(xml)
    object_counts.pop("Outline", None)
    return {
        "complete": complete,
        "title": title,
        "outlines": tuple(outlines),
        "object_counts": object_counts,
        "binary_sha256": tuple(page_binary_hashes(xml)),
    }


def _semantic_projection_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _semantic_projection_summary(projection: Mapping[str, Any]) -> dict[str, Any]:
    title = str(projection.get("title", ""))
    outlines = projection.get("outlines", ())
    object_counts = projection.get("object_counts", ())
    binary_hashes = projection.get("binary_sha256", ())
    return {
        "complete": bool(projection.get("complete")),
        "title_chars": len(title),
        "title_sha256": sha256(title.encode("utf-8")).hexdigest(),
        "outline_count": len(outlines),
        "outlines_sha256": _semantic_projection_digest(outlines),
        "object_counts": dict(object_counts),
        "binary_count": len(binary_hashes),
        "binary_set_sha256": _semantic_projection_digest(binary_hashes),
    }


def _semantic_projection_mismatches(
    source: Any,
    target: Any,
    *,
    limit: int = 24,
) -> dict[str, Any]:
    """Locate bounded semantic differences without returning Page content."""

    mismatches: list[dict[str, Any]] = []
    truncated = False

    def add(path: str, kind: str, **details: Any) -> None:
        nonlocal truncated
        if len(mismatches) >= limit:
            truncated = True
            return
        mismatches.append({"path": path, "kind": kind, **details})

    def compare(left: Any, right: Any, path: str) -> None:
        nonlocal truncated
        if truncated:
            return
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            left_keys = set(left)
            right_keys = set(right)
            if left_keys != right_keys:
                add(
                    path,
                    "mapping_keys",
                    source_count=len(left_keys),
                    target_count=len(right_keys),
                )
            for key in sorted(left_keys & right_keys, key=str):
                compare(left[key], right[key], f"{path}.{key}")
            return
        if isinstance(left, (tuple, list)) and isinstance(right, (tuple, list)):
            if len(left) != len(right):
                add(
                    path,
                    "sequence_length",
                    source_count=len(left),
                    target_count=len(right),
                )
            for index, (left_item, right_item) in enumerate(
                zip(left, right, strict=False)
            ):
                compare(left_item, right_item, f"{path}[{index}]")
            return
        if type(left) is not type(right):
            add(
                path,
                "value_type",
                source_type=type(left).__name__,
                target_type=type(right).__name__,
            )
            return
        if left != right:
            add(path, "value")

    compare(source, target, "$")
    return {
        "limit": limit,
        "reported": len(mismatches),
        "truncated": truncated,
        "items": mismatches,
    }


def _positive_finite_decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _semantic_content_typed_comparison(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    table_column_width_relative_tolerance: Decimal | None,
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    failed_types: set[str] = set()
    total_failures = 0
    width_comparisons: list[dict[str, Any]] = []

    def add_failure(
        code: str,
        content_object_type: str,
        path: str,
        **details: Any,
    ) -> None:
        nonlocal total_failures
        total_failures += 1
        failed_types.add(content_object_type)
        if len(failures) < CONTENT_OBJECT_FAILURE_LIMIT:
            failures.append(
                {
                    "code": code,
                    "content_object_type": content_object_type,
                    "path": path,
                    **details,
                    "content_exposed": False,
                }
            )

    def classification(
        path: str,
        context: str,
        *,
        in_table_cell: bool,
    ) -> tuple[str, str]:
        if path == "$.title":
            return "PageTitle", "page_title_mismatch"
        if path.startswith("$.binary_sha256"):
            return "Image", "image_binary_mismatch"
        if path.startswith("$.object_counts"):
            object_type = path.rsplit(".", 1)[-1]
            return object_type, f"{object_type.casefold()}_object_count_mismatch"
        if context == "Column":
            return "Table", "table_column_attribute_mismatch"
        if context in {"Table", "Row", "Cell"} or in_table_cell:
            return "Table", (
                "table_cell_content_mismatch"
                if in_table_cell
                else "table_topology_mismatch"
            )
        if context == "RichText":
            return "RichText", "rich_text_effective_style_mismatch"
        if context == "List":
            return "List", "list_marker_mismatch"
        if context == "Tag":
            return "Tag", "tag_state_mismatch"
        if context == "Outline":
            return "Outline", "outline_structure_mismatch"
        return "Unknown", "semantic_mismatch_unclassified"

    def compare(
        left: Any,
        right: Any,
        path: str,
        *,
        context: str = "Unknown",
        table_ordinal: int | None = None,
        column_ordinal: int | None = None,
        in_table_cell: bool = False,
    ) -> None:
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            next_table = table_ordinal
            next_column = column_ordinal
            if "table_ordinal" in left:
                next_table = int(left["table_ordinal"])
                context = "Table"
                if not left.get("columns") or not right.get("columns"):
                    add_failure(
                        "table_column_mapping_unavailable",
                        "Table",
                        f"{path}.columns",
                        component_type="Column",
                        table_ordinal=next_table,
                    )
            if "column_ordinal" in left:
                next_column = int(left["column_ordinal"])
                context = "Column"
            if (
                context == "Column"
                and path.endswith(".attributes")
                and "width" not in left
                and "width" not in right
            ):
                add_failure(
                    "table_column_width_invalid",
                    "Table",
                    f"{path}.width",
                    component_type="Column",
                    field="width",
                    table_ordinal=next_table,
                    column_ordinal=next_column,
                    comparison="relative_tolerance"
                    if table_column_width_relative_tolerance is not None
                    else "exact",
                    **(
                        {
                            "allowed_relative_delta": float(
                                table_column_width_relative_tolerance
                            )
                        }
                        if table_column_width_relative_tolerance is not None
                        else {}
                    ),
                )
            left_keys = set(left) - {"table_ordinal", "column_ordinal", "outline_ordinal"}
            right_keys = set(right) - {"table_ordinal", "column_ordinal", "outline_ordinal"}
            for key in sorted(left_keys | right_keys, key=str):
                child_path = f"{path}.{key}"
                child_context = context
                child_in_cell = in_table_cell
                if key == "outlines":
                    child_context = "Outline"
                elif key in {"rich_text"}:
                    child_context = "RichText"
                elif key in {"list", "lists"}:
                    child_context = "List"
                elif key == "tags":
                    child_context = "Tag"
                elif key == "tables":
                    child_context = "Table"
                elif key == "rows":
                    child_context = "Row"
                elif key == "cells":
                    child_context = "Cell"
                    child_in_cell = True
                elif key == "columns":
                    child_context = "Column"
                if key not in left or key not in right:
                    if context == "Column" and key == "width":
                        add_failure(
                            "table_column_width_invalid",
                            "Table",
                            child_path,
                            component_type="Column",
                            field="width",
                            table_ordinal=next_table,
                            column_ordinal=next_column,
                            comparison="relative_tolerance"
                            if table_column_width_relative_tolerance is not None
                            else "exact",
                        )
                    else:
                        object_type, code = classification(
                            child_path, child_context, in_table_cell=child_in_cell
                        )
                        add_failure(code, object_type, child_path)
                    continue
                if context == "Column" and key == "width":
                    expected_width = _positive_finite_decimal(left[key])
                    actual_width = _positive_finite_decimal(right[key])
                    if expected_width is None or actual_width is None:
                        add_failure(
                            "table_column_width_invalid",
                            "Table",
                            child_path,
                            component_type="Column",
                            field="width",
                            table_ordinal=next_table,
                            column_ordinal=next_column,
                            comparison="relative_tolerance"
                            if table_column_width_relative_tolerance is not None
                            else "exact",
                            **(
                                {
                                    "allowed_relative_delta": float(
                                        table_column_width_relative_tolerance
                                    )
                                }
                                if table_column_width_relative_tolerance is not None
                                else {}
                            ),
                        )
                        continue
                    if table_column_width_relative_tolerance is None:
                        if left[key] == right[key]:
                            continue
                        add_failure(
                            "table_column_width_mismatch",
                            "Table",
                            child_path,
                            component_type="Column",
                            field="width",
                            table_ordinal=next_table,
                            column_ordinal=next_column,
                            comparison="exact",
                        )
                        continue
                    if left[key] == right[key]:
                        continue
                    relative_delta = abs(actual_width - expected_width) / abs(
                        expected_width
                    )
                    width_evidence = {
                        "content_object_type": "Table",
                        "component_type": "Column",
                        "field": "width",
                        "table_ordinal": next_table,
                        "column_ordinal": next_column,
                        "comparison": "relative_tolerance",
                        "allowed_relative_delta": float(
                            table_column_width_relative_tolerance
                        ),
                        "observed_relative_delta": float(relative_delta),
                        "passed": relative_delta
                        <= table_column_width_relative_tolerance,
                        "content_exposed": False,
                    }
                    width_comparisons.append(width_evidence)
                    if not width_evidence["passed"]:
                        add_failure(
                            "table_column_width_out_of_tolerance",
                            "Table",
                            child_path,
                            **{
                                key: value
                                for key, value in width_evidence.items()
                                if key not in {"content_object_type", "passed", "content_exposed"}
                            },
                        )
                    continue
                compare(
                    left[key],
                    right[key],
                    child_path,
                    context=child_context,
                    table_ordinal=next_table,
                    column_ordinal=next_column,
                    in_table_cell=child_in_cell,
                )
            return
        if isinstance(left, (tuple, list)) and isinstance(right, (tuple, list)):
            if len(left) != len(right):
                object_type, code = classification(
                    path, context, in_table_cell=in_table_cell
                )
                add_failure(
                    code,
                    object_type,
                    path,
                    source_count=len(left),
                    target_count=len(right),
                    **(
                        {"table_ordinal": table_ordinal}
                        if table_ordinal is not None
                        else {}
                    ),
                )
            for index, (left_item, right_item) in enumerate(
                zip(left, right, strict=False)
            ):
                compare(
                    left_item,
                    right_item,
                    f"{path}[{index}]",
                    context=context,
                    table_ordinal=table_ordinal,
                    column_ordinal=index if context == "Column" else column_ordinal,
                    in_table_cell=in_table_cell,
                )
            return
        if type(left) is not type(right) or left != right:
            object_type, code = classification(
                path, context, in_table_cell=in_table_cell
            )
            add_failure(
                code,
                object_type,
                path,
                **(
                    {"table_ordinal": table_ordinal}
                    if table_ordinal is not None
                    else {}
                ),
                **(
                    {"column_ordinal": column_ordinal}
                    if column_ordinal is not None
                    else {}
                ),
            )

    if not source.get("complete") or not target.get("complete"):
        add_failure(
            "semantic_projection_incomplete",
            "Unknown",
            "$.complete",
            source_complete=bool(source.get("complete")),
            target_complete=bool(target.get("complete")),
        )
    compare(source.get("title"), target.get("title"), "$.title")
    compare(
        source.get("outlines", ()),
        target.get("outlines", ()),
        "$.outlines",
        context="Outline",
    )
    compare(
        source.get("object_counts", {}),
        target.get("object_counts", {}),
        "$.object_counts",
    )
    compare(
        source.get("binary_sha256", ()),
        target.get("binary_sha256", ()),
        "$.binary_sha256",
    )
    failures.sort(
        key=lambda failure: (
            str(failure.get("path", "")),
            str(failure.get("content_object_type", "")),
            str(failure.get("code", "")),
        )
    )
    return {
        "failed_content_object_types": sorted(failed_types),
        "content_object_failures": failures,
        "content_object_failure_summary": {
            "limit": CONTENT_OBJECT_FAILURE_LIMIT,
            "reported": len(failures),
            "truncated": total_failures > len(failures),
            "total": total_failures,
        },
        "table_column_width_comparisons": width_comparisons[
            :CONTENT_OBJECT_FAILURE_LIMIT
        ],
    }


def semantic_content_comparison(
    expected_xml: str,
    actual_xml: str,
    *,
    table_column_width_relative_tolerance: Decimal | None = None,
) -> dict[str, Any]:
    source = semantic_content_projection(expected_xml)
    target = semantic_content_projection(actual_xml)
    typed = _semantic_content_typed_comparison(
        source,
        target,
        table_column_width_relative_tolerance=table_column_width_relative_tolerance,
    )
    outline_failure_types = {"RichText", "List", "Tag", "Table", "Outline", "Unknown"}
    checks = {
        "title": source["title"] == target["title"],
        "rich_list_tag_table_outline": not any(
            failure["content_object_type"] in outline_failure_types
            and not failure["path"].startswith("$.object_counts")
            and not failure["path"].startswith("$.binary_sha256")
            for failure in typed["content_object_failures"]
        )
        and not typed["content_object_failure_summary"]["truncated"],
        "binary_objects": (
            source["object_counts"] == target["object_counts"]
            and source["binary_sha256"] == target["binary_sha256"]
        ),
    }
    return {
        "source_complete": bool(source["complete"]),
        "target_complete": bool(target["complete"]),
        "checks": checks,
        "passed": bool(source["complete"])
        and bool(target["complete"])
        and all(checks.values()),
        "projection_evidence": {
            "schema_version": 1,
            "source": _semantic_projection_summary(source),
            "target": _semantic_projection_summary(target),
            "mismatches": _semantic_projection_mismatches(source, target),
            "content_exposed": False,
        },
        **typed,
    }


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
    if {"List", "Tag"}.intersection(observed) and observed.issubset(
        SEMANTIC_LIST_TAG_PAGE_TYPES
    ):
        return SEMANTIC_LIST_TAG_VERIFICATION
    if (
        observed.intersection({"RichText", "List", "Tag", "Table", "Image"})
        and observed.issubset(SEMANTIC_CONTENT_PAGE_TYPES)
        and page_xml is not None
        and semantic_content_projection(page_xml)["complete"] is True
    ):
        return SEMANTIC_CONTENT_VERIFICATION
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


def _generic_page_equivalence_failures(
    expected_xml: str,
    actual_xml: str,
    *,
    verification_tier: str,
    checks: Mapping[str, bool],
    display_equation_comparison: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    failed_types: set[str] = set()
    total = 0

    def add(code: str, content_object_type: str, path: str, **details: Any) -> None:
        nonlocal total
        total += 1
        failed_types.add(content_object_type)
        if len(failures) < CONTENT_OBJECT_FAILURE_LIMIT:
            failures.append(
                {
                    "code": code,
                    "content_object_type": content_object_type,
                    "path": path,
                    **details,
                    "content_exposed": False,
                }
            )

    source_title = semantic_content_projection(expected_xml)["title"]
    target_title = semantic_content_projection(actual_xml)["title"]
    if source_title != target_title:
        add("page_title_mismatch", "PageTitle", "$.title")

    expected_counts = page_content_type_counts(expected_xml)
    actual_counts = page_content_type_counts(actual_xml)
    for object_type in sorted(set(expected_counts) | set(actual_counts)):
        if expected_counts.get(object_type, 0) != actual_counts.get(object_type, 0):
            add(
                f"{object_type.casefold()}_object_count_mismatch",
                object_type,
                f"$.content_objects.{object_type}",
                source_count=expected_counts.get(object_type, 0),
                target_count=actual_counts.get(object_type, 0),
            )

    if checks.get("binary_sha256") is False:
        binary_types = sorted(
            (set(expected_counts) | set(actual_counts))
            & {"Image", "FileAttachment", "InsertedFile", "MediaFile"}
        ) or ["Unknown"]
        for object_type in binary_types:
            add(
                "image_binary_mismatch"
                if object_type == "Image"
                else "binary_object_mismatch",
                object_type,
                "$.binary_sha256",
            )

    if verification_tier == SEMANTIC_LIST_TAG_VERIFICATION and checks.get(
        "semantic_list_tag"
    ) is False:
        expected = semantic_list_tag_projection(expected_xml)
        actual = semantic_list_tag_projection(actual_xml)
        if len(expected) != len(actual):
            if any(item.get("list_kind") is not None for item in [*expected, *actual]):
                add("list_marker_mismatch", "List", "$.semantic_list_tag")
            if any(item.get("tag") is not None for item in [*expected, *actual]):
                add("tag_state_mismatch", "Tag", "$.semantic_list_tag")
        for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
            if left.get("list_kind") != right.get("list_kind"):
                add("list_marker_mismatch", "List", f"$.semantic_list_tag[{index}].list")
            if left.get("tag") != right.get("tag"):
                add("tag_state_mismatch", "Tag", f"$.semantic_list_tag[{index}].tag")
            if left.get("text") != right.get("text"):
                add(
                    "rich_text_visible_text_mismatch",
                    "RichText",
                    f"$.semantic_list_tag[{index}].text",
                )
    elif verification_tier in {
        SEMANTIC_MATHML_VERIFICATION,
        SEMANTIC_DISPLAY_EQUATION_VERIFICATION,
    } and any(
        checks.get(name) is False
        for name in (
            "semantic_mathml",
            "display_equation_com_normalization",
            "outside_mathml_canonical",
        )
    ):
        outside_mismatch = (
            display_equation_comparison.get("outside_mathml_mismatch")
            if display_equation_comparison is not None
            else None
        )
        outside_path = (
            str(outside_mismatch.get("path", ""))
            if isinstance(outside_mismatch, Mapping)
            else ""
        )
        if "/Title[" in outside_path:
            details = {
                key: outside_mismatch[key]
                for key in (
                    "field",
                    "expected_attribute_names",
                    "actual_attribute_names",
                    "differing_attribute_names",
                )
                if key in outside_mismatch
            }
            add(
                "page_title_structure_mismatch",
                "PageTitle",
                "$.title.structure",
                **details,
            )
        else:
            add("display_equation_semantic_mismatch", "DisplayEquation", "$.mathml")
    elif verification_tier == SEMANTIC_INK_DRAWING_VERIFICATION and checks.get(
        verification_tier
    ) is False:
        add("ink_drawing_semantic_mismatch", "InkDrawing", "$.ink")
    elif verification_tier == SEMANTIC_UI_SHAPE_VERIFICATION and checks.get(
        verification_tier
    ) is False:
        add("ui_shape_semantic_mismatch", "UIShape", "$.ink")

    if checks.get("visible_text") is False and not any(
        failure["content_object_type"] in {"PageTitle", "RichText"}
        for failure in failures
    ):
        add("rich_text_visible_text_mismatch", "RichText", "$.visible_text")
    if checks.get("canonical_xml") is False and not failures:
        add("semantic_mismatch_unclassified", "Unknown", "$.canonical_xml")

    failures.sort(
        key=lambda failure: (
            str(failure.get("path", "")),
            str(failure.get("content_object_type", "")),
            str(failure.get("code", "")),
        )
    )
    return {
        "failed_content_object_types": sorted(failed_types),
        "content_object_failures": failures,
        "content_object_failure_summary": {
            "limit": CONTENT_OBJECT_FAILURE_LIMIT,
            "reported": len(failures),
            "truncated": total > len(failures),
            "total": total,
        },
    }


def page_equivalence(
    expected_xml: str,
    actual_xml: str,
    *,
    verification_tier: str = STRICT_CANONICAL_VERIFICATION,
) -> dict[str, Any]:
    """Return the stable content checks used by Copy and Page Move."""

    display_equation_comparison: dict[str, Any] | None = None
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
    elif verification_tier == SEMANTIC_CONTENT_VERIFICATION:
        semantic_content = semantic_content_comparison(
            expected_xml,
            actual_xml,
            table_column_width_relative_tolerance=(
                TABLE_COLUMN_WIDTH_RELATIVE_TOLERANCE
            ),
        )
        checks["semantic_content"] = semantic_content["passed"]
        checks["semantic_projection_complete"] = (
            semantic_content["source_complete"]
            and semantic_content["target_complete"]
        )
        if checks["semantic_projection_complete"]:
            acceptance_checks = [
                "binary_sha256",
                "semantic_content",
                "semantic_projection_complete",
            ]
        else:
            checks["semantic_fallback_strict"] = all(
                checks[name]
                for name in (
                    "canonical_xml",
                    "visible_text",
                    "content_objects",
                    "binary_sha256",
                )
            )
            acceptance_checks = ["semantic_fallback_strict"]
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
    if result["equivalent"]:
        typed_failures = {
            "failed_content_object_types": [],
            "content_object_failures": [],
            "content_object_failure_summary": {
                "limit": CONTENT_OBJECT_FAILURE_LIMIT,
                "reported": 0,
                "truncated": False,
                "total": 0,
            },
        }
    elif verification_tier == SEMANTIC_CONTENT_VERIFICATION:
        typed_failures = {
            key: semantic_content[key]
            for key in (
                "failed_content_object_types",
                "content_object_failures",
                "content_object_failure_summary",
            )
        }
    else:
        typed_failures = _generic_page_equivalence_failures(
            expected_xml,
            actual_xml,
            verification_tier=verification_tier,
            checks=checks,
            display_equation_comparison=display_equation_comparison,
        )
    result.update(typed_failures)
    if verification_tier in {
        SEMANTIC_INK_DRAWING_VERIFICATION,
        SEMANTIC_UI_SHAPE_VERIFICATION,
    }:
        result["ink_projection_comparison"] = ink_comparison
    elif verification_tier == SEMANTIC_MATHML_VERIFICATION:
        result["mathml_projection_comparison"] = mathml_comparison
    elif verification_tier == SEMANTIC_DISPLAY_EQUATION_VERIFICATION:
        result["display_equation_comparison"] = display_equation_comparison
    elif verification_tier == SEMANTIC_CONTENT_VERIFICATION:
        result["semantic_content_comparison"] = semantic_content
    return result


def _visible_text_in_title_region(xml: str, *, inside_title: bool) -> str:
    """Return visible text from one Page Title region without XML comparison."""

    root = parse_xml(xml)
    texts: list[str] = []

    def collect(node: ET.Element, *, in_title: bool = False) -> None:
        node_is_in_title = in_title or local_name(node.tag) == "Title"
        if (
            local_name(node.tag) == "T"
            and node.text
            and node_is_in_title == inside_title
        ):
            texts.append(html_fragment_to_text(node.text))
        for child in list(node):
            collect(child, in_title=node_is_in_title)

    collect(root)
    return "\n\n".join(text for text in texts if text).strip()


def page_visible_text_equivalence(expected_xml: str, actual_xml: str) -> dict[str, Any]:
    """Compare only the visible Page text after a destination-title rewrite.

    A Copy whose root title is subsequently updated through ``rename_page`` has
    no full XML/content-object equivalence contract. This intentionally accepts
    only the text projection required for that narrowed path.
    """

    visible_text = text_from_page_xml(expected_xml) == text_from_page_xml(actual_xml)
    expected_title = _visible_text_in_title_region(expected_xml, inside_title=True)
    actual_title = _visible_text_in_title_region(actual_xml, inside_title=True)
    failures: list[dict[str, Any]] = []

    if not visible_text:
        if expected_title != actual_title:
            failures.append(
                {
                    "code": "page_title_mismatch",
                    "content_object_type": "PageTitle",
                    "path": "$.title",
                    "content_exposed": False,
                }
            )
        if _visible_text_in_title_region(
            expected_xml, inside_title=False
        ) != _visible_text_in_title_region(actual_xml, inside_title=False):
            failures.append(
                {
                    "code": "rich_text_visible_text_mismatch",
                    "content_object_type": "RichText",
                    "path": "$.visible_text",
                    "content_exposed": False,
                }
            )
        if not failures:
            failures.append(
                {
                    "code": "rich_text_visible_text_mismatch",
                    "content_object_type": "RichText",
                    "path": "$.visible_text",
                    "content_exposed": False,
                }
            )

    failed_types = sorted(
        {str(failure["content_object_type"]) for failure in failures}
    )
    return {
        "equivalent": visible_text,
        "verification_tier": "visible_text_projection",
        "acceptance_checks": ["visible_text"],
        "checks": {"visible_text": visible_text},
        "failed_content_object_types": failed_types,
        "content_object_failures": failures,
        "content_object_failure_summary": {
            "limit": CONTENT_OBJECT_FAILURE_LIMIT,
            "reported": len(failures),
            "truncated": False,
            "total": len(failures),
        },
    }
