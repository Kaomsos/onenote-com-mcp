"""MCP server exposing a pure-local Microsoft OneNote control surface."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .bridge import OneNoteBridge, OneNoteBridgeError
from .constants import (
    CREATE_FILE_TYPES,
    FILING_LOCATION_TYPES,
    FILING_LOCATIONS,
    HIERARCHY_SCOPES,
    NEW_PAGE_STYLES,
    ONE_NS,
    PAGE_INFO,
    PUBLISH_FORMATS,
    SPECIAL_LOCATIONS,
    XML_SCHEMA_2013,
)
from .image_utils import proportional_dimensions
from .domain import content_objects
from .hierarchy import (
    display_name,
    filter_resources,
    find_resource_by_id,
    find_resource_by_path,
    parse_hierarchy,
    resolve_resource,
)
from .policy import MutationPolicy, SearchBudget, env_bool
from .xml_utils import (
    build_image_page_update_xml,
    build_page_update_xml,
    collect_page_objects,
    DELETABLE_PAGE_OBJECT_TYPES,
    text_from_page_xml,
    title_from_page_xml,
)


MCP_NAME = "local-onenote"
DEFAULT_TIMEOUT = int(os.environ.get("LOCAL_ONENOTE_MCP_TIMEOUT", "90"))
MAX_TEXT_CHARS = int(os.environ.get("LOCAL_ONENOTE_MCP_MAX_TEXT_CHARS", "60000"))

mcp = FastMCP(MCP_NAME)
bridge = OneNoteBridge(timeout_seconds=DEFAULT_TIMEOUT)


def _error(message: str, code: str = "operation_failed", **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code, "complete": False, **details}


def _ok(**data: Any) -> dict[str, Any]:
    return {"ok": True, "complete": True, "warnings": [], **data}


def _caught(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, PermissionError):
        code = "policy_disabled"
    elif isinstance(exc, ValueError):
        code = "validation_error"
    else:
        code = "backend_error"
    return _error(str(exc), code)


def _enum(name: str, value: str, options: dict[str, int]) -> int:
    key = value.casefold()
    if key not in options:
        allowed = ", ".join(sorted(options))
        raise ValueError(f"{name} must be one of: {allowed}")
    return options[key]


def _bridge(operation: str, **params: Any) -> dict[str, Any]:
    try:
        return bridge.call(operation, **params)
    except OneNoteBridgeError as exc:
        raise RuntimeError(str(exc)) from exc


def _hierarchy_xml(start_id: str = "", scope: str = "pages") -> str:
    return _bridge(
        "get_hierarchy",
        start_id=start_id,
        scope=_enum("scope", scope, HIERARCHY_SCOPES),
        schema=XML_SCHEMA_2013,
    )["xml"]


def _domain_items(include_recycle_bin: bool = False) -> list[dict[str, Any]]:
    items = parse_hierarchy(_hierarchy_xml("", "pages"))
    return items if include_recycle_bin else [item for item in items if not item["is_in_recycle_bin"]]


def _domain_item(object_id: str, resource_type: str | None = None) -> dict[str, Any]:
    if not object_id:
        raise ValueError("An object ID is required.")
    item = find_resource_by_id(_domain_items(include_recycle_bin=True), object_id, resource_type)
    if item is None:
        label = resource_type or "object"
        raise ValueError(f"No {label} found for ID '{object_id}'.")
    return item


def _item_name(item: dict[str, Any]) -> str:
    return display_name(item)


def _confirm_item(
    object_id: str,
    resource_type: str,
    *,
    expected_name: str,
    expected_parent_id: str | None,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    item = _domain_item(object_id, resource_type)
    actual_name = _item_name(item)
    if actual_name != expected_name:
        raise ValueError(f"Confirmation mismatch: expected name '{expected_name}', found '{actual_name}'.")
    if item["parent_id"] != expected_parent_id:
        raise ValueError(
            f"Confirmation mismatch: expected parent_id '{expected_parent_id}', found '{item['parent_id']}'."
        )
    if expected_modified is not None and item.get("modified") != expected_modified:
        raise ValueError(
            f"Confirmation mismatch: expected modified '{expected_modified}', found '{item.get('modified')}'."
        )
    return item


def _confirm_page(
    page_id: str,
    *,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    item = _domain_item(page_id, "page")
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


def _resolve_resource(identifier: str, resource_type: str | None = None) -> dict[str, Any]:
    return resolve_resource(_domain_items(include_recycle_bin=True), identifier, resource_type)


def _find_resource_path(path: str, resource_type: str | None = None) -> dict[str, Any] | None:
    return find_resource_by_path(_domain_items(include_recycle_bin=True), path, resource_type)


def _friendly_child_path(parent_path: str, child_name: str) -> str:
    normalized = child_name.replace("\\", "/").strip("/")
    if normalized.lower().endswith(".one"):
        normalized = normalized[:-4]
    return f"{parent_path}/{normalized}" if normalized else parent_path


def _wait_domain_item(
    object_id: str,
    resource_type: str,
    *,
    predicate: Any | None = None,
    retries: int = 8,
    delay_seconds: float = 0.5,
) -> dict[str, Any] | None:
    for attempt in range(retries):
        try:
            item = _domain_item(object_id, resource_type)
        except ValueError:
            item = None
        if item is not None and (predicate is None or predicate(item)):
            return item
        if attempt + 1 < retries:
            time.sleep(delay_seconds)
    return None


def _wait_created_domain_item(
    expected_path: str,
    resource_type: str,
    fallback_id: str,
    *,
    retries: int = 8,
    delay_seconds: float = 0.5,
) -> dict[str, Any] | None:
    for attempt in range(retries):
        items = _domain_items(include_recycle_bin=True)
        item = next(
            (
                candidate
                for candidate in items
                if candidate["resource_type"] == resource_type
                and (candidate["path"].casefold() == expected_path.casefold() or candidate["id"] == fallback_id)
            ),
            None,
        )
        if item:
            return item
        if attempt + 1 < retries:
            time.sleep(delay_seconds)
    return None


def _hierarchy_update_xml(item: dict[str, Any], **attributes: str) -> str:
    """Build a minimal UpdateHierarchy document rooted at an item's ancestors."""

    all_items = _domain_items(include_recycle_bin=True)
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
    tags = {
        "notebook": "Notebook",
        "section_group": "SectionGroup",
        "section": "Section",
        "page": "Page",
    }
    for candidate in chain:
        attrs = {"ID": candidate["id"], "name": _item_name(candidate)}
        if candidate is item:
            attrs.update(attributes)
        current = ET.SubElement(current, f"{{{ONE_NS}}}{tags[candidate['resource_type']]}", attrs)
    return ET.tostring(root, encoding="unicode")


