"""Snapshot, evidence, and manifest helpers used by validation scenarios."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version as package_version
import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from local_onenote_mcp.page import canonical_page_digest
from local_onenote_mcp.services.pages import stable_page_content_digest

from .mcp_stdio_client import COPY_BUDGET_ENV, MCPStdioClient, ScenarioPolicy
from .runtime import InvariantFailure, RestoreFailure, RunnerFailure, RuntimeOptions


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


async def capture_snapshot(client: MCPStdioClient, notebook_id: str) -> dict[str, Any]:
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
    for page in pages:
        page_id = str(page["id"])
        xml_result = await client.call_tool("get_page_xml", {"page_id": page_id, "page_info": "all"})
        xml = str(xml_result["xml"])
        page_hashes[page_id] = page_content_hash(xml)
        page_canonical_hashes[page_id] = canonical_page_digest(xml)
        page_reparent_hashes[page_id] = page_reparent_content_hash(xml)
        page_xml_hashes[page_id] = hashlib.sha256(xml.encode("utf-8")).hexdigest()
        objects_result = await client.call_tool("get_page_objects", {"page_id": page_id})
        page_objects[page_id] = [
            {field: obj.get(field) for field in OBJECT_FIELDS if field in obj}
            for obj in objects_result.get("objects", [])
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
