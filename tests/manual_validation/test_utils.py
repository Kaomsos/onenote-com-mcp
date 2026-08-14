"""Snapshot, evidence, and manifest helpers used by validation scenarios."""

from __future__ import annotations

import base64
import binascii
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version as package_version
import json
from pathlib import Path
import re
from typing import Any
import uuid
import xml.etree.ElementTree as ET

from local_onenote_mcp.page import (
    canonical_page_digest,
    collect_page_objects,
    page_content_capability_projection,
    semantic_mathml_projection,
)
from local_onenote_mcp.domain import content_objects
from local_onenote_mcp.page.copying import MATHML_FRAGMENT_PATTERN
from local_onenote_mcp.page.parser import html_fragment_to_text, local_name, parse_xml
from local_onenote_mcp.services.pages import stable_page_content_digest

from .mcp_stdio_client import COPY_BUDGET_ENV, MCPStdioClient, ScenarioPolicy
from .local_filesystem import atomic_replace_with_retry
from .path_budget import preflight_paths, validate_run_evidence_leaf
from .run_identity import new_run_identity
from .runtime import InvariantFailure, RestoreFailure, RunnerFailure, RuntimeOptions


def timestamp() -> str:
    """Return a Windows-safe local display timestamp for legacy callers."""

    return new_run_identity().safe_timestamp


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def installed_runner_version() -> str:
    try:
        return package_version("local-onenote-mcp")
    except PackageNotFoundError:
        return "unknown"


