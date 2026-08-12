"""Typed fixture-building primitives shared by scenario profiles."""

from __future__ import annotations

import base64
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

from local_onenote_mcp.page import page_content_capability_projection, text_from_page_xml

from ...mcp_stdio_client import MCPStdioClient
from ...runtime import EXIT_MCP, InvariantFailure, RunnerFailure
from ...test_utils import (
    display_name,
    write_json,
)
from .config import (
    AUTOMATED_COPY_CAPABILITIES,
    COPY_FIXTURE_MARKER,
    COPY_FIXTURE_PNG,
    RELAXED_COPY_CAPABILITIES,
    REPARENT_PAGE_FIXTURE_MARKER,
)
from .lookup import exactly_one


INLINE_EQUATION_MARKER = "Inline equation fixture:"
DISPLAY_EQUATION_MARKER = "Display equation fixture:"
MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML"
REDUNDANT_DISPLAY_BREAK_PATTERN = re.compile(
    r"<br\s*/?>\s*(?=<math\b(?=[^>]*\bdisplay\s*=\s*(['\"])block\1)[^>]*>)",
    flags=re.IGNORECASE,
)
DISPLAY_MATHML_PATTERN = re.compile(
    r"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\b"
    r"(?=[^>]*\bdisplay\s*=\s*(['\"])block\1)[^>]*>"
    r".*?</(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
EXPECTED_EQUATION_EVIDENCE = {
    "mathml_roots": 2,
    "inline_equations": 1,
    "display_equations": 1,
    "namespace_declarations": 2,
    "redundant_breaks_before_display": 0,
    "standalone_display_oes": 1,
    "nonempty_display_predecessors": 1,
    "empty_oes_before_display": 0,
}


def _equation_evidence(xml: str) -> dict[str, int]:
    root = ET.fromstring(xml)
    projection = page_content_capability_projection(xml)
    tags = projection.get("embedded_markup_tag_counts", {})
    attributes = projection.get("embedded_markup_attribute_name_counts", {})
    roots = int(tags.get("math", 0))
    display = int(attributes.get("math@display", 0))
    redundant_breaks = sum(
        len(REDUNDANT_DISPLAY_BREAK_PATTERN.findall(node.text or ""))
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] == "T"
    )
    parents = {id(child): parent for parent in root.iter() for child in list(parent)}
    standalone_display_oes = 0
    nonempty_display_predecessors = 0
    empty_oes_before_display = 0
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "T" or not node.text:
            continue
        matches = list(DISPLAY_MATHML_PATTERN.finditer(node.text))
        if len(matches) != 1:
            continue
        residual = DISPLAY_MATHML_PATTERN.sub("", node.text, count=1)
        residual = re.sub(r"</?span\b[^>]*>", "", residual, flags=re.IGNORECASE)
        oe = parents.get(id(node))
        if (
            residual.strip()
            or oe is None
            or oe.tag.rsplit("}", 1)[-1] != "OE"
            or [child.tag.rsplit("}", 1)[-1] for child in list(oe)] != ["T"]
        ):
            continue
        standalone_display_oes += 1
        container = parents.get(id(oe))
        siblings = list(container) if container is not None else []
        index = siblings.index(oe) if oe in siblings else -1
        predecessor = siblings[index - 1] if index > 0 else None
        predecessor_has_content = predecessor is not None and any(
            (candidate.text or "").strip()
            for candidate in predecessor.iter()
            if candidate.tag.rsplit("}", 1)[-1] == "T"
        )
        if predecessor_has_content:
            nonempty_display_predecessors += 1
        elif predecessor is not None:
            empty_oes_before_display += 1
    return {
        "mathml_roots": roots,
        "inline_equations": max(roots - display, 0),
        "display_equations": display,
        "namespace_declarations": int(attributes.get("math@xmlns", 0)),
        "redundant_breaks_before_display": redundant_breaks,
        "standalone_display_oes": standalone_display_oes,
        "nonempty_display_predecessors": nonempty_display_predecessors,
        "empty_oes_before_display": empty_oes_before_display,
    }


def _has_exact_equation_fixture(xml: str) -> bool:
    report = _equation_fixture_report(xml)
    return report["passed"] is True


