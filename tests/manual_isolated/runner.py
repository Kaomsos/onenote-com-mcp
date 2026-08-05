"""Scenario orchestration for user-triggered isolated OneNote smoke tests."""

from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version as package_version
import json
from pathlib import Path
import platform
import sys
from typing import Any, Iterable
import uuid
import xml.etree.ElementTree as ET

from .mcp_stdio_client import (
    ClientFailure,
    COPY_BUDGET_ENV,
    COPY_NO_DELETE_POLICY,
    COPY_POLICY,
    DELETE_POLICY,
    MCPStdioClient,
    MOVE_POLICY,
    READ_ONLY_POLICY,
    RECONSTRUCTIVE_MOVE_PAGE_POLICY,
    ScenarioPolicy,
    WRITE_POLICY,
)


DEFAULT_NOTEBOOK_NAME = "__LOCAL_ONENOTE_MCP_ISOLATED__"
COPY_FIXTURE_MARKER = "LOCAL_ONENOTE_MCP_COPY_FIXTURE_V1"
COPY_FIXTURE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
AUTOMATED_COPY_CAPABILITIES = {"Image", "Outline", "RichText", "Table"}
EXIT_ARGUMENT = 2
EXIT_MCP = 3
EXIT_RESTORE = 4
EXIT_INVARIANT = 5

READ_TOOLS = {
    "health_check",
    "list_notebooks",
    "get_notebook",
    "list_section_groups",
    "list_sections",
    "list_pages",
    "get_tree",
    "get_page_xml",
    "get_page_objects",
}
BASELINE_TOOLS = READ_TOOLS | {"publish_object"}
CREATE_TOOLS = READ_TOOLS | {
    "add_image_to_page",
    "append_to_page",
    "create_notebook",
    "create_section_group",
    "create_section",
    "create_page",
    "reorder_page",
}
RENAME_TOOLS = READ_TOOLS | {"rename_section_group", "rename_section"}
REORDER_TOOLS = READ_TOOLS | {"reorder_page"}
MOVE_TOOLS = READ_TOOLS | {"move_section"}
DELETE_TOOLS = READ_TOOLS | {"delete_section_group", "delete_section", "delete_page"}
COPY_TOOLS = READ_TOOLS | {
    "plan_copy",
    "copy_page",
    "copy_section",
    "copy_section_group",
    "delete_page",
    "delete_section",
    "delete_section_group",
}
COPY_NOTEBOOK_TOOLS = READ_TOOLS | {"plan_copy", "copy_notebook", "close_notebook"}
RECONSTRUCTIVE_MOVE_PAGE_TOOLS = READ_TOOLS | {
    "plan_reconstructive_move_page",
    "reconstructive_move_page",
}


class RunnerFailure(RuntimeError):
    def __init__(self, message: str, exit_code: int = EXIT_ARGUMENT) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class InvariantFailure(RunnerFailure):
    def __init__(self, message: str) -> None:
        super().__init__(message, EXIT_INVARIANT)


class RestoreFailure(RunnerFailure):
    def __init__(self, message: str) -> None:
        super().__init__(message, EXIT_RESTORE)


@dataclass(frozen=True)
class RuntimeOptions:
    run_dir: Path
    timeout: int
    json_output: bool
    dry_run: bool


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_run_dir() -> Path:
    return Path(".local-validation") / timestamp()


def installed_runner_version() -> str:
    try:
        return package_version("local-onenote-mcp")
    except PackageNotFoundError:
        return "unknown"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


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
    "object_id",
    "callback_id",
    "format",
    "delete_supported",
    "delete_object_id",
)


def stable_item(item: dict[str, Any]) -> dict[str, Any]:
    return {field: item.get(field) for field in SNAPSHOT_FIELDS if field in item}


async def capture_snapshot(client: MCPStdioClient, notebook_id: str) -> dict[str, Any]:
    tree_result = await client.call_tool("get_tree", {"root_id": notebook_id, "max_depth": 8})
    tree = tree_result["tree"]
    items = flatten_tree(tree)
    pages = sorted(
        (item for item in items if item.get("resource_type") == "page"),
        key=lambda item: (str(item.get("section_id")), int(item.get("order", 0))),
    )
    page_hashes: dict[str, str] = {}
    page_objects: dict[str, list[dict[str, Any]]] = {}
    for page in pages:
        page_id = str(page["id"])
        xml_result = await client.call_tool("get_page_xml", {"page_id": page_id, "page_info": "all"})
        xml = str(xml_result["xml"])
        page_hashes[page_id] = hashlib.sha256(xml.encode("utf-8")).hexdigest()
        objects_result = await client.call_tool("get_page_objects", {"page_id": page_id})
        page_objects[page_id] = [
            {field: obj.get(field) for field in OBJECT_FIELDS if field in obj}
            for obj in objects_result.get("objects", [])
            if isinstance(obj, dict)
        ]
    return {
        "captured_at": utc_now(),
        "notebook_id": notebook_id,
        "items": [stable_item(item) for item in items],
        "page_hashes": page_hashes,
        "page_objects": page_objects,
    }