def _section_move_xml(section: dict[str, Any], destination: dict[str, Any]) -> str:
    """Build the target-parent form used by OneNote UpdateHierarchy for a Section move."""

    all_items = _domain_items(include_recycle_bin=True)
    by_id = {candidate["id"]: candidate for candidate in all_items}
    chain = [destination]
    parent_id = destination.get("parent_id")
    while parent_id:
        parent = by_id.get(parent_id)
        if parent is None:
            raise RuntimeError(f"Cannot build move update: missing ancestor {parent_id}.")
        chain.append(parent)
        parent_id = parent.get("parent_id")
    chain.reverse()
    root = ET.Element(f"{{{ONE_NS}}}Notebooks")
    current = root
    tags = {"notebook": "Notebook", "section_group": "SectionGroup"}
    for candidate in chain:
        current = ET.SubElement(
            current,
            f"{{{ONE_NS}}}{tags[candidate['resource_type']]}",
            {"ID": candidate["id"], "name": _item_name(candidate)},
        )
    ET.SubElement(current, f"{{{ONE_NS}}}Section", {"ID": section["id"], "name": _item_name(section)})
    return ET.tostring(root, encoding="unicode")


def _page_order_update_xml(section: dict[str, Any], pages: list[dict[str, Any]]) -> str:
    """Build a complete ordered Page sequence for one Section."""

    root_xml = _hierarchy_update_xml(section)
    root = ET.fromstring(root_xml)
    section_node = next(node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Section")
    for page in pages:
        ET.SubElement(
            section_node,
            f"{{{ONE_NS}}}Page",
            {"ID": page["id"], "name": _item_name(page), "pageLevel": str(page["page_level"])},
        )
    return ET.tostring(root, encoding="unicode")


def _create_type_to_item_type(create_type: str) -> str | None:
    key = create_type.casefold()
    if key == "section":
        return "section"
    if key in {"folder", "section_group"}:
        return "section_group"
    if key == "notebook":
        return "notebook"
    return None


def _safe_leaf_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        raise ValueError("Name cannot be empty.")
    return cleaned


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[truncated: {len(text) - max_chars} chars omitted]"


def _without_recycle_bin(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if not _is_recycle_bin_item(item)]


def _is_recycle_bin_item(item: dict[str, Any]) -> bool:
    return item.get("is_in_recycle_bin") is True


def _page_xml(page_id: str, page_info: str = "basic") -> str:
    return _bridge(
        "get_page_content",
        page_id=page_id,
        page_info=_enum("page_info", page_info, PAGE_INFO),
        schema=XML_SCHEMA_2013,
    )["xml"]


def _page_content_digest(xml: str) -> str:
    """Hash Page content while ignoring hierarchy/clock metadata that may change on move."""

    root = ET.fromstring(xml)
    for attribute in ("ID", "name", "dateTime", "lastModifiedTime", "pageLevel", "isCurrentlyViewed"):
        root.attrib.pop(attribute, None)
    canonical = ET.tostring(root, encoding="utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _local_text_search(
    start_id: str,
    query: str,
    max_results: int,
    include_recycle_bin: bool,
    budget: SearchBudget | None = None,
    include_snippets: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    budget = budget or SearchBudget.current()
    items = _domain_items(include_recycle_bin)
    pages = filter_resources(items, "page")
    if start_id:
        root = find_resource_by_id(items, start_id)
        if root is None:
            raise ValueError(f"No search scope found for ID '{start_id}'.")
        prefix = root["path"] + "/"
        pages = [page for page in pages if page["id"] == start_id or page["path"].startswith(prefix)]
    if len(pages) > budget.max_pages:
        raise ValueError(
            f"Search scope contains {len(pages)} candidate pages, exceeding LOCAL_ONENOTE_MAX_SEARCH_PAGES={budget.max_pages}."
        )
    query_lower = query.casefold()
    matches = []
    total_chars = 0
    scanned_pages = 0
    started = time.monotonic()
    for page in pages:
        if len(matches) >= max(1, max_results):
            break
        if time.monotonic() - started > budget.max_seconds:
            raise RuntimeError(f"Local search exceeded its {budget.max_seconds}-second budget.")
        haystacks = [display_name(page), page.get("path", "")]
        try:
            page_text = text_from_page_xml(_page_xml(page["id"], "basic"))
            scanned_pages += 1
            if len(page_text) > budget.max_page_chars:
                page_text = page_text[: budget.max_page_chars]
            total_chars += len(page_text)
            if total_chars > budget.max_total_chars:
                raise RuntimeError(
                    f"Local search exceeded LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS={budget.max_total_chars}."
                )
            haystacks.append(page_text)
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            page["scan_error"] = str(exc)
        if any(query_lower in value.casefold() for value in haystacks if value):
            if include_snippets and len(haystacks) > 2:
                text = haystacks[-1]
                index = text.casefold().find(query_lower)
                if index >= 0:
                    radius = max(40, budget.snippet_chars // 2)
                    page["snippet"] = text[max(0, index - radius) : index + len(query) + radius].strip()
            matches.append(page)
    return matches, {
        "candidate_pages": len(pages),
        "scanned_pages": scanned_pages,
        "scanned_chars": total_chars,
        "max_pages": budget.max_pages,
        "max_page_chars": budget.max_page_chars,
        "max_total_chars": budget.max_total_chars,
        "max_seconds": budget.max_seconds,
    }


REPLACE_BODY_OBJECT_TYPES = {"Outline", "Image", "InkDrawing", "FileAttachment", "InsertedFile", "MediaFile"}
IDENTIFIER_RESOLUTION_ORDER = ["id", "exact_path", "unique_name"]
IDENTIFIER_TYPES = {"notebook", "section_group", "section", "page"}


def _advanced_tool(function: Any) -> Any:
    """Register raw mutation tools only in an explicit development profile."""

    return mcp.tool()(function) if env_bool("LOCAL_ONENOTE_ENABLE_RAW_XML") else function


@mcp.tool()
async def health_check() -> dict[str, Any]:
    """Verify local OneNote COM access and return a small hierarchy summary."""

    try:
        items = _domain_items(include_recycle_bin=False)
        notebooks = filter_resources(items, "notebook")
        sections = filter_resources(items, "section")
        policy = MutationPolicy.current()
        search_budget = SearchBudget.current()
        return _ok(
            server=MCP_NAME,
            transport="stdio",
            python_executable=sys.executable,
            module_path=str(Path(__file__).resolve()),
            process_cwd=str(Path.cwd()),
            timeout_seconds=DEFAULT_TIMEOUT,
            max_text_chars=MAX_TEXT_CHARS,
            identifier_resolution_order=IDENTIFIER_RESOLUTION_ORDER,
            search_default_backend="local_scan",
            content_formats=["plain", "html", "markdown"],
            mutation_policy={
                "writes_enabled": policy.writes_enabled,
                "deletes_enabled": policy.deletes_enabled,
                "permanent_deletes_enabled": policy.permanent_deletes_enabled,
                "experimental_move_section_enabled": policy.experimental_move_section_enabled,
                "raw_xml_enabled": policy.raw_xml_enabled,
            },
            search_budget={
                "max_pages": search_budget.max_pages,
                "max_page_chars": search_budget.max_page_chars,
                "max_total_chars": search_budget.max_total_chars,
                "max_seconds": search_budget.max_seconds,
            },
            notebooks=len(notebooks),
            sections=len(sections),
            write_backend="OneNote desktop COM API",
        )
    except Exception as exc:
        return _error(str(exc))


@mcp.tool()
async def resolve_identifier(identifier: str, item_type: str = "") -> dict[str, Any]:
    """Resolve a OneNote identifier to one live object before using it in another tool."""

    try:
        if not identifier:
            raise ValueError("identifier is required.")
        normalized_type = item_type.strip().casefold() or None
        if normalized_type and normalized_type not in IDENTIFIER_TYPES:
            allowed = ", ".join(sorted(IDENTIFIER_TYPES))
            raise ValueError(f"item_type must be empty or one of: {allowed}")
        item = _resolve_resource(identifier, normalized_type)
        return _ok(item=item, identifier_resolution_order=IDENTIFIER_RESOLUTION_ORDER)
    except Exception as exc:
        return _error(str(exc))


@mcp.tool()
async def get_special_locations() -> dict[str, Any]:
    """Return OneNote's local special folders: backup, unfiled, and default notebook folder."""

    try:
        locations = {}
        for name, value in SPECIAL_LOCATIONS.items():
            locations[name] = _bridge("get_special_location", location=value)["path"]
        return _ok(locations=locations)
    except Exception as exc:
        return _error(str(exc))


@mcp.tool()
async def list_hierarchy(
    start_identifier: str = "",
    scope: str = "pages",
    include_xml: bool = False,
    include_recycle_bin: bool = False,
) -> dict[str, Any]:
    """List live OneNote hierarchy objects. Identifiers may be an ID, exact path, or unique name."""

    try:
        xml = _hierarchy_xml("", "pages")
        items = parse_hierarchy(xml)
        if not include_recycle_bin:
            items = [item for item in items if not item["is_in_recycle_bin"]]
        root = None
        if start_identifier:
            root = resolve_resource(items, start_identifier)
            items = [item for item in items if item["id"] == root["id"] or item["path"].startswith(root["path"] + "/")]
        scope_types = {
            "self": set(),
            "children": IDENTIFIER_TYPES,
            "notebooks": {"notebook"},
            "sections": {"notebook", "section_group", "section"},
            "pages": IDENTIFIER_TYPES,
        }
        if scope not in scope_types:
            _enum("scope", scope, HIERARCHY_SCOPES)
        if scope == "self":
            items = [root] if root else []
        elif scope == "children":
            items = [
                item
                for item in items
                if item["parent_id"] == (root["id"] if root else None)
            ]
        else:
            items = [item for item in items if item["resource_type"] in scope_types[scope]]
        data = _ok(items=items, count=len(items))
        if include_xml:
            data["xml"] = xml
        return data
    except Exception as exc:
        return _error(str(exc))


@mcp.tool()
async def list_notebooks(include_recycle_bin: bool = False) -> dict[str, Any]:
    """List live notebooks currently known to the local OneNote desktop app."""

    try:
        notebooks = filter_resources(_domain_items(include_recycle_bin), "notebook")
        return _ok(notebooks=notebooks, count=len(notebooks))
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def get_notebook(notebook_id: str) -> dict[str, Any]:
    """Get stable metadata for one notebook by COM object ID."""

    try:
        return _ok(item=_domain_item(notebook_id, "notebook"))
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def list_section_groups(parent_id: str = "", recursive: bool = True, include_recycle_bin: bool = False) -> dict[str, Any]:
    """List section groups, optionally below one notebook or section-group ID."""

    try:
        items = _domain_items(include_recycle_bin)
        groups = filter_resources(items, "section_group")
        if parent_id:
            parent = next((item for item in items if item["id"] == parent_id), None)
            if not parent or parent["resource_type"] not in {"notebook", "section_group"}:
                raise ValueError("parent_id must identify a notebook or section_group.")
            if recursive:
                prefix = parent["path"] + "/"
                groups = [item for item in groups if item["path"].startswith(prefix)]
            else:
                groups = [item for item in groups if item["parent_id"] == parent_id]
        return _ok(items=groups, count=len(groups))
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def get_section_group(section_group_id: str) -> dict[str, Any]:
    """Get stable metadata for one section group by COM object ID."""

    try:
        return _ok(item=_domain_item(section_group_id, "section_group"))
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def list_sections(parent_id: str = "", recursive: bool = True, include_recycle_bin: bool = False) -> dict[str, Any]:
    """List sections, optionally below one notebook or section-group ID."""

    try:
        items = _domain_items(include_recycle_bin)
        sections = filter_resources(items, "section")
        if parent_id:
            parent = next((item for item in items if item["id"] == parent_id), None)
            if not parent or parent["resource_type"] not in {"notebook", "section_group"}:
                raise ValueError("parent_id must identify a notebook or section_group.")
            if recursive:
                prefix = parent["path"] + "/"
                sections = [item for item in sections if item["path"].startswith(prefix)]
            else:
                sections = [item for item in sections if item["parent_id"] == parent_id]
        return _ok(sections=sections, count=len(sections))
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def get_section(section_id: str) -> dict[str, Any]:
    """Get stable metadata for one section by COM object ID."""

    try:
        return _ok(item=_domain_item(section_id, "section"))
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def list_pages(section_id: str, include_recycle_bin: bool = False) -> dict[str, Any]:
    """List page metadata in one section selected by its COM object ID."""

    try:
        section = _domain_item(section_id, "section")
        pages = [
            item
            for item in _domain_items(include_recycle_bin)
            if item["resource_type"] == "page" and item["section_id"] == section_id
        ]
        return _ok(section=section, pages=pages, count=len(pages))
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def get_page(page_id: str) -> dict[str, Any]:
    """Get page metadata only; use the dedicated content tools for text, XML, objects, or binary."""

    try:
        return _ok(item=_domain_item(page_id, "page"))
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def get_page_xml(page_id: str, page_info: str = "basic") -> dict[str, Any]:
    """Return raw OneNote XML for a page."""

    try:
        _domain_item(page_id, "page")
        return _ok(xml=_page_xml(page_id, page_info))
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def get_page_text(page_id: str, max_chars: int = MAX_TEXT_CHARS) -> dict[str, Any]:
    """Return plain text extracted from a OneNote page."""

    try:
        _domain_item(page_id, "page")
        text = text_from_page_xml(_page_xml(page_id, "basic"))
        return _ok(text=_truncate(text, max_chars), chars=len(text))
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def get_page_objects(page_id: str) -> dict[str, Any]:
    """List page content objects such as outlines, images, attachments, and callback IDs."""

    try:
        _domain_item(page_id, "page")
        objects = content_objects(page_id, collect_page_objects(_page_xml(page_id, "all")))
        return _ok(objects=objects, count=len(objects))
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def get_binary_content(page_id: str, callback_id: str) -> dict[str, Any]:
    """Read binary page content by callback ID returned from get_page_objects."""

    try:
        _domain_item(page_id, "page")
        objects = content_objects(page_id, collect_page_objects(_page_xml(page_id, "all")))
        matched = next((item for item in objects if item["callback_id"] == callback_id), None)
        if not matched:
            raise ValueError("callback_id was not found in the current page object snapshot.")
        result = _bridge("get_binary_page_content", page_id=page_id, callback_id=callback_id)
        return _ok(object=matched, base64=result["base64"])
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def search_pages(
    query: str,
    scope_type: str,
    scope_id: str,
    backend: str = "local_scan",
    max_results: int = 20,
    include_snippets: bool = True,
    include_recycle_bin: bool = False,
) -> dict[str, Any]:
    """Search Page text in an explicit notebook, section-group, or section scope."""

    try:
        if not query.strip():
            raise ValueError("query is required.")
        normalized_scope = scope_type.strip().casefold()
        if normalized_scope not in {"notebook", "section_group", "section"}:
            raise ValueError("scope_type must be one of: notebook, section_group, section.")
        scope = _domain_item(scope_id, normalized_scope)
        normalized_backend = backend.strip().casefold()
        budget_data: dict[str, Any] | None = None
        if normalized_backend == "local_scan":
            pages, budget_data = _local_text_search(
                scope["id"],
                query,
                max_results,
                include_recycle_bin,
                include_snippets=include_snippets,
            )
        elif normalized_backend == "onenote_index":
            xml = _bridge(
                "find_pages",
                start_id=scope["id"],
                query=query,
                include_unindexed=False,
                display=False,
                schema=XML_SCHEMA_2013,
            )["xml"]
            catalog = _domain_items(include_recycle_bin=True)
            pages = filter_resources(parse_hierarchy(xml, catalog=catalog), "page")
        else:
            raise ValueError("backend must be one of: local_scan, onenote_index.")
        if not include_recycle_bin:
            pages = _without_recycle_bin(pages)
        pages = pages[: max(1, max_results)]
        if include_snippets and normalized_backend == "onenote_index":
            q = query.casefold()
            for page in pages:
                try:
                    text = text_from_page_xml(_page_xml(page["id"], "basic"))
                    idx = text.casefold().find(q)
                    if idx >= 0:
                        start = max(0, idx - 160)
                        end = min(len(text), idx + len(query) + SearchBudget.current().snippet_chars)
                        page["snippet"] = text[start:end].strip()
                except Exception as exc:
                    page["snippet_error"] = str(exc)
        return _ok(
            pages=pages,
            count=len(pages),
            scope=scope,
            search_backend=normalized_backend,
            scan_budget=budget_data,
        )
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def query_hierarchy(
    resource_type: str,
    name_equals: str = "",
    name_contains: str = "",
    parent_id: str = "",
    modified_after: str = "",
    modified_before: str = "",
    include_recycle_bin: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    """Query stable hierarchy metadata without reading Page content."""

    try:
        normalized_type = resource_type.strip().casefold()
        if normalized_type not in IDENTIFIER_TYPES:
            raise ValueError("resource_type must be one of: notebook, section_group, section, page.")
        items = filter_resources(_domain_items(include_recycle_bin), normalized_type)
        if name_equals:
            target = name_equals.casefold()
            items = [item for item in items if _item_name(item).casefold() == target]
        if name_contains:
            target = name_contains.casefold()
            items = [item for item in items if target in _item_name(item).casefold()]
        if parent_id:
            if normalized_type == "page":
                items = [item for item in items if item["section_id"] == parent_id or item["parent_page_id"] == parent_id]
            else:
                items = [item for item in items if item["parent_id"] == parent_id]
        if modified_after:
            items = [item for item in items if item.get("modified") and item["modified"] > modified_after]
        if modified_before:
            items = [item for item in items if item.get("modified") and item["modified"] < modified_before]
        bounded = items[: max(1, min(limit, 1000))]
        return _ok(items=bounded, count=len(bounded), total_matches=len(items), truncated=len(bounded) < len(items))
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def get_path(object_id: str) -> dict[str, Any]:
    """Get a display path and stable ancestor IDs for one hierarchy object."""

    try:
        items = _domain_items(include_recycle_bin=True)
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
        return _ok(item=item, path=item["path"], ancestors=ancestors)
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def get_tree(root_id: str, max_depth: int = 8, include_recycle_bin: bool = False) -> dict[str, Any]:
    """Get a typed hierarchy tree; Page children follow derived indentation relationships."""

    try:
        items = _domain_items(include_recycle_bin)
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

        return _ok(tree=build(root, 0))
    except Exception as exc:
        return _caught(exc)


@_advanced_tool
async def find_meta(start_identifier: str, name: str, include_unindexed: bool = True) -> dict[str, Any]:
    """Find pages or objects with matching OneNote meta name."""

    try:
        start_id = _resolve_resource(start_identifier)["id"] if start_identifier else ""
        xml = _bridge(
            "find_meta",
            start_id=start_id,
            name=name,
            include_unindexed=include_unindexed,
            schema=XML_SCHEMA_2013,
        )["xml"]
        items = parse_hierarchy(xml, catalog=_domain_items(include_recycle_bin=True))
        return _ok(items=items, count=len(items), xml=xml)
    except Exception as exc:
        return _error(str(exc))


@mcp.tool()
async def get_hyperlink(object_id: str, page_content_object_id: str = "", web: bool = False) -> dict[str, Any]:
    """Return a OneNote client or web hyperlink for an object."""

    try:
        item = _domain_item(object_id)
        operation = "get_web_hyperlink" if web else "get_hyperlink"
        result = _bridge(operation, object_id=object_id, page_content_object_id=page_content_object_id)
        return _ok(item=item, hyperlink=result["hyperlink"])
    except Exception as exc:
        return _error(str(exc))


@mcp.tool()
async def get_parent(object_id: str) -> dict[str, Any]:
    """Return stable metadata for an object's parent."""

    try:
        item = _domain_item(object_id)
        parent_id = _bridge("get_hierarchy_parent", object_id=object_id)["parent_id"]
        parent = _domain_item(parent_id) if parent_id else None
        return _ok(item=item, parent=parent, parent_id=parent_id)
    except Exception as exc:
        return _error(str(exc))


@_advanced_tool
async def open_hierarchy(path: str, relative_to_identifier: str = "", create_type: str = "none") -> dict[str, Any]:
    """Open or create a notebook, section group, or section. Existing OneNote hierarchy paths resolve directly."""

    try:
        normalized_create_type = create_type.strip().casefold() or "none"
        relative_to_id = ""
        expected_path = path.replace("\\", "/").strip("/")
        if relative_to_identifier:
            parent = _resolve_resource(relative_to_identifier)
            relative_to_id = parent["id"]
            expected_path = _friendly_child_path(parent["path"], path)

        if normalized_create_type == "none":
            existing = _find_resource_path(expected_path)
            if existing:
                return _ok(object_id=existing["id"], item=existing, opened_existing=True)
            if not relative_to_identifier:
                try:
                    existing = _resolve_resource(path)
                    return _ok(object_id=existing["id"], item=existing, opened_existing=True)
                except Exception:
                    pass

        MutationPolicy.current().require_write()

        result = _bridge(
            "open_hierarchy",
            path=path,
            relative_to_id=relative_to_id,
            create_file_type=_enum("create_type", normalized_create_type, CREATE_FILE_TYPES),
        )
        item_type = _create_type_to_item_type(normalized_create_type)
        item = (
            _wait_created_domain_item(expected_path, item_type, result["object_id"])
            if item_type
            else None
        )
        data = _ok(object_id=item["id"] if item else result["object_id"], opened_existing=False)
        if item:
            data["item"] = item
        return data
    except Exception as exc:
        return _error(str(exc))


@mcp.tool()
async def create_notebook(name_or_path: str, base_folder: str = "") -> dict[str, Any]:
    """Create a local notebook folder and open it in OneNote."""

    try:
        MutationPolicy.current().require_write()
        raw = Path(name_or_path)
        if raw.is_absolute():
            notebook_path = raw
        else:
            if base_folder:
                root = Path(base_folder)
            else:
                root = Path(_bridge("get_special_location", location=SPECIAL_LOCATIONS["default_notebook_folder"])["path"])
            notebook_path = root / _safe_leaf_name(name_or_path)
        result = _bridge(
            "open_hierarchy",
            path=str(notebook_path),
            relative_to_id="",
            create_file_type=CREATE_FILE_TYPES["notebook"],
        )
        notebook = _wait_created_domain_item(notebook_path.name, "notebook", result["object_id"])
        if notebook is None:
            raise RuntimeError("Notebook creation returned success, but the new notebook could not be verified.")
        return _ok(path=str(notebook_path), notebook_id=result["object_id"], item=notebook)
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def create_section(parent_id: str, section_name: str) -> dict[str, Any]:
    """Create a section under a notebook or section group."""

    try:
        MutationPolicy.current().require_write()
        parent = _domain_item(parent_id)
        if parent["resource_type"] not in {"notebook", "section_group"}:
            raise ValueError("parent_id must identify a notebook or section_group.")
        filename = _safe_leaf_name(section_name)
        if not filename.lower().endswith(".one"):
            filename += ".one"
        result = _bridge(
            "open_hierarchy",
            path=filename,
            relative_to_id=parent["id"],
            create_file_type=CREATE_FILE_TYPES["section"],
        )
        expected_path = _friendly_child_path(parent["path"], filename)
        section = _wait_created_domain_item(expected_path, "section", result["object_id"])
        if section is None:
            raise RuntimeError("Section creation returned success, but the new section could not be verified.")
        return _ok(
            parent=parent,
            section=section,
            section_id=section["id"] if section else result["object_id"],
            name=section_name,
            path=expected_path,
        )
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def create_section_group(parent_id: str, group_name: str) -> dict[str, Any]:
    """Create a section group under a notebook or another section group."""

    try:
        MutationPolicy.current().require_write()
        parent = _domain_item(parent_id)
        if parent["resource_type"] not in {"notebook", "section_group"}:
            raise ValueError("parent_id must identify a notebook or section_group.")
        result = _bridge(
            "open_hierarchy",
            path=_safe_leaf_name(group_name),
            relative_to_id=parent["id"],
            create_file_type=CREATE_FILE_TYPES["section_group"],
        )
        expected_path = _friendly_child_path(parent["path"], group_name)
        group = _wait_created_domain_item(expected_path, "section_group", result["object_id"])
        if group is None:
            raise RuntimeError("Section-group creation returned success, but the new group could not be verified.")
        return _ok(
            parent=parent,
            section_group=group,
            section_group_id=group["id"] if group else result["object_id"],
            name=group_name,
            path=expected_path,
        )
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def create_page(
    section_id: str,
    title: str,
    content: str = "",
    content_format: str = "plain",
    new_page_style: str = "blank_with_title",
) -> dict[str, Any]:
    """Create a page in a local OneNote section. content_format accepts plain, html, or markdown."""

    try:
        MutationPolicy.current().require_write()
        section = _domain_item(section_id, "section")
        result = _bridge(
            "create_new_page",
            section_id=section["id"],
            new_page_style=_enum("new_page_style", new_page_style, NEW_PAGE_STYLES),
        )
        page_id = result["page_id"]
        xml = build_page_update_xml(page_id, title=title, content=content, content_format=content_format)
        _bridge("update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False)
        expected_path = _friendly_child_path(section["path"], title)
        page = _wait_created_domain_item(expected_path, "page", page_id)
        if page is None:
            raise RuntimeError("Page creation returned success, but the new page could not be verified.")
        return _ok(page_id=page["id"] if page else page_id, page=page, section=section, title=title, path=expected_path)
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def update_page_title(
    page_id: str,
    title: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Update a page title."""

    try:
        MutationPolicy.current().require_write()
        _confirm_page(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        xml = build_page_update_xml(page_id, title=title)
        _bridge("update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False)
        item = _wait_domain_item(page_id, "page", predicate=lambda value: value["title"] == title)
        if item is None:
            raise RuntimeError("Update returned success, but the page title could not be verified.")
        return _ok(item=item)
    except Exception as exc:
        return _caught(exc)


async def _rename_resource(
    object_id: str,
    resource_type: str,
    new_name: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None,
) -> dict[str, Any]:
    MutationPolicy.current().require_write()
    item = _confirm_item(
        object_id,
        resource_type,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
    )
    normalized_name = _safe_leaf_name(new_name)
    _bridge(
        "update_hierarchy",
        xml=_hierarchy_update_xml(item, name=normalized_name),
        schema=XML_SCHEMA_2013,
    )
    refreshed = _wait_domain_item(
        object_id,
        resource_type,
        predicate=lambda value: value["name"] == normalized_name and value["parent_id"] == expected_parent_id,
    )
    if refreshed is None:
        raise RuntimeError("Rename returned success, but the new name could not be verified by ID.")
    return _ok(item=refreshed, previous_name=expected_name)


@mcp.tool()
async def rename_section_group(
    section_group_id: str,
    new_name: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Rename a confirmed section group and verify the same ID after mutation."""

    try:
        return await _rename_resource(
            section_group_id, "section_group", new_name, expected_name, expected_parent_id, expected_modified
        )
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def rename_section(
    section_id: str,
    new_name: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Rename a confirmed section and verify the same ID after mutation."""

    try:
        return await _rename_resource(
            section_id, "section", new_name, expected_name, expected_parent_id, expected_modified
        )
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def reorder_page(
    page_id: str,
    expected_title: str,
    expected_section_id: str,
    after_page_id: str = "",
    page_level: int = 0,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Reorder a Page within its Section and optionally change its indentation level."""

    try:
        MutationPolicy.current().require_write()
        page = _confirm_page(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        section = _domain_item(expected_section_id, "section")
        pages = [
            item
            for item in _domain_items(include_recycle_bin=False)
            if item["resource_type"] == "page" and item["section_id"] == expected_section_id
        ]
        pages.sort(key=lambda item: item["order"])
        pages = [item for item in pages if item["id"] != page_id]
        if after_page_id:
            if after_page_id == page_id:
                raise ValueError("after_page_id cannot equal page_id.")
            indexes = [index for index, item in enumerate(pages) if item["id"] == after_page_id]
            if not indexes:
                raise ValueError("after_page_id must identify another page in the same section.")
            insertion_index = indexes[0] + 1
        else:
            insertion_index = 0
        target_level = page_level or page["page_level"]
        if target_level < 1:
            raise ValueError("page_level must be zero (preserve) or at least 1.")
        if insertion_index == 0 and target_level != 1:
            raise ValueError("The first page in a section must have page_level=1.")
        if insertion_index > 0 and target_level > pages[insertion_index - 1]["page_level"] + 1:
            raise ValueError("page_level cannot jump by more than one level from the preceding page.")
        moved = {**page, "page_level": target_level}
        pages.insert(insertion_index, moved)
        _bridge("update_hierarchy", xml=_page_order_update_xml(section, pages), schema=XML_SCHEMA_2013)
        refreshed_pages = [
            item
            for item in _domain_items(include_recycle_bin=False)
            if item["resource_type"] == "page" and item["section_id"] == expected_section_id
        ]
        refreshed_pages.sort(key=lambda item: item["order"])
        refreshed = next((item for item in refreshed_pages if item["id"] == page_id), None)
        if refreshed is None or refreshed["order"] != insertion_index or refreshed["page_level"] != target_level:
            raise RuntimeError("Reorder returned success, but order/page_level read-back verification failed.")
        return _ok(item=refreshed, pages=refreshed_pages)
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def move_section(
    section_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Experimentally move a Section inside the same Notebook with identity/content/order verification."""

    try:
        MutationPolicy.current().require_experimental_move()
        section = _confirm_item(
            section_id,
            "section",
            expected_name=expected_name,
            expected_parent_id=expected_parent_id,
            expected_modified=expected_modified,
        )
        destination = _domain_item(destination_parent_id)
        if destination["resource_type"] not in {"notebook", "section_group"}:
            raise ValueError("destination_parent_id must identify a notebook or section_group.")
        destination_notebook_id = (
            destination["id"] if destination["resource_type"] == "notebook" else destination["notebook_id"]
        )
        if destination_notebook_id != section["notebook_id"]:
            raise ValueError("move_section only supports destinations in the same notebook.")
        before_pages = [
            item
            for item in _domain_items(include_recycle_bin=False)
            if item["resource_type"] == "page" and item["section_id"] == section_id
        ]
        before_pages.sort(key=lambda item: item["order"])
        before_hashes = {item["id"]: _page_content_digest(_page_xml(item["id"], "all")) for item in before_pages}
        _bridge("update_hierarchy", xml=_section_move_xml(section, destination), schema=XML_SCHEMA_2013)
        moved = _wait_domain_item(
            section_id,
            "section",
            predicate=lambda value: value["parent_id"] == destination_parent_id,
        )
        if moved is None:
            raise RuntimeError("Move returned success, but the Section parent could not be verified.")
        after_pages = [
            item
            for item in _domain_items(include_recycle_bin=False)
            if item["resource_type"] == "page" and item["section_id"] == section_id
        ]
        after_pages.sort(key=lambda item: item["order"])
        if [item["id"] for item in after_pages] != [item["id"] for item in before_pages]:
            raise RuntimeError("Section moved, but Page identity/order verification failed.")
        after_hashes = {item["id"]: _page_content_digest(_page_xml(item["id"], "all")) for item in after_pages}
        if after_hashes != before_hashes:
            raise RuntimeError("Section moved, but Page content verification failed.")
        return _ok(
            item=moved,
            verified={"section_id_preserved": True, "page_ids_and_order_preserved": True, "page_content_preserved": True},
            warnings=["Experimental COM behavior: keep this tool disabled until the documented isolated test passes."],
        )
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def append_to_page(
    page_id: str,
    content: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    content_format: str = "plain",
    x: float | None = None,
    y: float | None = None,
) -> dict[str, Any]:
    """Append a new outline block to a page. content_format accepts plain, html, or markdown."""

    try:
        MutationPolicy.current().require_write()
        before = _confirm_page(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        before_hash = _page_content_digest(_page_xml(page_id, "all"))
        xml = build_page_update_xml(page_id, content=content, content_format=content_format, x=x, y=y)
        _bridge("update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False)
        after_hash = _page_content_digest(_page_xml(page_id, "all"))
        if after_hash == before_hash:
            raise RuntimeError("Append returned success, but Page content did not change during read-back verification.")
        after = _wait_domain_item(page_id, "page")
        return _ok(item=after, before_modified=before.get("modified"), appended=True)
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def add_image_to_page(
    page_id: str,
    image_path: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    image_format: str = "",
    x: float = 36.0,
    y: float = 120.0,
    width: float | None = None,
    height: float | None = None,
) -> dict[str, Any]:
    """Add a local image file to a OneNote page."""

    try:
        MutationPolicy.current().require_write()
        _confirm_page(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        before_hash = _page_content_digest(_page_xml(page_id, "all"))
        path = Path(image_path)
        if not path.is_file():
            raise ValueError(f"Image file not found: {image_path}")
        fmt = image_format or path.suffix.lstrip(".")
        if not fmt:
            raise ValueError("image_format is required when image_path has no extension.")
        resolved_width, resolved_height = proportional_dimensions(path, width, height)
        image_base64 = base64.b64encode(path.read_bytes()).decode("ascii")
        xml = build_image_page_update_xml(
            page_id,
            image_base64=image_base64,
            image_format=fmt,
            x=x,
            y=y,
            width=resolved_width,
            height=resolved_height,
        )
        _bridge("update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False)
        after_hash = _page_content_digest(_page_xml(page_id, "all"))
        if after_hash == before_hash:
            raise RuntimeError("Image update returned success, but Page content did not change during read-back verification.")
        item = _wait_domain_item(page_id, "page")
        return _ok(item=item, image_path=str(path), width=resolved_width, height=resolved_height)
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def replace_page_body(
    page_id: str,
    content: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    title: str | None = None,
    content_format: str = "plain",
) -> dict[str, Any]:
    """Rebuild page body content. This is a multi-step, non-atomic mutation."""

    deleted: list[str] = []
    try:
        policy = MutationPolicy.current()
        policy.require_write()
        policy.require_delete()
        _confirm_page(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        page_xml = _page_xml(page_id, "all")
        before_hash = _page_content_digest(page_xml)
        objects = collect_page_objects(page_xml)
        for obj in objects:
            if obj.get("type") not in REPLACE_BODY_OBJECT_TYPES:
                continue
            object_id = obj.get("object_id")
            if not object_id:
                continue
            _bridge("delete_page_content", page_id=page_id, object_id=object_id, force=False)
            deleted.append(object_id)
        xml = build_page_update_xml(page_id, title=title, content=content, content_format=content_format)
        _bridge("update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False)
        after_hash = _page_content_digest(_page_xml(page_id, "all"))
        if after_hash == before_hash:
            raise RuntimeError("Rebuild returned success, but Page content did not change during read-back verification.")
        item = _wait_domain_item(page_id, "page")
        return _ok(item=item, deleted_objects=deleted, replaced=True, partial=False)
    except Exception as exc:
        if deleted:
            return _error(
                str(exc),
                "partial_failure",
                partial=True,
                completed_steps=[{"operation": "delete_page_content", "object_id": value} for value in deleted],
            )
        return _caught(exc)


@mcp.tool()
async def delete_page_content(
    page_id: str,
    object_id: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Delete one deletable page content object by object ID. Use get_page_objects to find delete_supported objects."""

    try:
        MutationPolicy.current().require_delete()
        _confirm_page(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        objects = collect_page_objects(_page_xml(page_id, "all"))
        matched = next((obj for obj in objects if obj.get("object_id") == object_id), None)
        if matched and not matched.get("delete_supported"):
            suggested_id = matched.get("delete_object_id")
            if suggested_id:
                raise ValueError(
                    f"Object '{object_id}' is a {matched.get('type')} child and is not directly deletable by OneNote COM. "
                    f"Delete its parent content object '{suggested_id}' instead."
                )
            allowed = ", ".join(sorted(DELETABLE_PAGE_OBJECT_TYPES))
            raise ValueError(
                f"Object '{object_id}' is a {matched.get('type')} child and is not directly deletable by OneNote COM. "
                f"Deletable object types: {allowed}."
            )
        if matched is None or not matched.get("delete_supported"):
            raise ValueError("object_id is not a currently verified deletable page content object.")
        _bridge("delete_page_content", page_id=page_id, object_id=object_id, force=False)
        remaining = collect_page_objects(_page_xml(page_id, "all"))
        if any(item.get("object_id") == object_id for item in remaining):
            raise RuntimeError("Delete returned success, but the page content object still exists.")
        return _ok(page_id=page_id, object_id=object_id, deleted=True)
    except Exception as exc:
        return _caught(exc)


async def _delete_resource(
    object_id: str,
    resource_type: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None,
    permanently: bool,
) -> dict[str, Any]:
    policy = MutationPolicy.current()
    policy.require_delete(permanently=permanently)
    item = _confirm_item(
        object_id,
        resource_type,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
    )
    _bridge("delete_hierarchy", object_id=object_id, permanently=permanently)
    final_state: dict[str, Any] | None = None
    for attempt in range(8):
        try:
            final_state = _domain_item(object_id, resource_type)
        except ValueError:
            final_state = None
        if final_state is None or (not permanently and final_state["is_in_recycle_bin"]):
            return _ok(
                item=item,
                object_id=object_id,
                permanently=permanently,
                deleted=True,
                final_state=final_state,
            )
        if attempt < 7:
            time.sleep(0.5)
    raise RuntimeError("Delete returned success, but the object remained active after read-back verification.")


@mcp.tool()
async def delete_section_group(
    section_group_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
    permanently: bool = False,
) -> dict[str, Any]:
    """Delete a confirmed section group; recycle-bin deletion is the default."""

    try:
        return await _delete_resource(
            section_group_id, "section_group", expected_name, expected_parent_id, expected_modified, permanently
        )
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def delete_section(
    section_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
    permanently: bool = False,
) -> dict[str, Any]:
    """Delete a confirmed section; recycle-bin deletion is the default."""

    try:
        return await _delete_resource(section_id, "section", expected_name, expected_parent_id, expected_modified, permanently)
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def delete_page(
    page_id: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    permanently: bool = False,
) -> dict[str, Any]:
    """Delete a confirmed page; recycle-bin deletion is the default."""

    try:
        page = _confirm_page(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        return await _delete_resource(
            page_id,
            "page",
            page["title"],
            page["parent_id"],
            expected_modified,
            permanently,
        )
    except Exception as exc:
        return _caught(exc)


@_advanced_tool
async def delete_hierarchy(object_identifier: str, permanently: bool = False) -> dict[str, Any]:
    """Development profile only: legacy generic hierarchy delete (Notebook is always rejected)."""

    try:
        MutationPolicy.current().require_raw_xml()
        MutationPolicy.current().require_delete(permanently=permanently)
        item = _resolve_resource(object_identifier)
        if item["resource_type"] == "notebook":
            raise ValueError("Notebook deletion is unsupported; close_notebook is not deletion.")
        deleted_ids = []
        for attempt in range(4):
            object_id = item["id"]
            _bridge("delete_hierarchy", object_id=object_id, permanently=permanently)
            deleted_ids.append(object_id)
            time.sleep(0.5)
            remaining = _find_resource_path(item["path"], item["resource_type"])
            if not remaining:
                return _ok(
                    object_id=object_id,
                    deleted_ids=deleted_ids,
                    permanently=permanently,
                    deleted=True,
                    verified_gone=True,
                )
            item = remaining
            if attempt == 3:
                raise RuntimeError(f"Delete returned success, but '{item['path']}' still exists with ID {item['id']}.")
        raise RuntimeError("Delete did not complete.")
    except Exception as exc:
        return _caught(exc)


@_advanced_tool
async def update_page_xml(xml: str) -> dict[str, Any]:
    """Advanced: submit raw OneNote page XML to UpdatePageContent."""

    try:
        policy = MutationPolicy.current()
        policy.require_raw_xml()
        policy.require_write()
        _bridge("update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False)
        return _ok(updated=True)
    except Exception as exc:
        return _error(str(exc))


@_advanced_tool
async def update_hierarchy_xml(xml: str) -> dict[str, Any]:
    """Advanced: submit raw OneNote hierarchy XML to UpdateHierarchy."""

    try:
        policy = MutationPolicy.current()
        policy.require_raw_xml()
        policy.require_write()
        _bridge("update_hierarchy", xml=xml, schema=XML_SCHEMA_2013)
        return _ok(updated=True)
    except Exception as exc:
        return _error(str(exc))


@mcp.tool()
async def publish_object(object_id: str, target_path: str, format: str = "pdf", overwrite: bool = False) -> dict[str, Any]:
    """Export a notebook, section, or page to a local file."""

    try:
        output = Path(target_path).expanduser()
        if not output.is_absolute():
            output = Path.cwd() / output
        output = output.resolve(strict=False)
        if output.exists() and not overwrite:
            raise ValueError(f"Target already exists: {target_path}")
        output.parent.mkdir(parents=True, exist_ok=True)
        item = _domain_item(object_id)
        if item["resource_type"] not in {"notebook", "section", "page"}:
            raise ValueError("publish_object supports notebook, section, or page IDs.")
        result = _bridge(
            "publish",
            object_id=object_id,
            target_path=str(output),
            format=_enum("format", format, PUBLISH_FORMATS),
        )
        return _ok(item=item, path=result["path"], format=format.casefold())
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def navigate_to(object_id: str, page_content_object_id: str = "", new_window: bool = False) -> dict[str, Any]:
    """Open a OneNote object in the desktop app."""

    try:
        item = _domain_item(object_id)
        _bridge("navigate_to", object_id=object_id, page_content_object_id=page_content_object_id, new_window=new_window)
        return _ok(item=item, navigated=True)
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def navigate_to_url(url: str, new_window: bool = False) -> dict[str, Any]:
    """Open a OneNote URL in the desktop app."""

    try:
        _bridge("navigate_to_url", url=url, new_window=new_window)
        return _ok(navigated=True)
    except Exception as exc:
        return _error(str(exc))


@mcp.tool()
async def sync_notebook(notebook_id: str) -> dict[str, Any]:
    """Ask OneNote to sync one Notebook selected by exact COM object ID."""

    try:
        item = _domain_item(notebook_id, "notebook")
        _bridge("sync_hierarchy", object_id=notebook_id)
        return _ok(item=item, synced=True)
    except Exception as exc:
        return _caught(exc)


@mcp.tool()
async def close_notebook(notebook_id: str, expected_name: str, expected_modified: str | None = None) -> dict[str, Any]:
    """Close a notebook in the desktop OneNote app."""

    try:
        MutationPolicy.current().require_write()
        item = _confirm_item(
            notebook_id,
            "notebook",
            expected_name=expected_name,
            expected_parent_id=None,
            expected_modified=expected_modified,
        )
        _bridge("close_notebook", notebook_id=notebook_id, force=False)
        closed_state: dict[str, Any] | None = None
        for attempt in range(8):
            try:
                closed_state = _domain_item(notebook_id, "notebook")
            except ValueError:
                closed_state = None
            if closed_state is None or closed_state.get("is_open") is False:
                return _ok(item=item, closed=True, final_state=closed_state)
            if attempt < 7:
                time.sleep(0.5)
        raise RuntimeError("Close returned success, but the Notebook still appears open after read-back verification.")
    except Exception as exc:
        return _caught(exc)


@_advanced_tool
async def merge_sections(source_section_identifier: str, destination_section_identifier: str) -> dict[str, Any]:
    """Merge one section into another."""

    try:
        policy = MutationPolicy.current()
        policy.require_raw_xml()
        policy.require_write()
        source_id = _resolve_resource(source_section_identifier, "section")["id"]
        destination_id = _resolve_resource(destination_section_identifier, "section")["id"]
        _bridge("merge_sections", source_section_id=source_id, destination_section_id=destination_id)
        return _ok(source_section_id=source_id, destination_section_id=destination_id, merged=True)
    except Exception as exc:
        return _error(str(exc))


@_advanced_tool
async def set_filing_location(filing_location: str, filing_location_type: str, section_or_page_identifier: str) -> dict[str, Any]:
    """Set OneNote's local filing location for email, web clips, printouts, and similar content."""

    try:
        MutationPolicy.current().require_write()
        object_id = _resolve_resource(section_or_page_identifier)["id"]
        _bridge(
            "set_filing_location",
            filing_location=_enum("filing_location", filing_location, FILING_LOCATIONS),
            filing_location_type=_enum("filing_location_type", filing_location_type, FILING_LOCATION_TYPES),
            section_or_page_id=object_id,
        )
        return _ok(object_id=object_id, updated=True)
    except Exception as exc:
        return _error(str(exc))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