def _equation_fixture_report(xml: str) -> dict[str, Any]:
    actual = _equation_evidence(xml)
    count_mismatches = {
        name: {"actual": actual.get(name), "expected": expected}
        for name, expected in EXPECTED_EQUATION_EVIDENCE.items()
        if actual.get(name) != expected
    }
    marker_checks = {
        "inline_marker_present": INLINE_EQUATION_MARKER in xml,
        "display_marker_present": DISPLAY_EQUATION_MARKER in xml,
    }
    marker_mismatches = {
        name: {"actual": present, "expected": True}
        for name, present in marker_checks.items()
        if not present
    }
    mismatches = {**count_mismatches, **marker_mismatches}
    return {
        "actual": actual,
        "expected": dict(EXPECTED_EQUATION_EVIDENCE),
        "marker_checks": marker_checks,
        "mismatches": mismatches,
        "passed": not mismatches,
    }


async def ensure_group(client: MCPStdioClient, parent_id: str, name: str) -> dict[str, Any]:
    listed = await client.call_tool(
        "list_section_groups",
        {"parent_id": parent_id, "recursive": False},
    )
    existing = exactly_one(listed.get("items", []), name, "section group")
    if existing:
        return existing
    return (
        await client.call_tool("create_section_group", {"parent_id": parent_id, "group_name": name})
    )["section_group"]

async def ensure_section(client: MCPStdioClient, parent_id: str, name: str) -> dict[str, Any]:
    listed = await client.call_tool(
        "list_sections",
        {"parent_id": parent_id, "recursive": False},
    )
    existing = exactly_one(listed.get("sections", []), name, "section")
    if existing:
        return existing
    return (
        await client.call_tool("create_section", {"parent_id": parent_id, "section_name": name})
    )["section"]

async def ensure_page(
    client: MCPStdioClient,
    section_id: str,
    title: str,
    content: str,
) -> dict[str, Any]:
    listed = await client.call_tool("list_pages", {"section_id": section_id})
    existing = exactly_one(listed.get("pages", []), title, "page")
    if existing:
        return existing
    return (
        await client.call_tool(
            "create_page",
            {
                "section_id": section_id,
                "title": title,
                "content": content,
                "content_format": "plain",
                "new_page_style": "blank_with_title",
            },
        )
    )["page"]

async def enforce_page_position(
    client: MCPStdioClient,
    section_id: str,
    page_id: str,
    after_page_id: str,
    page_level: int,
) -> dict[str, Any]:
    listed = await client.call_tool("list_pages", {"section_id": section_id})
    pages = sorted(listed["pages"], key=lambda item: int(item["order"]))
    page = next((item for item in pages if item["id"] == page_id), None)
    if page is None:
        raise RunnerFailure(f"Prepared page disappeared: {page_id}", EXIT_MCP)
    index = pages.index(page)
    actual_after = "" if index == 0 else str(pages[index - 1]["id"])
    if actual_after == after_page_id and int(page["page_level"]) == page_level:
        return page
    result = await client.call_tool(
        "reorder_page",
        {
            "page_id": page_id,
            "expected_title": display_name(page),
            "expected_section_id": section_id,
            "after_page_id": after_page_id,
            "page_level": page_level,
            "expected_modified": page.get("modified"),
        },
    )
    return result["item"]