def comparable_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Strip capture time and normalize item order for restoration comparison."""

    return {
        "notebook_id": snapshot.get("notebook_id"),
        "items": sorted(
            ({key: value for key, value in item.items() if key != "modified"} for item in snapshot.get("items", [])),
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


def expected_copy_source_items(
    snapshot: dict[str, Any],
    source_id: str,
) -> list[dict[str, Any]]:
    items = snapshot.get("items", [])
    by_id = {item["id"]: item for item in items}
    source = by_id.get(source_id)
    if source is None:
        raise InvariantFailure(f"Copy source '{source_id}' is missing from the before snapshot.")
    if source["resource_type"] == "page":
        pages = sorted(
            (
                item
                for item in items
                if item.get("resource_type") == "page"
                and item.get("section_id") == source.get("section_id")
            ),
            key=lambda item: int(item.get("order", 0)),
        )
        start = next(index for index, item in enumerate(pages) if item["id"] == source_id)
        root_level = int(source.get("page_level", 1))
        selected = [source]
        for item in pages[start + 1 :]:
            if int(item.get("page_level", 1)) <= root_level:
                break
            selected.append(item)
        return selected

    def descendant(item: dict[str, Any]) -> bool:
        parent_id = item.get("parent_id")
        while parent_id:
            if parent_id == source_id:
                return True
            parent = by_id.get(parent_id)
            if parent is None:
                return False
            parent_id = parent.get("parent_id")
        return False

    return [item for item in items if item["id"] == source_id or descendant(item)]


def assert_copy_mapping(
    before: dict[str, Any],
    after: dict[str, Any],
    source_id: str,
    destination_parent_id: str | None,
    destination_name: str,
    copied: dict[str, Any],
) -> None:
    id_map = copied.get("copy_report", {}).get("id_map")
    if not isinstance(id_map, dict) or not id_map:
        raise InvariantFailure("Copy response does not contain a non-empty id_map.")
    source_items = expected_copy_source_items(before, source_id)
    source_by_id = {item["id"]: item for item in source_items}
    if set(id_map) != set(source_by_id):
        raise InvariantFailure("Copy id_map source IDs do not exactly match the planned source subtree.")
    target_ids = list(id_map.values())
    if len(set(target_ids)) != len(target_ids) or set(target_ids) & set(id_map):
        raise InvariantFailure("Copy id_map target IDs are not unique and disjoint from source IDs.")
    after_by_id = {item["id"]: item for item in after.get("items", [])}
    missing = sorted(set(target_ids) - set(after_by_id))
    if missing:
        raise InvariantFailure(f"Copy targets are missing from the after snapshot: {missing}")
    before_ids = {item["id"] for item in before.get("items", [])}
    unexpected_new_ids = (set(after_by_id) - before_ids) - set(target_ids)
    if unexpected_new_ids:
        raise InvariantFailure(
            f"Copy created active objects outside id_map: {sorted(unexpected_new_ids)}"
        )

    source_root = source_by_id[source_id]
    target_root = after_by_id[id_map[source_id]]
    if display_name(target_root) != destination_name:
        raise InvariantFailure("Copy target root name differs from the planned destination name.")
    source_root_level = int(source_root.get("page_level", 1))
    for old_id, new_id in id_map.items():
        source = source_by_id[old_id]
        target = after_by_id[new_id]
        if target.get("resource_type") != source.get("resource_type"):
            raise InvariantFailure("Copy id_map changed a resource type.")
        if old_id != source_id and display_name(target) != display_name(source):
            raise InvariantFailure("Copy changed a non-root resource name.")
        kind = source["resource_type"]
        if kind in {"section", "section_group"}:
            expected_parent = (
                destination_parent_id
                if old_id == source_id
                else id_map.get(source.get("parent_id"))
            )
            if target.get("parent_id") != expected_parent:
                raise InvariantFailure("Copy container parent mapping differs from id_map topology.")
        elif kind == "page":
            expected_section = (
                destination_parent_id
                if source_root["resource_type"] == "page"
                else id_map.get(source.get("section_id"))
            )
            if target.get("section_id") != expected_section:
                raise InvariantFailure("Copied Page is in the wrong target Section.")
            expected_parent_page = id_map.get(source.get("parent_page_id"))
            if target.get("parent_page_id") != expected_parent_page:
                raise InvariantFailure("Copied Page parent relation differs from the source subtree.")
            expected_level = int(source.get("page_level", 1))
            if source_root["resource_type"] == "page":
                expected_level = expected_level - source_root_level + 1
            if int(target.get("page_level", 1)) != expected_level:
                raise InvariantFailure("Copied Page relative page_level differs from the source subtree.")

    source_pages_by_section: dict[str, list[dict[str, Any]]] = {}
    for item in source_items:
        if item["resource_type"] == "page":
            source_pages_by_section.setdefault(str(item.get("section_id")), []).append(item)
    for pages in source_pages_by_section.values():
        expected_ids = [
            id_map[item["id"]]
            for item in sorted(pages, key=lambda item: int(item.get("order", 0)))
        ]
        target_section = after_by_id[expected_ids[0]].get("section_id")
        actual_ids = [
            item["id"]
            for item in sorted(
                (
                    after_by_id[target_id]
                    for target_id in expected_ids
                    if after_by_id[target_id].get("section_id") == target_section
                ),
                key=lambda item: int(item.get("order", 0)),
            )
        ]
        if actual_ids != expected_ids:
            raise InvariantFailure("Copied Page relative order differs from the source subtree.")


def assert_copy_fixture_capabilities(planned: dict[str, Any]) -> None:
    capabilities = set(planned.get("content_capabilities", []))
    missing = sorted(AUTOMATED_COPY_CAPABILITIES - capabilities)
    if missing:
        raise InvariantFailure(
            "Copy source is missing automated fixture capabilities "
            f"{missing}; run the explicit create scenario again before mutation."
        )


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
        raise RestoreFailure("Restored snapshot does not match the before snapshot; inspect artifacts manually.")


def exact_matches(items: Iterable[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    folded = name.casefold()
    return [item for item in items if display_name(item).casefold() == folded]


def exactly_one(items: Iterable[dict[str, Any]], name: str, label: str) -> dict[str, Any] | None:
    matches = exact_matches(items, name)
    if len(matches) > 1:
        paths = ", ".join(str(item.get("path")) for item in matches)
        raise RunnerFailure(f"Duplicate {label} named '{name}': {paths}")
    return matches[0] if matches else None


async def resolve_notebook(
    client: MCPStdioClient,
    *,
    notebook_name: str | None = None,
    notebook_id: str | None = None,
) -> dict[str, Any]:
    if bool(notebook_name) == bool(notebook_id):
        raise RunnerFailure("Specify exactly one of --notebook-name or --notebook-id.")
    if notebook_id:
        return (await client.call_tool("get_notebook", {"notebook_id": notebook_id}))["item"]
    listed = await client.call_tool("list_notebooks", {})
    notebook = exactly_one(listed.get("notebooks", []), str(notebook_name), "notebook")
    if notebook is None:
        raise RunnerFailure(f"No active notebook has the exact name '{notebook_name}'.")
    return notebook


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
    if not any(item.get("type") == "Image" for item in objects if isinstance(item, dict)):
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
    if not any(item.get("type") == "Image" for item in final_objects if isinstance(item, dict)):
        raise InvariantFailure("Prepared Copy fixture does not contain an Image object.")
    current = await current_page()
    evidence = {
        "page_id": page_id,
        "marker": COPY_FIXTURE_MARKER,
        "automated_content": ["rich_text", "table", "image"],
        "manual_content": ["file_attachment", "ink", "media"],
        "observed_object_types": sorted(
            {
                str(item.get("type"))
                for item in final_objects
                if isinstance(item, dict) and item.get("type")
            }
        ),
    }
    return current, evidence


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


def new_manifest(run_dir: Path, notebook: dict[str, Any], structure: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_dir.name,
        "created_at": utc_now(),
        "runner": "tests/manual_isolated/run.py",
        "local_onenote_mcp_version": installed_runner_version(),
        "python": sys.version,
        "platform": platform.platform(),
        "notebook": stable_item(notebook),
        "structure": {key: stable_item(value) for key, value in structure.items()},
        "disposable_targets": {
            "notebook_copy_root": str((run_dir / "notebook-copies").resolve()),
        },
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
        "copy_scenario": {"supported": True, "real_backend_confirmed": False},
    }


async def command_inspect(args: argparse.Namespace, options: RuntimeOptions) -> dict[str, Any]:
    if options.dry_run:
        return dry_run_result("inspect", READ_ONLY_POLICY, READ_TOOLS, args.notebook_name, options)
    async with MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools=READ_TOOLS,
        run_dir=options.run_dir,
        timeout_seconds=options.timeout,
    ) as client:
        notebook = await resolve_notebook(client, notebook_name=args.notebook_name)
        tree = await client.call_tool("get_tree", {"root_id": notebook["id"], "max_depth": 8})
        result = {
            "command": "inspect",
            "notebook": stable_item(notebook),
            "tree": tree["tree"],
            "mutation_policy": READ_ONLY_POLICY.as_dict(),
            "run_dir": str(options.run_dir.resolve()),
        }
        write_json(options.run_dir / "inspect.json", result)
        return result


async def command_create(args: argparse.Namespace, options: RuntimeOptions) -> dict[str, Any]:
    if options.dry_run:
        result = dry_run_result("create", WRITE_POLICY, CREATE_TOOLS, args.notebook_name, options)
        result["planned_structure"] = [
            "Group-A/Move-Source/{Parent[rich text+table+image],Child,Sibling}",
            "Group-B",
            "Delete-Sandbox/Disposable-Group",
            "Delete-Sandbox/Disposable-Section/Disposable-Page",
        ]
        return result
    async with MCPStdioClient(
        policy=WRITE_POLICY,
        allowed_tools=CREATE_TOOLS,
        run_dir=options.run_dir,
        timeout_seconds=options.timeout,
    ) as client:
        listed = await client.call_tool("list_notebooks", {})
        notebook = exactly_one(listed.get("notebooks", []), args.notebook_name, "notebook")
        created_notebook = notebook is None
        if notebook is None:
            created = await client.call_tool(
                "create_notebook",
                {"name_or_path": args.notebook_name, "base_folder": args.base_folder},
            )
            notebook = created["item"]

        group_a = await ensure_group(client, notebook["id"], "Group-A")
        group_b = await ensure_group(client, notebook["id"], "Group-B")
        delete_sandbox = await ensure_group(client, notebook["id"], "Delete-Sandbox")
        move_source = await ensure_section(client, group_a["id"], "Move-Source")
        disposable_group = await ensure_group(client, delete_sandbox["id"], "Disposable-Group")
        disposable_section = await ensure_section(client, delete_sandbox["id"], "Disposable-Section")
        token = str(uuid.uuid4())
        parent = await ensure_page(client, move_source["id"], "Parent", f"Parent smoke token: {token}")
        child = await ensure_page(client, move_source["id"], "Child", f"Child smoke token: {token}")
        sibling = await ensure_page(client, move_source["id"], "Sibling", f"Sibling smoke token: {token}")
        disposable_page = await ensure_page(
            client,
            disposable_section["id"],
            "Disposable-Page",
            f"Disposable smoke token: {token}",
        )
        parent = await enforce_page_position(client, move_source["id"], parent["id"], "", 1)
        child = await enforce_page_position(client, move_source["id"], child["id"], parent["id"], 2)
        sibling = await enforce_page_position(client, move_source["id"], sibling["id"], child["id"], 1)
        parent, copy_fixture = await ensure_copy_rich_fixture(client, parent, options.run_dir)
        snapshot = await capture_snapshot(client, notebook["id"])
        structure = {
            "group_a": group_a,
            "group_b": group_b,
            "delete_sandbox": delete_sandbox,
            "move_source": move_source,
            "parent_page": parent,
            "child_page": child,
            "sibling_page": sibling,
            "disposable_group": disposable_group,
            "disposable_section": disposable_section,
            "disposable_page": disposable_page,
        }
        manifest = new_manifest(options.run_dir, notebook, structure)
        manifest["copy_fixture"] = copy_fixture
        write_json(manifest_path(options.run_dir), manifest)
        write_json(options.run_dir / "prepared.json", snapshot)
        write_json(options.run_dir / "page-hashes.json", snapshot["page_hashes"])
        render_report(options.run_dir)
        return {
            "command": "create",
            "created_notebook": created_notebook,
            "notebook": stable_item(notebook),
            "structure_ids": {key: value["id"] for key, value in structure.items()},
            "copy_fixture": copy_fixture,
            "run_dir": str(options.run_dir.resolve()),
        }


async def command_read(args: argparse.Namespace, options: RuntimeOptions) -> dict[str, Any]:
    if options.dry_run:
        target = args.notebook_name or args.notebook_id
        result = dry_run_result("read", READ_ONLY_POLICY, BASELINE_TOOLS, target, options)
        result["export_onepkg"] = bool(args.export_onepkg)
        return result
    async with MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools=BASELINE_TOOLS,
        run_dir=options.run_dir,
        timeout_seconds=options.timeout,
    ) as client:
        notebook = await resolve_notebook(
            client,
            notebook_name=args.notebook_name,
            notebook_id=args.notebook_id,
        )
        snapshot = await capture_snapshot(client, notebook["id"])
        write_json(options.run_dir / "before.json", snapshot)
        write_json(options.run_dir / "page-hashes.json", snapshot["page_hashes"])
        onepkg_path: Path | None = None
        if args.export_onepkg:
            onepkg_path = (options.run_dir / "baseline.onepkg").resolve()
            if onepkg_path.exists():
                raise RunnerFailure(f"Refusing to overwrite existing baseline export: {onepkg_path}")
            await client.call_tool(
                "publish_object",
                {
                    "object_id": notebook["id"],
                    "target_path": str(onepkg_path),
                    "format": "onepkg",
                    "overwrite": False,
                },
            )
        existing = manifest_path(options.run_dir)
        if not existing.exists():
            write_json(
                existing,
                {
                    "schema_version": 1,
                    "run_id": options.run_dir.name,
                    "created_at": utc_now(),
                    "runner": "tests/manual_isolated/run.py",
                    "local_onenote_mcp_version": installed_runner_version(),
                    "python": sys.version,
                    "platform": platform.platform(),
                    "notebook": stable_item(notebook),
                    "structure": {},
                },
            )
        render_report(options.run_dir)
        return {
            "command": "read",
            "notebook": stable_item(notebook),
            "pages_hashed": len(snapshot["page_hashes"]),
            "baseline_onepkg": str(onepkg_path) if onepkg_path else None,
            "run_dir": str(options.run_dir.resolve()),
        }


def find_snapshot_item(snapshot: dict[str, Any], object_id: str) -> dict[str, Any] | None:
    return next((item for item in snapshot.get("items", []) if item.get("id") == object_id), None)


def scenario_dir(run_dir: Path, scenario: str) -> Path:
    return run_dir / "scenarios" / scenario


def validate_manifest_notebook(manifest: dict[str, Any], requested_name: str | None) -> str:
    notebook = manifest.get("notebook", {})
    if requested_name and display_name(notebook).casefold() != requested_name.casefold():
        raise RunnerFailure(
            f"--notebook-name '{requested_name}' does not match manifest notebook '{display_name(notebook)}'."
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


async def run_rename(args: argparse.Namespace, options: RuntimeOptions, manifest: dict[str, Any]) -> dict[str, Any]:
    target_key = args.target
    target = resolve_manifest_item(manifest, target_key)
    resource_type = target.get("resource_type")
    if resource_type not in {"section", "section_group"}:
        raise RunnerFailure("Rename target must be a section or section group.")
    tool = "rename_section" if resource_type == "section" else "rename_section_group"
    id_key = "section_id" if resource_type == "section" else "section_group_id"
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    out = scenario_dir(options.run_dir, "rename")
    async with MCPStdioClient(
        policy=WRITE_POLICY,
        allowed_tools=RENAME_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        current = find_snapshot_item(before, target["id"])
        if current is None:
            raise RunnerFailure("Rename target is not active in the current notebook snapshot.")
        original_name = display_name(current)
        new_name = args.new_name or f"{original_name}-Smoke-Renamed"
        if new_name == original_name:
            raise RunnerFailure("--new-name must differ from the current name.")
        forward = await client.call_tool(
            tool,
            {
                id_key: current["id"],
                "new_name": new_name,
                "expected_name": original_name,
                "expected_parent_id": current["parent_id"],
                "expected_modified": current.get("modified"),
            },
        )
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        changed = find_snapshot_item(after, current["id"])
        validation_error: InvariantFailure | None = None
        try:
            if changed is None or display_name(changed) != new_name or changed.get("parent_id") != current.get("parent_id"):
                raise InvariantFailure("Rename read-back did not preserve ID/parent and apply the requested name.")
            if snapshot_ids(before) != snapshot_ids(after):
                raise InvariantFailure("Rename changed one or more hierarchy object IDs.")
            if page_topology(before) != page_topology(after):
                raise InvariantFailure("Rename changed Page IDs, order, level, or parent relationships.")
            if before["page_hashes"] != after["page_hashes"]:
                raise InvariantFailure("Rename changed one or more Page XML hashes.")
        except InvariantFailure as exc:
            validation_error = exc
        restore_target = changed or forward.get("item")
        if not isinstance(restore_target, dict):
            raise RestoreFailure("Rename succeeded but no target identity was available for restoration.")
        try:
            await client.call_tool(
                tool,
                {
                    id_key: restore_target["id"],
                    "new_name": original_name,
                    "expected_name": new_name,
                    "expected_parent_id": current["parent_id"],
                    "expected_modified": restore_target.get("modified"),
                },
            )
            restored = await capture_snapshot(client, notebook_id)
            write_json(out / "restored.json", restored)
            assert_restored(before, restored)
        except (ClientFailure, RunnerFailure) as exc:
            if isinstance(exc, RestoreFailure):
                raise
            raise RestoreFailure(f"Rename succeeded but restoration failed: {exc}") from exc
        if validation_error is not None:
            raise validation_error
        result = {
            "scenario": "rename",
            "status": "passed",
            "target_id": current["id"],
            "original_name": original_name,
            "temporary_name": new_name,
            "forward_result": forward.get("item"),
            "restored": True,
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


def page_predecessor(pages: list[dict[str, Any]], page_id: str) -> str:
    index = next((index for index, page in enumerate(pages) if page.get("id") == page_id), -1)
    if index < 0:
        raise RunnerFailure(f"Page is missing from snapshot: {page_id}")
    return "" if index == 0 else str(pages[index - 1]["id"])


async def run_reorder(args: argparse.Namespace, options: RuntimeOptions, manifest: dict[str, Any]) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    target = resolve_manifest_item(manifest, "sibling_page")
    after_target = resolve_manifest_item(manifest, "parent_page")
    section = resolve_manifest_item(manifest, "move_source")
    out = scenario_dir(options.run_dir, "reorder")
    async with MCPStdioClient(
        policy=WRITE_POLICY,
        allowed_tools=REORDER_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        original = find_snapshot_item(before, target["id"])
        if original is None:
            raise RunnerFailure("Reorder target is not active.")
        pages = sorted(
            [item for item in before["items"] if item.get("section_id") == section["id"]],
            key=lambda item: int(item["order"]),
        )
        original_after = page_predecessor(pages, original["id"])
        original_level = int(original["page_level"])
        forward = await client.call_tool(
            "reorder_page",
            {
                "page_id": original["id"],
                "expected_title": display_name(original),
                "expected_section_id": section["id"],
                "after_page_id": after_target["id"],
                "page_level": args.page_level,
                "expected_modified": original.get("modified"),
            },
        )
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        changed = find_snapshot_item(after, original["id"])
        after_pages = sorted(
            [item for item in after["items"] if item.get("section_id") == section["id"]],
            key=lambda item: int(item["order"]),
        )
        validation_error: InvariantFailure | None = None
        try:
            if changed is None or page_predecessor(after_pages, original["id"]) != after_target["id"]:
                raise InvariantFailure("Reorder read-back position does not match the requested predecessor.")
            if int(changed["page_level"]) != args.page_level:
                raise InvariantFailure("Reorder read-back page_level does not match the requested level.")
            if snapshot_ids(before) != snapshot_ids(after):
                raise InvariantFailure("Reorder changed one or more hierarchy object IDs.")
            assert_valid_page_tree(after, section["id"])
            if before["page_hashes"] != after["page_hashes"]:
                raise InvariantFailure("Reorder changed one or more Page XML hashes.")
        except InvariantFailure as exc:
            validation_error = exc
        restore_target = changed or forward.get("item")
        if not isinstance(restore_target, dict):
            raise RestoreFailure("Reorder succeeded but no target identity was available for restoration.")
        try:
            await client.call_tool(
                "reorder_page",
                {
                    "page_id": restore_target["id"],
                    "expected_title": display_name(original),
                    "expected_section_id": section["id"],
                    "after_page_id": original_after,
                    "page_level": original_level,
                    "expected_modified": restore_target.get("modified"),
                },
            )
            restored = await capture_snapshot(client, notebook_id)
            write_json(out / "restored.json", restored)
            assert_restored(before, restored)
        except (ClientFailure, RunnerFailure) as exc:
            if isinstance(exc, RestoreFailure):
                raise
            raise RestoreFailure(f"Reorder succeeded but restoration failed: {exc}") from exc
        if validation_error is not None:
            raise validation_error
        result = {
            "scenario": "reorder",
            "status": "passed",
            "target_id": original["id"],
            "temporary_after_page_id": after_target["id"],
            "temporary_page_level": args.page_level,
            "restored": True,
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


async def run_move(args: argparse.Namespace, options: RuntimeOptions, manifest: dict[str, Any]) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    target = resolve_manifest_item(manifest, "move_source")
    source = resolve_manifest_item(manifest, "group_a")
    destination = resolve_manifest_item(manifest, "group_b")
    out = scenario_dir(options.run_dir, "move")
    async with MCPStdioClient(
        policy=MOVE_POLICY,
        allowed_tools=MOVE_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        current = find_snapshot_item(before, target["id"])
        if current is None or current.get("parent_id") != source["id"]:
            raise RunnerFailure("Move-Source is not currently under Group-A; refusing to guess recovery state.")
        forward = await client.call_tool(
            "move_section",
            {
                "section_id": current["id"],
                "destination_parent_id": destination["id"],
                "expected_name": display_name(current),
                "expected_parent_id": source["id"],
                "expected_modified": current.get("modified"),
            },
        )
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        moved = find_snapshot_item(after, current["id"])
        validation_error: InvariantFailure | None = None
        try:
            if moved is None or moved.get("parent_id") != destination["id"]:
                raise InvariantFailure("Move read-back did not preserve ID and apply the destination parent.")
            if snapshot_ids(before) != snapshot_ids(after):
                raise InvariantFailure("Move changed one or more hierarchy object IDs.")
            if page_topology(before, current["id"]) != page_topology(after, current["id"]):
                raise InvariantFailure("Move changed Page IDs, order, level, or parent relationships.")
            if before["page_hashes"] != after["page_hashes"]:
                raise InvariantFailure("Move changed one or more Page XML hashes.")
        except InvariantFailure as exc:
            validation_error = exc
        restore_target = moved or forward.get("item")
        if not isinstance(restore_target, dict):
            raise RestoreFailure("Move succeeded but no target identity was available for restoration.")
        try:
            await client.call_tool(
                "move_section",
                {
                    "section_id": restore_target["id"],
                    "destination_parent_id": source["id"],
                    "expected_name": display_name(current),
                    "expected_parent_id": destination["id"],
                    "expected_modified": restore_target.get("modified"),
                },
            )
            restored = await capture_snapshot(client, notebook_id)
            write_json(out / "restored.json", restored)
            assert_restored(before, restored)
        except (ClientFailure, RunnerFailure) as exc:
            if isinstance(exc, RestoreFailure):
                raise
            raise RestoreFailure(f"Move succeeded but restoration failed: {exc}") from exc
        if validation_error is not None:
            raise validation_error
        result = {
            "scenario": "move",
            "status": "passed",
            "target_id": current["id"],
            "destination_parent_id": destination["id"],
            "restored": True,
            "warning": "This validates one installed OneNote/Office combination, not universal COM behavior.",
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


async def run_delete(args: argparse.Namespace, options: RuntimeOptions, manifest: dict[str, Any]) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    delete_sandbox = resolve_manifest_item(manifest, "delete_sandbox")
    allowed_keys = {"disposable_group", "disposable_section", "disposable_page"}
    allowed = {resolve_manifest_item(manifest, key)["id"]: key for key in allowed_keys}
    if args.delete_target_id not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise RunnerFailure(f"Delete target is not manifest-allowlisted. Allowed IDs: {allowed_text}")
    out = scenario_dir(options.run_dir, "delete")
    async with MCPStdioClient(
        policy=DELETE_POLICY,
        allowed_tools=DELETE_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        current = find_snapshot_item(before, args.delete_target_id)
        if current is None:
            raise RunnerFailure("Delete target is not active in the current notebook snapshot.")
        if not is_descendant_of(before, current["id"], delete_sandbox["id"]):
            raise RunnerFailure("Delete target is no longer a descendant of the manifest Delete-Sandbox.")
        resource_type = current.get("resource_type")
        if resource_type == "page":
            tool = "delete_page"
            arguments = {
                "page_id": current["id"],
                "expected_title": display_name(current),
                "expected_section_id": current["section_id"],
                "expected_modified": current.get("modified"),
                "permanently": False,
            }
        elif resource_type in {"section", "section_group"}:
            tool = "delete_section" if resource_type == "section" else "delete_section_group"
            id_key = "section_id" if resource_type == "section" else "section_group_id"
            arguments = {
                id_key: current["id"],
                "expected_name": display_name(current),
                "expected_parent_id": current["parent_id"],
                "expected_modified": current.get("modified"),
                "permanently": False,
            }
        else:
            raise RunnerFailure("Delete smoke supports only allowlisted Page/Section/SectionGroup targets.")
        deleted = await client.call_tool(tool, arguments)
        if deleted.get("permanently") is not False:
            raise InvariantFailure("Delete response did not explicitly confirm permanently=false.")
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        if find_snapshot_item(after, current["id"]) is not None:
            raise InvariantFailure("Deleted target is still visible in the default active snapshot.")
        recycle_tree_result = await client.call_tool(
            "get_tree",
            {"root_id": notebook_id, "max_depth": 8, "include_recycle_bin": True},
        )
        recycle_items = [stable_item(item) for item in flatten_tree(recycle_tree_result["tree"])]
        recycle_snapshot = {
            "captured_at": utc_now(),
            "notebook_id": notebook_id,
            "include_recycle_bin": True,
            "items": recycle_items,
        }
        write_json(out / "recycle-bin.json", recycle_snapshot)
        recycled = next((item for item in recycle_items if item.get("id") == current["id"]), None)
        if recycled is not None and recycled.get("is_in_recycle_bin") is not True:
            raise InvariantFailure("Delete target remains visible without an is_in_recycle_bin marker.")
        restoration = {
            "status": "not_attempted",
            "reason": "The typed MCP profile has no recycle-bin restore tool. Re-run create to replenish disposable fixtures.",
            "target_id": current["id"],
        }
        write_json(out / "restored.json", restoration)
        result = {
            "scenario": "delete",
            "status": "passed",
            "target_id": current["id"],
            "target_key": allowed[current["id"]],
            "permanently": False,
            "restored": False,
            "replenish_command": f".venv\\Scripts\\python.exe tests\\manual_isolated\\run.py create --notebook-name {display_name(manifest['notebook'])!r} --run-dir {str(options.run_dir)!r}",
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


def _copy_spec(scenario: str, manifest: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    suffix = timestamp()
    if scenario == "copy-page":
        return {
            "source": resolve_manifest_item(manifest, "parent_page"),
            "destination": resolve_manifest_item(manifest, "disposable_section"),
            "destination_name": f"Copy-Parent-{suffix}",
            "tool": "copy_page",
            "policy": COPY_POLICY,
            "tools": COPY_TOOLS,
        }
    if scenario == "copy-section":
        return {
            "source": resolve_manifest_item(manifest, "move_source"),
            "destination": resolve_manifest_item(manifest, "group_b"),
            "destination_name": f"Copy-Section-{suffix}",
            "tool": "copy_section",
            "policy": COPY_POLICY,
            "tools": COPY_TOOLS,
        }
    if scenario == "copy-section-group":
        return {
            "source": resolve_manifest_item(manifest, "group_a"),
            "destination": manifest["notebook"],
            "destination_name": f"Copy-Group-{suffix}",
            "tool": "copy_section_group",
            "policy": COPY_POLICY,
            "tools": COPY_TOOLS,
        }
    if scenario == "copy-notebook":
        disposable_targets = manifest.get("disposable_targets")
        allowlisted = (
            disposable_targets.get("notebook_copy_root")
            if isinstance(disposable_targets, dict)
            else None
        )
        expected_root = (run_dir / "notebook-copies").resolve()
        if not allowlisted or Path(allowlisted).resolve() != expected_root:
            raise RunnerFailure(
                "Manifest is missing the exact disposable Notebook Copy root; run create again."
            )
        return {
            "source": manifest["notebook"],
            "destination": None,
            "destination_name": f"{display_name(manifest['notebook'])}-Copy-{suffix}",
            "destination_base_folder": str(expected_root),
            "tool": "copy_notebook",
            "policy": COPY_NO_DELETE_POLICY,
            "tools": COPY_NOTEBOOK_TOOLS,
        }
    raise RunnerFailure(f"Unknown Copy scenario: {scenario}")


def _copy_execute_arguments(
    spec: dict[str, Any],
    source: dict[str, Any],
    plan_digest: str,
) -> dict[str, Any]:
    common = {
        "plan_digest": plan_digest,
        "expected_modified": source.get("modified"),
    }
    tool = spec["tool"]
    if tool == "copy_page":
        return {
            **common,
            "page_id": source["id"],
            "destination_section_id": spec["destination"]["id"],
            "expected_title": display_name(source),
            "expected_section_id": source["section_id"],
            "destination_title": spec["destination_name"],
        }
    if tool == "copy_section":
        id_key = "section_id"
    elif tool == "copy_section_group":
        id_key = "section_group_id"
    else:
        return {
            **common,
            "notebook_id": source["id"],
            "expected_name": display_name(source),
            "destination_name": spec["destination_name"],
            "destination_base_folder": spec["destination_base_folder"],
        }
    return {
        **common,
        id_key: source["id"],
        "destination_parent_id": spec["destination"]["id"],
        "expected_name": display_name(source),
        "expected_parent_id": source["parent_id"],
        "destination_name": spec["destination_name"],
    }


async def _cleanup_copy(
    client: MCPStdioClient,
    snapshot: dict[str, Any],
    copied: dict[str, Any],
) -> list[str]:
    id_map = copied.get("copy_report", {}).get("id_map", {})
    target_ids = set(id_map.values())
    targets = [item for item in snapshot.get("items", []) if item.get("id") in target_ids]
    if not targets:
        raise RestoreFailure("Copy returned IDs that were not found in the post-copy snapshot.")
    root = next((item for item in targets if item.get("id") == copied.get("item", {}).get("id")), None)
    if root is None:
        raise RestoreFailure("Copy target root was not found in the post-copy snapshot.")
    notebook_id = root.get("notebook_id")
    if not notebook_id:
        raise RestoreFailure("Copy target does not expose the containing Notebook ID needed for cleanup.")

    by_id = {item["id"]: item for item in targets}

    def container_depth(item: dict[str, Any]) -> int:
        depth = 0
        parent_id = item.get("parent_id")
        while parent_id in by_id:
            depth += 1
            parent_id = by_id[parent_id].get("parent_id")
        return depth

    cleanup_order = [
        *sorted(
            (item for item in targets if item["resource_type"] == "page"),
            key=lambda item: (str(item.get("section_id")), int(item.get("order", 0))),
            reverse=True,
        ),
        *sorted(
            (item for item in targets if item["resource_type"] == "section"),
            key=lambda item: (container_depth(item), str(item["id"])),
            reverse=True,
        ),
        *sorted(
            (item for item in targets if item["resource_type"] == "section_group"),
            key=lambda item: (container_depth(item), str(item["id"])),
            reverse=True,
        ),
    ]
    deleted: list[str] = []
    for target in cleanup_order:
        tree_result = await client.call_tool(
            "get_tree",
            {"root_id": notebook_id, "max_depth": 8},
        )
        current = next(
            (item for item in flatten_tree(tree_result["tree"]) if item.get("id") == target["id"]),
            None,
        )
        if current is None:
            raise RestoreFailure(
                f"Copy cleanup target '{target['id']}' disappeared before its explicit leaf-to-root step."
            )
        if current["resource_type"] == "page":
            tool = "delete_page"
            arguments = {
                "page_id": current["id"],
                "expected_title": display_name(current),
                "expected_section_id": current["section_id"],
                "expected_modified": current.get("modified"),
                "permanently": False,
            }
        else:
            tool = "delete_section" if current["resource_type"] == "section" else "delete_section_group"
            id_key = "section_id" if current["resource_type"] == "section" else "section_group_id"
            arguments = {
                id_key: current["id"],
                "expected_name": display_name(current),
                "expected_parent_id": current["parent_id"],
                "expected_modified": current.get("modified"),
                "permanently": False,
            }
        result = await client.call_tool(tool, arguments)
        if result.get("permanently") is not False:
            raise RestoreFailure("Copy cleanup did not explicitly confirm permanently=false.")
        deleted.append(current["id"])
    return deleted


async def _call_with_result_evidence(
    client: MCPStdioClient,
    tool: str,
    arguments: dict[str, Any],
    evidence_path: Path,
) -> dict[str, Any]:
    """Persist both successful and structured partial mutation responses."""

    try:
        result = await client.call_tool(tool, arguments)
    except ClientFailure as exc:
        if exc.envelope is not None:
            write_json(evidence_path, exc.envelope)
        raise
    write_json(evidence_path, result)
    return result


async def run_copy(args: argparse.Namespace, options: RuntimeOptions, manifest: dict[str, Any]) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    spec = _copy_spec(args.scenario, manifest, options.run_dir)
    if args.scenario == "copy-notebook":
        Path(spec["destination_base_folder"]).mkdir(parents=True, exist_ok=True)
    out = scenario_dir(options.run_dir, args.scenario)
    async with MCPStdioClient(
        policy=spec["policy"],
        allowed_tools=spec["tools"],
        run_dir=out,
        timeout_seconds=options.timeout,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        current = find_snapshot_item(before, spec["source"]["id"])
        if current is None:
            raise RunnerFailure("Manifest Copy source is not active in the current snapshot.")
        plan_arguments = {
            "source_id": current["id"],
            "destination_name": spec["destination_name"],
        }
        if spec["destination"] is not None:
            plan_arguments["destination_parent_id"] = spec["destination"]["id"]
        if spec.get("destination_base_folder"):
            plan_arguments["destination_base_folder"] = spec["destination_base_folder"]
        planned = await client.call_tool("plan_copy", plan_arguments)
        write_json(out / "plan.json", planned)
        assert_copy_fixture_capabilities(planned)
        copied = await _call_with_result_evidence(
            client,
            spec["tool"],
            _copy_execute_arguments(spec, current, planned["plan_digest"]),
            out / "copy-result.json",
        )
        if copied.get("copy_report", {}).get("verified") is not True:
            raise InvariantFailure("Copy response did not report successful read-back verification.")

        if args.scenario == "copy-notebook":
            target = copied.get("item")
            if not isinstance(target, dict) or target.get("resource_type") != "notebook":
                raise InvariantFailure("Notebook Copy did not return a typed target Notebook.")
            actual_target_path = str(copied.get("destination_path", ""))
            planned_target_path = str(planned["destination"]["target_path"])
            if not actual_target_path or actual_target_path.casefold() != planned_target_path.casefold():
                raise InvariantFailure("Notebook Copy result path differs from the manifest-scoped plan.")
            target_snapshot = await capture_snapshot(client, target["id"])
            write_json(out / "after.json", target_snapshot)
            assert_copy_mapping(
                before,
                target_snapshot,
                current["id"],
                None,
                spec["destination_name"],
                copied,
            )
            closed = await client.call_tool(
                "close_notebook",
                {
                    "notebook_id": target["id"],
                    "expected_name": display_name(target),
                    "expected_modified": target.get("modified"),
                },
            )
            remaining = {
                "status": "closed_not_deleted",
                "target_id": target["id"],
                "target_path": actual_target_path,
                "close_result": closed,
                "reason": "OneNote COM exposes CloseNotebook, not typed Notebook deletion.",
            }
            write_json(out / "restored.json", remaining)
            result = {
                "scenario": args.scenario,
                "status": "passed",
                "target_id": target["id"],
                "restored": False,
                "remaining_state": remaining,
                "copy_report": copied["copy_report"],
            }
            write_json(out / "result.json", result)
            render_report(options.run_dir)
            return result

        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        assert_copy_mapping(
            before,
            after,
            current["id"],
            spec["destination"]["id"],
            spec["destination_name"],
            copied,
        )
        for source_id, source_hash in before["page_hashes"].items():
            if after["page_hashes"].get(source_id) != source_hash:
                raise InvariantFailure("Copy changed an existing source Page XML hash.")
        deleted_ids = await _cleanup_copy(client, after, copied)
        restored = await capture_snapshot(client, notebook_id)
        write_json(out / "restored.json", restored)
        assert_restored(before, restored)
        result = {
            "scenario": args.scenario,
            "status": "passed",
            "target_id": copied.get("item", {}).get("id"),
            "restored": True,
            "cleanup_deleted_ids": deleted_ids,
            "copy_report": copied["copy_report"],
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


async def run_reconstructive_move_page(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    source = resolve_manifest_item(manifest, "disposable_page")
    destination = resolve_manifest_item(manifest, "move_source")
    destination_title = f"Moved-Disposable-{timestamp()}"
    out = scenario_dir(options.run_dir, "reconstructive-move-page")
    async with MCPStdioClient(
        policy=RECONSTRUCTIVE_MOVE_PAGE_POLICY,
        allowed_tools=RECONSTRUCTIVE_MOVE_PAGE_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        current = find_snapshot_item(before, source["id"])
        if current is None:
            raise RunnerFailure("Disposable Page is not active; run create to replenish the fixture.")
        planned = await client.call_tool(
            "plan_reconstructive_move_page",
            {
                "page_id": current["id"],
                "destination_section_id": destination["id"],
                "destination_title": destination_title,
            },
        )
        write_json(out / "plan.json", planned)
        moved = await _call_with_result_evidence(
            client,
            "reconstructive_move_page",
            {
                "page_id": current["id"],
                "destination_section_id": destination["id"],
                "expected_title": display_name(current),
                "expected_section_id": current["section_id"],
                "expected_modified": current.get("modified"),
                "destination_title": destination_title,
                "plan_digest": planned["plan_digest"],
            },
            out / "copy-result.json",
        )
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        source_subtree_ids = {
            item["id"] for item in expected_copy_source_items(before, current["id"])
        }
        remaining_source_ids = source_subtree_ids & snapshot_ids(after)
        if remaining_source_ids:
            raise InvariantFailure(
                f"Reconstructive Move source subtree remains active: {sorted(remaining_source_ids)}"
            )
        target_id = moved.get("item", {}).get("id")
        if not target_id or find_snapshot_item(after, target_id) is None:
            raise InvariantFailure("Reconstructive Move target is missing from the active snapshot.")
        assert_copy_mapping(
            before,
            after,
            current["id"],
            destination["id"],
            destination_title,
            moved,
        )
        recycle_tree = await client.call_tool(
            "get_tree",
            {"root_id": notebook_id, "max_depth": 8, "include_recycle_bin": True},
        )
        recycle_items = [stable_item(item) for item in flatten_tree(recycle_tree["tree"])]
        recycled_source_ids = {
            item["id"]
            for item in recycle_items
            if item.get("id") in source_subtree_ids
            and item.get("is_in_recycle_bin") is True
        }
        if recycled_source_ids != source_subtree_ids:
            raise InvariantFailure(
                "Reconstructive Move source subtree could not be proven to be in the OneNote recycle bin."
            )
        write_json(
            out / "recycle-bin.json",
            {
                "captured_at": utc_now(),
                "notebook_id": notebook_id,
                "include_recycle_bin": True,
                "items": recycle_items,
            },
        )
        remaining = {
            "status": "source_subtree_in_recycle_bin",
            "source_id": current["id"],
            "source_ids": sorted(source_subtree_ids),
            "target_id": target_id,
            "reason": "Typed recycle-bin restore is unavailable; run create to replenish the fixture.",
        }
        write_json(out / "restored.json", remaining)
        result = {
            "scenario": "reconstructive-move-page",
            "status": "passed",
            "target_id": current["id"],
            "new_target_id": target_id,
            "restored": False,
            "remaining_state": remaining,
            "copy_report": moved["copy_report"],
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


async def command_validate(args: argparse.Namespace, options: RuntimeOptions) -> dict[str, Any]:
    manifest = load_manifest(options.run_dir)
    if args.scenario == "reorder" and args.page_level < 1:
        raise RunnerFailure("--page-level must be at least 1.")
    policy_tools = {
        "rename": (WRITE_POLICY, RENAME_TOOLS),
        "reorder": (WRITE_POLICY, REORDER_TOOLS),
        "move": (MOVE_POLICY, MOVE_TOOLS),
        "delete": (DELETE_POLICY, DELETE_TOOLS),
        "copy-page": (COPY_POLICY, COPY_TOOLS),
        "copy-section": (COPY_POLICY, COPY_TOOLS),
        "copy-section-group": (COPY_POLICY, COPY_TOOLS),
        "copy-notebook": (COPY_NO_DELETE_POLICY, COPY_NOTEBOOK_TOOLS),
        "reconstructive-move-page": (
            RECONSTRUCTIVE_MOVE_PAGE_POLICY,
            RECONSTRUCTIVE_MOVE_PAGE_TOOLS,
        ),
    }
    policy, tools = policy_tools[args.scenario]
    if options.dry_run:
        notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
        target_keys = {
            "rename": getattr(args, "target", "move_source"),
            "reorder": "sibling_page",
            "move": "move_source",
            "copy-page": "parent_page",
            "copy-section": "move_source",
            "copy-section-group": "group_a",
            "copy-notebook": None,
            "reconstructive-move-page": "disposable_page",
        }
        if args.scenario == "delete":
            allowed_ids = {
                resolve_manifest_item(manifest, key)["id"]
                for key in ("disposable_group", "disposable_section", "disposable_page")
            }
            if args.delete_target_id not in allowed_ids:
                raise RunnerFailure("Delete target is not one of the manifest-allowlisted disposable IDs.")
            target_id = args.delete_target_id
        else:
            target_key = target_keys[args.scenario]
            target_id = (
                manifest["notebook"]["id"]
                if target_key is None
                else resolve_manifest_item(manifest, target_key)["id"]
            )
        result = dry_run_result(args.scenario, policy, tools, notebook_id, options)
        result["target_id"] = target_id
        return result
    if args.scenario == "rename":
        return await run_rename(args, options, manifest)
    if args.scenario == "reorder":
        return await run_reorder(args, options, manifest)
    if args.scenario == "move":
        return await run_move(args, options, manifest)
    if args.scenario == "delete":
        return await run_delete(args, options, manifest)
    if args.scenario.startswith("copy-"):
        return await run_copy(args, options, manifest)
    return await run_reconstructive_move_page(args, options, manifest)


def render_report(run_dir: Path) -> Path:
    manifest = read_json(manifest_path(run_dir))
    notebook = manifest.get("notebook", {})
    lines = [
        "# OneNote isolated manual smoke report",
        "",
        f"- Run ID: `{manifest.get('run_id', run_dir.name)}`",
        f"- Notebook: `{display_name(notebook)}`",
        f"- Notebook ID: `{notebook.get('id', '')}`",
        f"- Generated: `{utc_now()}`",
        f"- local-onenote-mcp: `{manifest.get('local_onenote_mcp_version', 'unknown')}`",
        "- Copy/Move fidelity is accepted only from explicit named scenario results below.",
        "- Retry policy: mutation calls are never retried; read-only transport failures are attempted at most twice.",
        "",
    ]
    copy_fixture = manifest.get("copy_fixture")
    if isinstance(copy_fixture, dict):
        lines.extend(
            [
                "## Copy fixture",
                "",
                f"- Page ID: `{copy_fixture.get('page_id', '')}`",
                f"- Automated content: `{', '.join(copy_fixture.get('automated_content', []))}`",
                f"- Manual content: `{', '.join(copy_fixture.get('manual_content', []))}`",
                f"- Observed object types: `{', '.join(copy_fixture.get('observed_object_types', []))}`",
                "",
            ]
        )
    lines.extend(["## Scenarios", ""])
    found = False
    scenarios_root = run_dir / "scenarios"
    if scenarios_root.exists():
        for scenario_path in sorted(path for path in scenarios_root.iterdir() if path.is_dir()):
            result_path = scenario_path / "result.json"
            failure_path = scenario_path / "failure.json"
            if result_path.exists():
                result = read_json(result_path)
            elif failure_path.exists():
                result = read_json(failure_path)
            else:
                continue
            found = True
            lines.extend(
                [
                    f"### {result.get('scenario', scenario_path.name)}",
                    "",
                    f"- Status: `{result.get('status', 'unknown')}`",
                    f"- Target ID: `{result.get('target_id', '')}`",
                    f"- Restored: `{result.get('restored', 'n/a')}`",
                    "",
                ]
            )
            plan_path = scenario_path / "plan.json"
            if plan_path.exists():
                planned = read_json(plan_path)
                lines.extend(
                    [
                        f"- Planned content capabilities: `{', '.join(planned.get('content_capabilities', []))}`",
                        f"- Planned lossless candidate: `{planned.get('copyability', {}).get('lossless_candidate', 'n/a')}`",
                    ]
                )
            copy_result_path = scenario_path / "copy-result.json"
            if copy_result_path.exists():
                copy_result = read_json(copy_result_path)
                copy_report = copy_result.get("copy_report", {})
                lines.extend(
                    [
                        f"- Copy verified: `{copy_report.get('verified', 'n/a')}`",
                        f"- Copy lossless: `{copy_report.get('lossless', 'n/a')}`",
                        f"- Copy outcome: `{copy_result.get('outcome', 'copy')}`",
                    ]
                )
            if plan_path.exists() or copy_result_path.exists():
                lines.append("")
            if result.get("error"):
                lines.extend([f"Error: {result['error']}", ""])
    if not found:
        lines.extend(["No mutation scenario has completed yet.", ""])
    lines.extend(
        [
            "## Safety boundary",
            "",
            "Each command started its own MCP process with a static minimal policy. Permanent delete and raw XML remained disabled. Delete fixtures are not automatically restored because the typed tool profile has no recycle-bin restore operation.",
            "",
        ]
    )
    validation_environment = manifest.get("validation_environment")
    if isinstance(validation_environment, dict):
        lines.extend(
            [
                "## Validated environment",
                "",
                f"- OneNote version: `{validation_environment.get('onenote_version', 'not recorded')}`",
                f"- Office channel: `{validation_environment.get('office_channel', 'not recorded')}`",
                f"- Recorded: `{validation_environment.get('recorded_at', '')}`",
                "",
            ]
        )
    path = run_dir / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def record_validate_failure(args: argparse.Namespace, message: str, exit_code: int) -> None:
    """Persist a failure handoff without masking the original exception."""

    try:
        if getattr(args, "command", None) != "validate" or not getattr(args, "run_dir", None):
            return
        run_dir = Path(args.run_dir)
        out = scenario_dir(run_dir, args.scenario)
        completed_artifacts = [
            name
            for name in ("before.json", "plan.json", "copy-result.json", "after.json", "restored.json")
            if (out / name).exists()
        ]
        mutation_result = (
            read_json(out / "copy-result.json")
            if "copy-result.json" in completed_artifacts
            else {}
        )
        created_ids = mutation_result.get("created_ids", [])
        needs_manual_cleanup = bool(created_ids) or mutation_result.get("outcome") in {
            "copy_only",
            "copy_unverified",
            "source_partially_recycled",
            "source_recycle_unverified",
            "source_delete_failed",
        }
        manifest = load_manifest(run_dir)
        target_keys = {
            "rename": getattr(args, "target", "move_source"),
            "reorder": "sibling_page",
            "move": "move_source",
            "copy-page": "parent_page",
            "copy-section": "move_source",
            "copy-section-group": "group_a",
            "copy-notebook": None,
            "reconstructive-move-page": "disposable_page",
        }
        if args.scenario == "delete":
            target_id = getattr(args, "delete_target_id", "")
        else:
            target_key = target_keys[args.scenario]
            target_id = (
                manifest.get("notebook", {}).get("id", "")
                if target_key is None
                else manifest.get("structure", {}).get(target_key, {}).get("id", "")
            )
        notebook_id = manifest.get("notebook", {}).get("id", "")
        last_step = "preflight"
        if "before.json" in completed_artifacts:
            last_step = "capture_before"
        if "copy-result.json" in completed_artifacts:
            last_step = "execute_mutation"
        if "after.json" in completed_artifacts:
            last_step = "capture_after"
        if "restored.json" in completed_artifacts:
            last_step = "capture_restored"
        failure = {
            "scenario": args.scenario,
            "status": (
                "needs_manual_cleanup"
                if needs_manual_cleanup
                else "needs_manual_restore" if exit_code == EXIT_RESTORE else "failed"
            ),
            "exit_code": exit_code,
            "error": message,
            "target_id": target_id,
            "last_successful_step": last_step,
            "completed_artifacts": completed_artifacts,
            "outcome": mutation_result.get("outcome"),
            "created_ids": created_ids,
            "id_map": (
                mutation_result.get("copy_report", {}).get("id_map")
                or mutation_result.get("id_map", {})
            ),
            "restored": True if "restored.json" in completed_artifacts and exit_code != EXIT_RESTORE else "unknown",
            "failed_at": utc_now(),
            "suggested_next_step": (
                ".venv\\Scripts\\python.exe tests\\manual_isolated\\run.py read "
                f"--notebook-id {notebook_id!r} --output .local-validation\\recovery-{timestamp()}"
            ),
        }
        write_json(out / "failure.json", failure)
        render_report(Path(args.run_dir))
    except Exception:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="User-triggered isolated OneNote MCP manual smoke tests.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def runtime_flags(
        command: argparse.ArgumentParser,
        *,
        run_dir_required: bool = False,
        timeout_default: int = 180,
    ) -> None:
        command.add_argument(
            "--run-dir",
            "--output",
            type=Path,
            required=run_dir_required,
            help="Artifact directory; required for validation scenarios.",
        )
        command.add_argument(
            "--timeout",
            type=int,
            default=timeout_default,
            help=f"Per MCP operation timeout in seconds (default: {timeout_default}).",
        )
        command.add_argument("--dry-run", action="store_true", help="Print the static plan without starting MCP.")
        command.add_argument("--json", action="store_true", dest="json_output", help="Print stable JSON only.")

    inspect_parser = subparsers.add_parser("inspect", help="Read-only exact-name discovery and tree inspection.")
    inspect_parser.add_argument("--notebook-name", required=True)
    runtime_flags(inspect_parser)

    create_parser = subparsers.add_parser("create", help="Idempotently create/reuse the isolated fixture tree.")
    create_parser.add_argument("--notebook-name", default=DEFAULT_NOTEBOOK_NAME)
    create_parser.add_argument("--base-folder", default="")
    runtime_flags(create_parser)

    read_parser = subparsers.add_parser(
        "read",
        aliases=["baseline"],
        help="Capture a read-only hierarchy and Page hash baseline.",
    )
    target = read_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--notebook-name")
    target.add_argument("--notebook-id")
    read_parser.add_argument(
        "--export-onepkg",
        action="store_true",
        help="Also export the target Notebook to <output>/baseline.onepkg without overwriting.",
    )
    runtime_flags(read_parser)

    validate_parser = subparsers.add_parser("validate", help="Run exactly one mutation and its checks.")
    validate_subparsers = validate_parser.add_subparsers(dest="scenario", required=True)
    rename = validate_subparsers.add_parser("rename", help="Rename and restore one prepared container.")
    rename.add_argument("--target", choices=["group_a", "group_b", "move_source"], default="move_source")
    rename.add_argument("--new-name")
    rename.add_argument("--notebook-name")
    runtime_flags(rename, run_dir_required=True)
    reorder = validate_subparsers.add_parser("reorder", help="Reorder/indent Sibling and restore it.")
    reorder.add_argument("--page-level", type=int, default=2)
    reorder.add_argument("--notebook-name")
    runtime_flags(reorder, run_dir_required=True)
    move = validate_subparsers.add_parser("move", help="Move Move-Source to Group-B and restore it.")
    move.add_argument("--notebook-name")
    runtime_flags(move, run_dir_required=True)
    delete = validate_subparsers.add_parser("delete", help="Non-permanently delete one manifest-allowlisted fixture.")
    delete.add_argument("--delete-target-id", required=True)
    delete.add_argument("--notebook-name")
    runtime_flags(delete, run_dir_required=True)
    for scenario, help_text in (
        ("copy-page", "Copy the prepared Parent Page subtree and clean up the target."),
        ("copy-section", "Copy Move-Source into Group-B and clean up the target."),
        ("copy-section-group", "Copy Group-A into the prepared Notebook and clean up the target."),
        ("copy-notebook", "Copy and close the Notebook in a manifest-scoped disposable folder."),
        (
            "reconstructive-move-page",
            "Move the disposable Page by verified Copy plus non-permanent source deletion.",
        ),
    ):
        command = validate_subparsers.add_parser(scenario, help=help_text)
        command.add_argument("--notebook-name")
        runtime_flags(command, run_dir_required=True, timeout_default=1_800)

    report = subparsers.add_parser("report", help="Regenerate report.md from local artifacts only.")
    report.add_argument("--run-dir", type=Path, required=True)
    report.add_argument("--onenote-version", help="Record the OneNote version used for the manual run.")
    report.add_argument("--office-channel", help="Record the Office update channel used for the manual run.")
    report.add_argument("--json", action="store_true", dest="json_output")
    return parser


def print_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return
    for key, value in result.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        else:
            rendered = str(value)
        print(f"{key}: {rendered}")


async def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "report":
        if args.onenote_version or args.office_channel:
            manifest = load_manifest(args.run_dir)
            previous = manifest.get("validation_environment", {})
            manifest["validation_environment"] = {
                "onenote_version": args.onenote_version or previous.get("onenote_version", "not recorded"),
                "office_channel": args.office_channel or previous.get("office_channel", "not recorded"),
                "recorded_at": utc_now(),
            }
            write_json(manifest_path(args.run_dir), manifest)
        report_path = render_report(args.run_dir)
        return {"command": "report", "report": str(report_path.resolve())}
    run_dir = args.run_dir or default_run_dir()
    if args.timeout < 1:
        raise RunnerFailure("--timeout must be at least 1 second.")
    options = RuntimeOptions(
        run_dir=run_dir,
        timeout=args.timeout,
        json_output=args.json_output,
        dry_run=args.dry_run,
    )
    if args.command == "inspect":
        return await command_inspect(args, options)
    if args.command == "create":
        return await command_create(args, options)
    if args.command in {"read", "baseline"}:
        return await command_read(args, options)
    return await command_validate(args, options)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(dispatch(args))
    except RunnerFailure as exc:
        record_validate_failure(args, str(exc), exc.exit_code)
        error = {"ok": False, "error": str(exc), "exit_code": exc.exit_code}
        print_result(error, json_output=bool(getattr(args, "json_output", False)))
        return exc.exit_code
    except ClientFailure as exc:
        record_validate_failure(args, str(exc), EXIT_MCP)
        error = {"ok": False, "error": str(exc), "exit_code": EXIT_MCP}
        print_result(error, json_output=bool(getattr(args, "json_output", False)))
        return EXIT_MCP
    result = {"ok": True, **result}
    print_result(result, json_output=bool(getattr(args, "json_output", False)))
    return 0