def write_json(path: Path, value: Any) -> None:
    validate_run_evidence_leaf(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:16]}.tmp")
    preflight_paths(
        ((path, "run_evidence", None), (temporary, "atomic_metadata_temp", None)),
        phase="run_evidence_preflight",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    atomic_replace_with_retry(temporary, path)


_BINARY_DATA_PATTERN = re.compile(
    r"(<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?Data\b[^>]*>)"
    r"(.*?)"
    r"(</(?:[A-Za-z_][A-Za-z0-9_.-]*:)?Data\s*>)",
    flags=re.IGNORECASE | re.DOTALL,
)


def write_sensitive_page_xml(path: Path, xml: str) -> dict[str, Any]:
    """Persist opted-in Page XML while replacing embedded binary payloads."""

    redactions: list[dict[str, Any]] = []

    def redact(match: re.Match[str]) -> str:
        payload = match.group(2)
        compact = "".join(payload.split())
        decoded_as_base64 = True
        try:
            binary = base64.b64decode(compact, validate=True)
        except (ValueError, binascii.Error):
            decoded_as_base64 = False
            binary = payload.encode("utf-8")
        redactions.append(
            {
                "encoded_chars": len(compact),
                "decoded_bytes": len(binary),
                "decoded_as_base64": decoded_as_base64,
                "binary_sha256": hashlib.sha256(binary).hexdigest(),
            }
        )
        marker = (
            "[[local-onenote-mcp:binary-data-redacted:"
            f"index={len(redactions) - 1}]]"
        )
        return f"{match.group(1)}{marker}{match.group(3)}"

    redacted_xml = _BINARY_DATA_PATTERN.sub(redact, xml)
    validate_run_evidence_leaf(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:16]}.tmp")
    preflight_paths(
        ((path, "run_xml_evidence", None), (temporary, "atomic_metadata_temp", None)),
        phase="run_evidence_preflight",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(redacted_xml, encoding="utf-8")
    atomic_replace_with_retry(temporary, path)
    return {
        "path": str(path.resolve()),
        "source_xml_chars": len(xml),
        "saved_xml_chars": len(redacted_xml),
        "binary_payload_count": len(redactions),
        "binary_payloads": redactions,
        "body_text_retained": True,
        "raw_oe_t_mathml_retained": True,
        "binary_data_retained": False,
    }


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunnerFailure(f"Required file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RunnerFailure(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RunnerFailure(f"Expected a JSON object in {path}.")
    return value


def display_name(item: dict[str, Any]) -> str:
    return str(item.get("title") or item.get("name") or "")


def flatten_tree(tree: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        item = node.get("item")
        if isinstance(item, dict):
            items.append(item)
        for child in node.get("children", []):
            if isinstance(child, dict):
                visit(child)

    visit(tree)
    return items


SNAPSHOT_FIELDS = (
    "resource_type",
    "id",
    "name",
    "title",
    "path",
    "parent_id",
    "modified",
    "notebook_id",
    "section_id",
    "page_level",
    "order",
    "parent_page_id",
    "is_in_recycle_bin",
)
OBJECT_FIELDS = (
    "type",
    "kind",
    "id",
    "object_id",
    "callback_id",
    "format",
    "media_type",
    "can_delete",
    "delete_supported",
    "delete_target_id",
    "delete_object_id",
    "container_object_id",
    "parent_object_id",
    "page_id",
)


def stable_item(item: dict[str, Any]) -> dict[str, Any]:
    return {field: item.get(field) for field in SNAPSHOT_FIELDS if field in item}


def page_content_hash(xml: str) -> str:
    """Hash stable Page content while preserving content-object identities."""

    return stable_page_content_digest(xml)


def page_reparent_content_hash(xml: str) -> str:
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


def mathml_structure_projection(xml: str) -> dict[str, Any]:
    """Describe UI-authored MathML placement without retaining formula text."""

    root = parse_xml(xml)
    parents = {child: parent for parent in root.iter() for child in list(parent)}
    semantic = semantic_mathml_projection(xml)
    candidates: list[dict[str, Any]] = []

    def nearest(node: ET.Element, kind: str) -> ET.Element | None:
        current = parents.get(node)
        while current is not None:
            if local_name(current.tag) == kind:
                return current
            current = parents.get(current)
        return None

    def oe_summary(node: ET.Element | None) -> dict[str, Any] | None:
        if node is None:
            return None
        texts = [
            child.text or ""
            for child in node.iter()
            if local_name(child.tag) == "T"
        ]
        return {
            "child_kinds": [local_name(child.tag) for child in list(node)],
            "contains_mathml": any(MATHML_FRAGMENT_PATTERN.search(value) for value in texts),
            "contains_visible_text": any(html_fragment_to_text(value) for value in texts),
        }

    def text_summary(node: ET.Element | None) -> dict[str, Any] | None:
        if node is None:
            return None
        raw = node.text or ""
        residual = MATHML_FRAGMENT_PATTERN.sub("", raw)
        markup_tags = Counter(
            match.group(1).casefold()
            for match in re.finditer(
                r"<\s*(?!/|!|\?)([A-Za-z][A-Za-z0-9:_-]*)\b",
                residual,
            )
        )
        return {
            "contains_mathml": bool(MATHML_FRAGMENT_PATTERN.search(raw)),
            "contains_visible_text": bool(html_fragment_to_text(residual)),
            "markup_tags": dict(sorted(markup_tags.items())),
        }

    def known_display_break_wrapper(residual: str) -> bool:
        cleaned = re.sub(
            r"<!--\s*\[if\s+mathML\]\s*>|<!\s*\[endif\]\s*-->",
            "",
            residual,
            flags=re.IGNORECASE,
        ).strip()
        if not cleaned:
            return False
        return bool(
            re.fullmatch(
                r"<span\b"
                r"(?=[^>]*\bstyle\s*=\s*(['\"])[^>]*font-family\s*:\s*Calibri[^>]*\1)"
                r"(?=[^>]*\blang\s*=\s*(?:['\"][^'\"]+['\"]|[^\s>]+))"
                r"[^>]*>\s*(?:<br\s*/>\s*)+</span>",
                cleaned,
                flags=re.IGNORECASE | re.DOTALL,
            )
        ) and not html_fragment_to_text(cleaned)

    for text_node in root.iter():
        if local_name(text_node.tag) != "T" or not text_node.text:
            continue
        matches = list(MATHML_FRAGMENT_PATTERN.finditer(text_node.text))
        if not matches:
            continue
        equations: list[dict[str, Any]] = []
        for match in matches:
            try:
                equation = ET.fromstring(match.group(0))
            except ET.ParseError:
                equations.append({"complete": False, "display": None})
            else:
                equations.append(
                    {
                        "complete": True,
                        "display": equation.attrib.get("display"),
                    }
                )

        residual = MATHML_FRAGMENT_PATTERN.sub("", text_node.text)
        residual_tags = Counter(
            match.group(1).casefold()
            for match in re.finditer(
                r"<\s*(?!/|!|\?)([A-Za-z][A-Za-z0-9:_-]*)\b",
                residual,
            )
        )
        oe = nearest(text_node, "OE")
        direct_text_nodes = (
            [child for child in list(oe) if local_name(child.tag) == "T"]
            if oe is not None
            else []
        )
        text_sibling_index = (
            direct_text_nodes.index(text_node) if text_node in direct_text_nodes else None
        )
        previous_text = (
            direct_text_nodes[text_sibling_index - 1]
            if text_sibling_index is not None and text_sibling_index > 0
            else None
        )
        next_text = (
            direct_text_nodes[text_sibling_index + 1]
            if text_sibling_index is not None
            and text_sibling_index + 1 < len(direct_text_nodes)
            else None
        )
        previous_text_summary = text_summary(previous_text)
        next_text_summary = text_summary(next_text)
        same_oe_surrounding_visible_text = bool(
            previous_text_summary
            and previous_text_summary["contains_visible_text"]
            and next_text_summary
            and next_text_summary["contains_visible_text"]
        )
        direct_text_break_count = sum(
            int((text_summary(child) or {}).get("markup_tags", {}).get("br", 0))
            for child in direct_text_nodes
        )
        oe_children = parents.get(oe) if oe is not None else None
        siblings = (
            [child for child in list(oe_children) if local_name(child.tag) == "OE"]
            if oe_children is not None and local_name(oe_children.tag) == "OEChildren"
            else []
        )
        sibling_index = siblings.index(oe) if oe in siblings else None
        ancestor_kinds: list[str] = []
        current: ET.Element | None = text_node
        while current is not None:
            ancestor_kinds.append(local_name(current.tag))
            current = parents.get(current)
        candidates.append(
            {
                "equations": equations,
                "ancestor_kinds": ancestor_kinds,
                "oe_child_kinds": (
                    [local_name(child.tag) for child in list(oe)] if oe is not None else []
                ),
                "t_sibling_index": text_sibling_index,
                "t_sibling_count": len(direct_text_nodes),
                "previous_t": previous_text_summary,
                "next_t": next_text_summary,
                "same_oe_surrounding_visible_text": same_oe_surrounding_visible_text,
                "oe_direct_t_break_count": direct_text_break_count,
                "oe_sibling_index": sibling_index,
                "oe_sibling_count": len(siblings),
                "previous_oe": (
                    oe_summary(siblings[sibling_index - 1])
                    if sibling_index is not None and sibling_index > 0
                    else None
                ),
                "next_oe": (
                    oe_summary(siblings[sibling_index + 1])
                    if sibling_index is not None and sibling_index + 1 < len(siblings)
                    else None
                ),
                "residual_markup_tags": dict(sorted(residual_tags.items())),
                "residual_visible_text": bool(html_fragment_to_text(residual)),
                "known_onenote_display_break_wrapper": (
                    known_display_break_wrapper(residual)
                ),
                "inline_visible_text_context": (
                    bool(html_fragment_to_text(residual))
                    or same_oe_surrounding_visible_text
                ),
            }
        )

    display_attribute_count = sum(
        equation.get("display") == "block"
        for candidate in candidates
        for equation in candidate["equations"]
    )
    standalone_candidate_count = sum(
        len(candidate["equations"]) == 1
        and candidate["inline_visible_text_context"] is False
        for candidate in candidates
    )
    return {
        "schema_version": 2,
        "semantic_mathml": semantic,
        "candidate_text_node_count": len(candidates),
        "display_attribute_equation_count": display_attribute_count,
        "equations_without_display_attribute": (
            semantic["equation_count"] - display_attribute_count
        ),
        "standalone_candidate_count": standalone_candidate_count,
        "candidates": candidates,
        "complete": semantic["complete"] is True,
    }


def mathml_oe_adjacency_projection(xml: str) -> dict[str, Any]:
    """Project the exact OE siblings around MathML without retaining body text."""

    root = parse_xml(xml)
    parents = {child: parent for parent in root.iter() for child in list(parent)}

    def nearest(node: ET.Element, kind: str) -> ET.Element | None:
        current = parents.get(node)
        while current is not None:
            if local_name(current.tag) == kind:
                return current
            current = parents.get(current)
        return None

    def text_projection(node: ET.Element) -> dict[str, Any]:
        raw = node.text or ""
        matches = list(MATHML_FRAGMENT_PATTERN.finditer(raw))
        display_block_count = 0
        complete = True
        for match in matches:
            try:
                equation = ET.fromstring(match.group(0))
            except ET.ParseError:
                complete = False
            else:
                display_block_count += equation.attrib.get("display") == "block"
        residual = MATHML_FRAGMENT_PATTERN.sub("", raw)
        whitespace_codepoints = Counter(
            f"U+{ord(value):04X}" for value in raw if value.isspace()
        )
        return {
            "raw_chars": len(raw),
            "whitespace_chars": sum(value.isspace() for value in raw),
            "only_whitespace": bool(raw) and raw.isspace(),
            "whitespace_codepoint_counts": dict(sorted(whitespace_codepoints.items())),
            "mathml_root_count": len(matches),
            "display_block_count": display_block_count,
            "mathml_complete": complete,
            "residual_chars": len(residual),
            "residual_whitespace_chars": sum(value.isspace() for value in residual),
            "residual_only_whitespace": bool(residual) and residual.isspace(),
            "residual_visible_text": bool(html_fragment_to_text(residual)),
        }

    def oe_projection(node: ET.Element | None) -> dict[str, Any] | None:
        if node is None:
            return None
        direct_text_nodes = [
            child for child in list(node) if local_name(child.tag) == "T"
        ]
        return {
            "child_kinds": [local_name(child.tag) for child in list(node)],
            "direct_t_count": len(direct_text_nodes),
            "direct_t": [text_projection(child) for child in direct_text_nodes],
        }

    candidates: list[dict[str, Any]] = []
    for text_node in root.iter():
        if local_name(text_node.tag) != "T" or not text_node.text:
            continue
        if not MATHML_FRAGMENT_PATTERN.search(text_node.text):
            continue
        oe = nearest(text_node, "OE")
        oe_children = parents.get(oe) if oe is not None else None
        siblings = (
            [child for child in list(oe_children) if local_name(child.tag) == "OE"]
            if oe_children is not None and local_name(oe_children.tag) == "OEChildren"
            else []
        )
        sibling_index = siblings.index(oe) if oe in siblings else None
        previous = (
            siblings[sibling_index - 1]
            if sibling_index is not None and sibling_index > 0
            else None
        )
        following = (
            siblings[sibling_index + 1]
            if sibling_index is not None and sibling_index + 1 < len(siblings)
            else None
        )
        previous_projection = oe_projection(previous)
        formula_projection = oe_projection(oe)
        next_projection = oe_projection(following)
        previous_text = (
            previous_projection["direct_t"][0]
            if previous_projection
            and previous_projection["child_kinds"] == ["T"]
            and previous_projection["direct_t_count"] == 1
            else None
        )
        formula_text = (
            formula_projection["direct_t"][0]
            if formula_projection
            and formula_projection["child_kinds"] == ["T"]
            and formula_projection["direct_t_count"] == 1
            else None
        )
        candidates.append(
            {
                "formula_oe_sibling_index": sibling_index,
                "oe_sibling_count": len(siblings),
                "previous_oe": previous_projection,
                "formula_oe": formula_projection,
                "next_oe": next_projection,
                "matches_literal_space_oe_then_display_formula_oe": (
                    previous_text is not None
                    and previous_text["raw_chars"] == 1
                    and previous_text["only_whitespace"] is True
                    and previous_text["whitespace_codepoint_counts"]
                    == {"U+0020": 1}
                    and formula_text is not None
                    and formula_text["mathml_root_count"] == 1
                    and formula_text["display_block_count"] == 1
                    and formula_text["residual_chars"] == 0
                ),
            }
        )
    return {
        "schema_version": 1,
        "mathml_candidate_count": len(candidates),
        "candidates": candidates,
        "content_exposed": False,
    }


async def capture_snapshot(client: MCPStdioClient, notebook_id: str) -> dict[str, Any]:
    consume_handoff = getattr(client, "consume_scenario_before_snapshot", None)
    if callable(consume_handoff):
        handed_off = consume_handoff(notebook_id)
        if handed_off is not None:
            return handed_off
    tree_result = await client.call_tool("get_tree", {"root_id": notebook_id, "max_depth": 8})
    tree = tree_result["tree"]
    items = flatten_tree(tree)
    pages = sorted(
        (item for item in items if item.get("resource_type") == "page"),
        key=lambda item: (str(item.get("section_id")), int(item.get("order", 0))),
    )
    page_hashes: dict[str, str] = {}
    page_canonical_hashes: dict[str, str] = {}
    page_reparent_hashes: dict[str, str] = {}
    page_xml_hashes: dict[str, str] = {}
    page_objects: dict[str, list[dict[str, Any]]] = {}
    page_capability_projections: dict[str, dict[str, Any]] = {}
    page_mathml_structure_projections: dict[str, dict[str, Any]] = {}
    for page in pages:
        page_id = str(page["id"])
        xml_result = await client.call_tool("get_page_xml", {"page_id": page_id, "page_info": "all"})
        xml = str(xml_result["xml"])
        page_hashes[page_id] = page_content_hash(xml)
        page_canonical_hashes[page_id] = canonical_page_digest(xml)
        page_reparent_hashes[page_id] = page_reparent_content_hash(xml)
        page_xml_hashes[page_id] = hashlib.sha256(xml.encode("utf-8")).hexdigest()
        page_capability_projections[page_id] = page_content_capability_projection(xml)
        page_mathml_structure_projections[page_id] = mathml_structure_projection(xml)
        objects = content_objects(page_id, collect_page_objects(xml))
        page_objects[page_id] = [
            {field: obj.get(field) for field in OBJECT_FIELDS if field in obj}
            for obj in objects
            if isinstance(obj, dict)
        ]
    refreshed_tree_result = await client.call_tool(
        "get_tree", {"root_id": notebook_id, "max_depth": 8}
    )
    refreshed_items = flatten_tree(refreshed_tree_result["tree"])
    initial_ids = {str(item["id"]) for item in items if item.get("id")}
    refreshed_ids = {str(item["id"]) for item in refreshed_items if item.get("id")}
    if refreshed_ids != initial_ids:
        raise InvariantFailure(
            "Hierarchy IDs changed while the snapshot was collecting Page evidence."
        )
    return {
        "captured_at": utc_now(),
        "notebook_id": notebook_id,
        "items": [stable_item(item) for item in refreshed_items],
        "page_hashes": page_hashes,
        "page_canonical_hashes": page_canonical_hashes,
        "page_reparent_hashes": page_reparent_hashes,
        "page_xml_hashes": page_xml_hashes,
        "page_objects": page_objects,
        "page_capability_projections": page_capability_projections,
        "page_mathml_structure_projections": page_mathml_structure_projections,
    }


def comparable_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Strip capture time and normalize item order for restoration comparison."""

    return {
        "notebook_id": snapshot.get("notebook_id"),
        "items": sorted(
            (
                {key: value for key, value in item.items() if key != "modified"}
                for item in snapshot.get("items", [])
            ),
            key=lambda item: str(item.get("id")),
        ),
        "page_hashes": snapshot.get("page_hashes", {}),
        "page_objects": snapshot.get("page_objects", {}),
    }


def snapshot_ids(snapshot: dict[str, Any]) -> set[str]:
    return {str(item["id"]) for item in snapshot.get("items", []) if item.get("id")}


def page_topology(snapshot: dict[str, Any], section_id: str | None = None) -> list[tuple[Any, ...]]:
    pages = [
        item
        for item in snapshot.get("items", [])
        if item.get("resource_type") == "page"
        and (section_id is None or item.get("section_id") == section_id)
    ]
    pages.sort(key=lambda item: (str(item.get("section_id")), int(item.get("order", 0))))
    return [
        (
            item.get("id"),
            item.get("section_id"),
            item.get("order"),
            item.get("page_level"),
            item.get("parent_page_id"),
        )
        for item in pages
    ]


def assert_valid_page_tree(snapshot: dict[str, Any], section_id: str) -> None:
    pages = [
        item
        for item in snapshot.get("items", [])
        if item.get("resource_type") == "page" and item.get("section_id") == section_id
    ]
    pages.sort(key=lambda item: int(item.get("order", 0)))
    stack: list[dict[str, Any]] = []
    for index, page in enumerate(pages):
        level = int(page.get("page_level", 0))
        if level < 1 or (index == 0 and level != 1):
            raise InvariantFailure("Page tree has an invalid first/root level.")
        if index and level > int(pages[index - 1].get("page_level", 0)) + 1:
            raise InvariantFailure("Page tree level jumps by more than one.")
        while stack and int(stack[-1].get("page_level", 0)) >= level:
            stack.pop()
        expected_parent = stack[-1].get("id") if stack else None
        if page.get("parent_page_id") != expected_parent:
            raise InvariantFailure("Page parent_page_id does not match the level-derived tree.")
        stack.append(page)


def is_descendant_of(snapshot: dict[str, Any], object_id: str, ancestor_id: str) -> bool:
    by_id = {str(item["id"]): item for item in snapshot.get("items", []) if item.get("id")}
    current = by_id.get(object_id)
    seen: set[str] = set()
    while current is not None:
        parent_id = current.get("parent_id")
        if parent_id == ancestor_id:
            return True
        if not parent_id or parent_id in seen:
            return False
        seen.add(parent_id)
        current = by_id.get(str(parent_id))
    return False


def assert_restored(before: dict[str, Any], restored: dict[str, Any]) -> None:
    if comparable_snapshot(before) != comparable_snapshot(restored):
        raise RestoreFailure(
            "Restored snapshot does not match the before snapshot; inspect artifacts manually."
        )


def manifest_path(run_dir: Path) -> Path:
    return run_dir / "manifest.json"


def load_manifest(run_dir: Path) -> dict[str, Any]:
    manifest = read_json(manifest_path(run_dir))
    if manifest.get("schema_version") != 1:
        raise RunnerFailure("Unsupported or missing manifest schema_version.")
    if not isinstance(manifest.get("structure"), dict):
        raise RunnerFailure("Manifest does not contain a prepared structure.")
    return manifest


def resolve_manifest_item(manifest: dict[str, Any], key: str) -> dict[str, Any]:
    item = manifest["structure"].get(key)
    if not isinstance(item, dict) or not item.get("id"):
        raise RunnerFailure(f"Manifest is missing structure.{key}.")
    return item


def find_snapshot_item(snapshot: dict[str, Any], object_id: str) -> dict[str, Any] | None:
    return next((item for item in snapshot.get("items", []) if item.get("id") == object_id), None)


def scenario_dir(run_dir: Path, scenario: str) -> Path:
    return run_dir / "scenarios" / scenario


def validate_manifest_notebook(manifest: dict[str, Any], requested_name: str | None) -> str:
    notebook = manifest.get("notebook", {})
    if requested_name and display_name(notebook).casefold() != requested_name.casefold():
        raise RunnerFailure(
            f"--notebook-name '{requested_name}' does not match manifest notebook "
            f"'{display_name(notebook)}'."
        )
    notebook_id = notebook.get("id")
    if not notebook_id:
        raise RunnerFailure("Manifest is missing notebook.id.")
    return str(notebook_id)


def dry_run_result(
    command: str,
    policy: ScenarioPolicy,
    tools: set[str],
    target: str | None,
    options: RuntimeOptions,
) -> dict[str, Any]:
    return {
        "command": command,
        "dry_run": True,
        "target": target,
        "mutation_policy": policy.as_dict(),
        "copy_budget": {
            field: value for field, (_env_name, value) in COPY_BUDGET_ENV.items()
        },
        "timeout_seconds": options.timeout,
        "tool_allowlist": sorted(tools),
        "run_dir": str(options.run_dir.resolve()),
        "server_started": False,
    }


__all__ = [
    "assert_restored",
    "assert_valid_page_tree",
    "capture_snapshot",
    "comparable_snapshot",
    "display_name",
    "dry_run_result",
    "find_snapshot_item",
    "flatten_tree",
    "installed_runner_version",
    "is_descendant_of",
    "load_manifest",
    "manifest_path",
    "page_content_hash",
    "page_topology",
    "read_json",
    "resolve_manifest_item",
    "scenario_dir",
    "snapshot_ids",
    "stable_item",
    "timestamp",
    "utc_now",
    "validate_manifest_notebook",
    "write_json",
]