async def ensure_copy_rich_fixture(
    client: MCPStdioClient,
    page: dict[str, Any],
    run_dir: Path,
    *,
    marker: str = COPY_FIXTURE_MARKER,
    fixture_label: str = "Copy",
    asset_filename: str = "copy-fixture-1x1.png",
    include_equations: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Idempotently add stable rich-text, table, image, and optional equations."""

    page_id = str(page["id"])
    section_id = str(page["section_id"])
    xml = str(
        (await client.call_tool("get_page_xml", {"page_id": page_id, "page_info": "all"}))["xml"]
    )
    has_table = any(node.tag.rsplit("}", 1)[-1] == "Table" for node in ET.fromstring(xml).iter())
    has_equations = not include_equations or _has_exact_equation_fixture(xml)

    async def current_page() -> dict[str, Any]:
        listed = await client.call_tool("list_pages", {"section_id": section_id})
        current = next((item for item in listed.get("pages", []) if item.get("id") == page_id), None)
        if current is None:
            raise RunnerFailure(f"Copy fixture Page disappeared: {page_id}", EXIT_MCP)
        return current

    if marker not in xml or not has_table or not has_equations:
        current = await current_page()
        equation_html = ""
        if include_equations:
            equation_html = (
                f"<p>{INLINE_EQUATION_MARKER} before "
                f'<math xmlns="{MATHML_NAMESPACE}"><mrow><mi>E</mi><mo>=</mo>'
                "<mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></mrow></math>"
                " after.</p>"
                f"<span>{DISPLAY_EQUATION_MARKER}</span>"
                f'<math xmlns="{MATHML_NAMESPACE}" display="block"><mrow>'
                "<mi>x</mi><mo>=</mo><mfrac><mrow><mo>−</mo><mi>b</mi><mo>±</mo>"
                "<msqrt><mrow><msup><mi>b</mi><mn>2</mn></msup><mo>−</mo>"
                "<mn>4</mn><mi>a</mi><mi>c</mi></mrow></msqrt></mrow>"
                "<mrow><mn>2</mn><mi>a</mi></mrow></mfrac></mrow></math>"
            )
        await client.call_tool(
            "append_to_page",
            {
                "page_id": page_id,
                "content": (
                    f"<p><strong>{marker}</strong> "
                    "<em>rich text</em> <span style=\"color:#2F5597\">formatted</span></p>"
                    f"{equation_html}"
                    "<table><tr><th>Fixture</th><th>Value</th></tr>"
                    f"<tr><td>{fixture_label}</td><td>Table</td></tr></table>"
                ),
                "content_format": "html",
                "expected_title": display_name(current),
                "expected_section_id": section_id,
                "expected_modified": current.get("modified"),
                "x": 36.0,
                "y": 180.0,
            },
        )

    objects = (
        await client.call_tool("get_page_objects", {"page_id": page_id})
    ).get("objects", [])
    if not any(item.get("kind") == "Image" for item in objects if isinstance(item, dict)):
        asset_dir = run_dir / "fixture-assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        image_path = asset_dir / asset_filename
        if not image_path.exists():
            image_path.write_bytes(base64.b64decode(COPY_FIXTURE_PNG))
        current = await current_page()
        await client.call_tool(
            "add_image_to_page",
            {
                "page_id": page_id,
                "image_path": str(image_path.resolve()),
                "image_format": "png",
                "expected_title": display_name(current),
                "expected_section_id": section_id,
                "expected_modified": current.get("modified"),
                "x": 36.0,
                "y": 300.0,
                "width": 24.0,
                "height": 24.0,
            },
        )

    final_xml = str(
        (await client.call_tool("get_page_xml", {"page_id": page_id, "page_info": "all"}))["xml"]
    )
    final_objects = (
        await client.call_tool("get_page_objects", {"page_id": page_id})
    ).get("objects", [])
    has_table = any(node.tag.rsplit("}", 1)[-1] == "Table" for node in ET.fromstring(final_xml).iter())
    has_image = any(
        item.get("kind") == "Image" for item in final_objects if isinstance(item, dict)
    )
    equation_report = _equation_fixture_report(final_xml) if include_equations else None
    has_equations = equation_report is None or equation_report["passed"] is True
    structure_checks = {
        "rich_text_marker_present": marker in final_xml,
        "table_present": has_table,
        "image_present": has_image,
        "equations_passed": has_equations,
    }
    if include_equations:
        object_kinds = [
            str(item.get("kind"))
            for item in final_objects
            if isinstance(item, dict) and item.get("kind")
        ]
        detection = {
            "schema_version": 1,
            "fixture_label": fixture_label,
            "page_id": page_id,
            "checks": structure_checks,
            "equations": equation_report,
            "object_kind_counts": {
                kind: object_kinds.count(kind) for kind in sorted(set(object_kinds))
            },
            "passed": all(structure_checks.values()),
        }
        detection_path = run_dir / "fixture-equation-detection.json"
        write_json(detection_path, detection)
    if not all(structure_checks.values()):
        equation_mismatches = (
            ",".join(equation_report["mismatches"])
            if equation_report and equation_report["mismatches"]
            else "none"
        )
        raise InvariantFailure(
            f"Prepared {fixture_label} fixture does not contain the required "
            "rich-text/table/image/equation structure: "
            f"marker={structure_checks['rich_text_marker_present']}; "
            f"table={structure_checks['table_present']}; "
            f"image={structure_checks['image_present']}; "
            f"equation_mismatches={equation_mismatches}; "
            "evidence=fixture-equation-detection.json."
        )
    current = await current_page()
    automated_content = ["rich_text", "table", "image"]
    equation_evidence = None
    if include_equations:
        automated_content.extend(("inline_equation", "display_equation"))
        equation_evidence = equation_report["actual"]
    evidence = {
        "page_id": page_id,
        "marker": marker,
        "automated_content": automated_content,
        "manual_content": ["ink", "shape", "media"],
        "observed_object_types": sorted(
            {
                str(item.get("kind"))
                for item in final_objects
                if isinstance(item, dict) and item.get("kind")
            }
        ),
    }
    if equation_evidence is not None:
        evidence["equations"] = equation_evidence
    return current, evidence


async def ensure_reparent_page_rich_fixture(
    client: MCPStdioClient,
    page: dict[str, Any],
    run_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add the reparent-specific rich content marker without Copy semantics."""

    return await ensure_copy_rich_fixture(
        client,
        page,
        run_dir,
        marker=REPARENT_PAGE_FIXTURE_MARKER,
        fixture_label="Reparent",
        asset_filename="reparent-page-fixture-1x1.png",
    )


async def ensure_copy_list_tag_fixture(
    client: MCPStdioClient,
    page: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Idempotently add three native mixed List/To-Do items without raw XML."""

    page_id = str(page["id"])
    section_id = str(page["section_id"])
    fixture_text = ("为", "答复", "3发送")
    xml = str(
        (await client.call_tool(
            "get_page_xml",
            {"page_id": page_id, "page_info": "all"},
        ))["xml"]
    )

    def semantic_counts(value: str) -> tuple[int, int, int]:
        nodes = list(ET.fromstring(value).iter())
        return (
            sum(node.tag.rsplit("}", 1)[-1] == "List" for node in nodes),
            sum(node.tag.rsplit("}", 1)[-1] == "Tag" for node in nodes),
            sum(node.tag.rsplit("}", 1)[-1] == "TagDef" for node in nodes),
        )

    async def current_page() -> dict[str, Any]:
        listed = await client.call_tool("list_pages", {"section_id": section_id})
        current = next((item for item in listed.get("pages", []) if item.get("id") == page_id), None)
        if current is None:
            raise RunnerFailure(f"List/Tag fixture Page disappeared: {page_id}", EXIT_MCP)
        return current

    list_count, tag_count, tag_def_count = semantic_counts(xml)
    visible_text = text_from_page_xml(xml)
    fixture_complete = (
        list_count == 3
        and tag_count == 3
        and tag_def_count >= 1
        and all(value in visible_text for value in fixture_text)
    )
    if not fixture_complete and (list_count or tag_count or tag_def_count):
        raise InvariantFailure(
            "Programmatic List/Tag fixture found partial pre-existing semantic content; "
            "the fresh Page was not modified further."
        )
    if not fixture_complete:
        current = await current_page()
        await client.call_tool(
            "append_to_page",
            {
                "page_id": page_id,
                "content": (
                    '<ol><li data-tag="to-do:completed">为</li>'
                    '<li data-tag="to-do">答复</li></ol>'
                    '<ul><li data-tag="to-do:completed">3发送</li></ul>'
                ),
                "content_format": "html",
                "expected_title": display_name(current),
                "expected_section_id": section_id,
                "expected_modified": current.get("modified"),
                "x": 36.0,
                "y": 120.0,
            },
        )

    final_xml = str(
        (await client.call_tool(
            "get_page_xml",
            {"page_id": page_id, "page_info": "all"},
        ))["xml"]
    )
    list_count, tag_count, tag_def_count = semantic_counts(final_xml)
    visible_text = text_from_page_xml(final_xml)
    capabilities = set()
    if list_count == 3:
        capabilities.add("List")
    if tag_count == 3 and tag_def_count >= 1:
        capabilities.add("Tag")
    missing = sorted(RELAXED_COPY_CAPABILITIES - capabilities)
    missing_text = [value for value in fixture_text if value not in visible_text]
    if missing or missing_text:
        raise InvariantFailure(
            "Programmatic List/Tag fixture is incomplete; "
            f"missing capabilities: {missing}; missing visible text: {missing_text}."
        )
    evidence = {
        "page_id": page_id,
        "verification_tier": "semantic_list_tag",
        "generated_content": [
            {"list": "number", "text": "为", "tag": "to-do", "completed": True},
            {"list": "number", "text": "答复", "tag": "to-do", "completed": False},
            {"list": "bullet", "text": "3发送", "tag": "to-do", "completed": True},
        ],
        "observed_capabilities": sorted(capabilities),
        "observed_counts": {
            "List": list_count,
            "Tag": tag_count,
            "TagDef": tag_def_count,
        },
    }
    return await current_page(), evidence
