"""Typed fixture-building primitives shared by scenario profiles."""

from __future__ import annotations

import base64
from pathlib import Path
import platform
import sys
from typing import Any
import xml.etree.ElementTree as ET

from local_onenote_mcp.page import text_from_page_xml

from ...mcp_stdio_client import (
    COPY_NO_DELETE_POLICY,
    COPY_POLICY,
    DELETE_POLICY,
    MCPStdioClient,
    MOVE_POLICY,
    READ_ONLY_POLICY,
    RECONSTRUCTIVE_MOVE_PAGE_POLICY,
    WRITE_POLICY,
)
from ...runtime import EXIT_MCP, InvariantFailure, RunnerFailure
from ...test_utils import (
    display_name,
    installed_runner_version,
    stable_item,
    utc_now,
    write_json,
)
from .config import (
    AUTOMATED_COPY_CAPABILITIES,
    COPY_FIXTURE_MARKER,
    COPY_FIXTURE_PNG,
    RELAXED_COPY_CAPABILITIES,
)
from .lookup import exactly_one

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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Idempotently add stable rich-text, table, and image Copy fixtures."""

    page_id = str(page["id"])
    section_id = str(page["section_id"])
    xml = str(
        (await client.call_tool("get_page_xml", {"page_id": page_id, "page_info": "all"}))["xml"]
    )
    has_table = any(node.tag.rsplit("}", 1)[-1] == "Table" for node in ET.fromstring(xml).iter())

    async def current_page() -> dict[str, Any]:
        listed = await client.call_tool("list_pages", {"section_id": section_id})
        current = next((item for item in listed.get("pages", []) if item.get("id") == page_id), None)
        if current is None:
            raise RunnerFailure(f"Copy fixture Page disappeared: {page_id}", EXIT_MCP)
        return current

    if COPY_FIXTURE_MARKER not in xml or not has_table:
        current = await current_page()
        await client.call_tool(
            "append_to_page",
            {
                "page_id": page_id,
                "content": (
                    f"<p><strong>{COPY_FIXTURE_MARKER}</strong> "
                    "<em>rich text</em> <span style=\"color:#2F5597\">formatted</span></p>"
                    "<table><tr><th>Fixture</th><th>Value</th></tr>"
                    "<tr><td>Copy</td><td>Table</td></tr></table>"
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
        image_path = asset_dir / "copy-fixture-1x1.png"
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
    if COPY_FIXTURE_MARKER not in final_xml or not has_table:
        raise InvariantFailure("Prepared Copy fixture does not contain the rich-text/table marker.")
    if not any(item.get("kind") == "Image" for item in final_objects if isinstance(item, dict)):
        raise InvariantFailure("Prepared Copy fixture does not contain an Image object.")
    current = await current_page()
    evidence = {
        "page_id": page_id,
        "marker": COPY_FIXTURE_MARKER,
        "automated_content": ["rich_text", "table", "image"],
        "manual_content": ["file_attachment", "ink", "media"],
        "observed_object_types": sorted(
            {
                str(item.get("kind"))
                for item in final_objects
                if isinstance(item, dict) and item.get("kind")
            }
        ),
    }
    return current, evidence


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

def new_manifest(
    run_dir: Path,
    notebook: dict[str, Any],
    structure: dict[str, Any],
    *,
    notebook_path: str | None = None,
) -> dict[str, Any]:
    disposable_targets = {
        "notebook_copy_root": str((run_dir / "notebook-copies").resolve()),
    }
    if notebook_path:
        disposable_targets["source_notebook_path"] = str(Path(notebook_path).resolve())
    return {
        "schema_version": 1,
        "run_id": run_dir.name,
        "created_at": utc_now(),
        "runner": "tests/manual_validation/run.py",
        "local_onenote_mcp_version": installed_runner_version(),
        "python": sys.version,
        "platform": platform.platform(),
        "notebook": stable_item(notebook),
        "structure": {key: stable_item(value) for key, value in structure.items()},
        "disposable_targets": disposable_targets,
        "scenario_policies": {
            "inspect_read_report": READ_ONLY_POLICY.as_dict(),
            "create_rename_reorder": WRITE_POLICY.as_dict(),
            "move": MOVE_POLICY.as_dict(),
            "delete": DELETE_POLICY.as_dict(),
            "copy": COPY_POLICY.as_dict(),
            "copy_notebook": COPY_NO_DELETE_POLICY.as_dict(),
            "reconstructive_move_page": RECONSTRUCTIVE_MOVE_PAGE_POLICY.as_dict(),
        },
        "retry_policy": {
            "mutation_attempts": 1,
            "read_attempts": 2,
            "note": "Only transport failures on read-only calls are retried.",
        },
        "copy_scenario": {
            "supported": True,
            "real_backend_confirmed": True,
            "validated_content_types": sorted(AUTOMATED_COPY_CAPABILITIES),
        },
    }
