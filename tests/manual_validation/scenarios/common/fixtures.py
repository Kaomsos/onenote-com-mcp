"""Scenario-scoped fixture creation using an already-started MCP client."""

from __future__ import annotations

import argparse
from typing import Any
import uuid

from ...mcp_stdio_client import MCPStdioClient
from ...runtime import InvariantFailure, RuntimeOptions
from ...test_utils import (
    capture_snapshot,
    display_name,
    manifest_path,
    stable_item,
    write_json,
)
from .fixture_builders import (
    enforce_page_position,
    ensure_copy_rich_fixture,
    ensure_group,
    ensure_page,
    ensure_section,
    new_manifest,
)
from .specs import ScenarioSpec


async def _rich_page(
    client: MCPStdioClient,
    section: dict[str, Any],
    options: RuntimeOptions,
    token: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    page = await ensure_page(client, section["id"], "Rich-Page", f"Copy token: {token}")
    return await ensure_copy_rich_fixture(client, page, options.run_dir)


def _validate_fixture_snapshot(
    scenario: str,
    snapshot: dict[str, Any],
    structure: dict[str, dict[str, Any]],
    copy_fixture: dict[str, Any] | None,
) -> list[str]:
    """Prove the selected profile's identity, topology, and content invariants."""

    by_id = {
        str(item["id"]): item
        for item in snapshot.get("items", [])
        if isinstance(item, dict) and item.get("id")
    }
    resolved: dict[str, dict[str, Any]] = {}
    for key, declared in structure.items():
        item = by_id.get(str(declared.get("id", "")))
        if item is None or item.get("is_in_recycle_bin") is True:
            raise InvariantFailure(f"Fixture structure.{key} is missing from the active snapshot.")
        resolved[key] = item
    checks = ["all declared manifest keys resolve to active fresh IDs"]

    def require(condition: bool, message: str, check: str) -> None:
        if not condition:
            raise InvariantFailure(message)
        checks.append(check)

    if scenario in {"create", "reorder"}:
        parent = resolved["parent_page"]
        child = resolved["child_page"]
        sibling = resolved["sibling_page"]
        section = resolved["move_source"]
        require(
            parent.get("section_id") == section["id"]
            and child.get("section_id") == section["id"]
            and sibling.get("section_id") == section["id"],
            "Fixture Page tree is not contained by the declared source Section.",
            "Parent/Child/Sibling share the declared source Section",
        )
        require(
            int(parent.get("page_level", 0)) == 1
            and int(child.get("page_level", 0)) == 2
            and int(sibling.get("page_level", 0)) == 1
            and child.get("parent_page_id") == parent["id"]
            and sibling.get("parent_page_id") in {None, ""},
            "Fixture Parent/Child/Sibling Page topology is invalid.",
            "Page levels and derived parent relationships match the profile",
        )
    if scenario == "create":
        require(
            resolved["move_source"].get("parent_id") == resolved["group_a"]["id"],
            "Create fixture Move-Source is outside Group-A.",
            "Move-Source is a child of Group-A",
        )
        require(
            resolved["disposable_group"].get("parent_id")
            == resolved["delete_sandbox"]["id"]
            and resolved["disposable_section"].get("parent_id")
            == resolved["delete_sandbox"]["id"]
            and resolved["disposable_page"].get("section_id")
            == resolved["disposable_section"]["id"],
            "Create fixture disposable targets escaped Delete-Sandbox.",
            "disposable targets are descendants of Delete-Sandbox",
        )
    elif scenario == "rename":
        require(
            len(resolved) == 1,
            "Rename fixture must contain exactly one selected target.",
            "exactly one CLI-selected rename target key was created",
        )
        selected = next(iter(resolved.values()))
        require(
            display_name(selected) != "",
            "Rename fixture selected target has no stable display name.",
            "selected rename target has a stable display name",
        )
    elif scenario == "move":
        require(
            resolved["move_source"].get("parent_id") == resolved["group_a"]["id"]
            and resolved["group_a"]["id"] != resolved["group_b"]["id"],
            "Move fixture source/destination relationship is invalid.",
            "source Section is under Group-A and Group-B is distinct",
        )
    elif scenario == "delete":
        require(
            resolved["disposable_group"].get("parent_id")
            == resolved["delete_sandbox"]["id"],
            "Delete target is not a direct descendant of Delete-Sandbox.",
            "disposable_group is manifest-allowlisted under Delete-Sandbox",
        )
    elif scenario == "copy-page":
        require(
            resolved["parent_page"].get("section_id") == resolved["move_source"]["id"]
            and resolved["move_source"]["id"] != resolved["disposable_section"]["id"],
            "Copy Page fixture source and destination are not isolated Sections.",
            "rich source Page and destination Section are distinct",
        )
    elif scenario == "copy-section":
        require(
            resolved["move_source"].get("parent_id") == resolved["group_a"]["id"]
            and resolved["group_a"]["id"] != resolved["group_b"]["id"],
            "Copy Section fixture source and destination groups are invalid.",
            "source Section and destination Group are distinct",
        )
    elif scenario == "copy-section-group":
        require(
            resolved["move_source"].get("parent_id") == resolved["group_a"]["id"],
            "Copy SectionGroup fixture source Section escaped its source Group.",
            "rich source Section is contained by the source Group",
        )
    elif scenario == "copy-notebook":
        require(
            resolved["parent_page"].get("section_id") == resolved["move_source"]["id"],
            "Copy Notebook fixture rich Page escaped its source Section.",
            "rich source Page is contained by the source Notebook Section",
        )
    elif scenario == "reconstructive-move-page":
        require(
            resolved["disposable_page"].get("section_id")
            != resolved["move_source"]["id"],
            "Reconstructive Move source Page already belongs to the destination Section.",
            "disposable source Page and destination Section are distinct",
        )

    if copy_fixture is not None:
        automated = {str(value).casefold() for value in copy_fixture.get("automated_content", [])}
        require(
            {"rich_text", "table", "image"}.issubset(automated),
            "Rich Copy fixture is missing a required automated content capability.",
            "rich text, table, and image capabilities were created and observed",
        )
    return checks


async def prepare_scenario_fixture(
    args: argparse.Namespace,
    options: RuntimeOptions,
    client: MCPStdioClient,
    notebook: dict[str, Any],
    notebook_path: str,
    spec: ScenarioSpec,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create only the selected scenario's declared fixture and persist evidence."""

    structure: dict[str, dict[str, Any]] = {}
    copy_fixture: dict[str, Any] | None = None
    token = str(uuid.uuid4())
    notebook_id = str(notebook["id"])

    if args.scenario == "create":
        group_a = await ensure_group(client, notebook_id, "Group-A")
        group_b = await ensure_group(client, notebook_id, "Group-B")
        delete_sandbox = await ensure_group(client, notebook_id, "Delete-Sandbox")
        move_source = await ensure_section(client, group_a["id"], "Move-Source")
        disposable_group = await ensure_group(client, delete_sandbox["id"], "Disposable-Group")
        disposable_section = await ensure_section(
            client, delete_sandbox["id"], "Disposable-Section"
        )
        parent = await ensure_page(
            client, move_source["id"], "Parent", f"Parent smoke token: {token}"
        )
        child = await ensure_page(
            client, move_source["id"], "Child", f"Child smoke token: {token}"
        )
        sibling = await ensure_page(
            client, move_source["id"], "Sibling", f"Sibling smoke token: {token}"
        )
        disposable_page = await ensure_page(
            client,
            disposable_section["id"],
            "Disposable-Page",
            f"Disposable smoke token: {token}",
        )
        parent = await enforce_page_position(client, move_source["id"], parent["id"], "", 1)
        child = await enforce_page_position(
            client, move_source["id"], child["id"], parent["id"], 2
        )
        sibling = await enforce_page_position(
            client, move_source["id"], sibling["id"], child["id"], 1
        )
        parent, copy_fixture = await ensure_copy_rich_fixture(
            client, parent, options.run_dir
        )
        structure.update(
            group_a=group_a,
            group_b=group_b,
            delete_sandbox=delete_sandbox,
            move_source=move_source,
            parent_page=parent,
            child_page=child,
            sibling_page=sibling,
            disposable_group=disposable_group,
            disposable_section=disposable_section,
            disposable_page=disposable_page,
        )
    elif args.scenario == "rename":
        target_key = args.target
        if target_key == "move_source":
            group = await ensure_group(client, notebook_id, "Rename-Group")
            structure[target_key] = await ensure_section(client, group["id"], "Move-Source")
        else:
            name = "Group-A" if target_key == "group_a" else "Group-B"
            structure[target_key] = await ensure_group(client, notebook_id, name)
    elif args.scenario == "reorder":
        section = await ensure_section(client, notebook_id, "Move-Source")
        parent = await ensure_page(client, section["id"], "Parent", f"Parent token: {token}")
        child = await ensure_page(client, section["id"], "Child", f"Child token: {token}")
        sibling = await ensure_page(client, section["id"], "Sibling", f"Sibling token: {token}")
        parent = await enforce_page_position(client, section["id"], parent["id"], "", 1)
        child = await enforce_page_position(client, section["id"], child["id"], parent["id"], 2)
        sibling = await enforce_page_position(client, section["id"], sibling["id"], child["id"], 1)
        structure.update(
            move_source=section,
            parent_page=parent,
            child_page=child,
            sibling_page=sibling,
        )
    elif args.scenario == "move":
        group_a = await ensure_group(client, notebook_id, "Group-A")
        group_b = await ensure_group(client, notebook_id, "Group-B")
        section = await ensure_section(client, group_a["id"], "Move-Source")
        structure.update(group_a=group_a, group_b=group_b, move_source=section)
    elif args.scenario == "delete":
        sandbox = await ensure_group(client, notebook_id, "Delete-Sandbox")
        disposable = await ensure_group(client, sandbox["id"], "Disposable-Group")
        structure.update(delete_sandbox=sandbox, disposable_group=disposable)
    elif args.scenario == "copy-page":
        source_section = await ensure_section(client, notebook_id, "Source")
        destination = await ensure_section(client, notebook_id, "Destination")
        page, copy_fixture = await _rich_page(client, source_section, options, token)
        structure.update(
            move_source=source_section,
            parent_page=page,
            disposable_section=destination,
        )
    elif args.scenario == "copy-section":
        source_group = await ensure_group(client, notebook_id, "Source-Group")
        destination = await ensure_group(client, notebook_id, "Group-B")
        source_section = await ensure_section(client, source_group["id"], "Move-Source")
        page, copy_fixture = await _rich_page(client, source_section, options, token)
        structure.update(
            group_a=source_group,
            group_b=destination,
            move_source=source_section,
            parent_page=page,
        )
    elif args.scenario == "copy-section-group":
        source_group = await ensure_group(client, notebook_id, "Group-A")
        source_section = await ensure_section(client, source_group["id"], "Move-Source")
        page, copy_fixture = await _rich_page(client, source_section, options, token)
        structure.update(
            group_a=source_group,
            move_source=source_section,
            parent_page=page,
        )
    elif args.scenario == "copy-notebook":
        source_section = await ensure_section(client, notebook_id, "Move-Source")
        page, copy_fixture = await _rich_page(client, source_section, options, token)
        structure.update(move_source=source_section, parent_page=page)
    elif args.scenario == "reconstructive-move-page":
        source_section = await ensure_section(client, notebook_id, "Source")
        destination = await ensure_section(client, notebook_id, "Destination")
        page = await ensure_page(
            client, source_section["id"], "Disposable-Page", f"Move token: {token}"
        )
        structure.update(disposable_page=page, move_source=destination)
    else:
        raise ValueError(f"Unsupported fixture scenario: {args.scenario}")

    snapshot = await capture_snapshot(client, notebook_id)
    manifest = new_manifest(
        options.run_dir,
        notebook,
        structure,
        notebook_path=notebook_path,
    )
    manifest["scenario_policies"] = {args.scenario: spec.policy.as_dict()}
    manifest["scenario_spec"] = spec.as_dict()
    manifest["scenario_spec"]["fixture_profile"]["actual_manifest_keys"] = sorted(structure)
    manifest["mcp_process_contract"] = {
        "maximum_starts": 1,
        "fixture_and_scenario_share_process": True,
    }
    if copy_fixture is not None:
        manifest["copy_fixture"] = copy_fixture
    manifest["fixture_validation"] = {"status": "pending"}
    write_json(manifest_path(options.run_dir), manifest)
    write_json(options.run_dir / "prepared.json", snapshot)
    write_json(options.run_dir / "page-hashes.json", snapshot.get("page_hashes", {}))
    fixture_result = {
        "scenario": args.scenario,
        "notebook": stable_item(notebook),
        "structure_ids": {key: value["id"] for key, value in structure.items()},
        "fixture_profile": manifest["scenario_spec"]["fixture_profile"],
        "validation": {"passed": False, "checks": []},
    }
    write_json(options.run_dir / "fixture-result.json", fixture_result)

    declared = set(spec.fixture.manifest_keys)
    try:
        if args.scenario == "rename" and set(structure) != {args.target}:
            raise InvariantFailure(
                "Rename fixture must create exactly the one CLI-selected manifest key."
            )
        if args.scenario != "rename" and not declared.issubset(structure):
            missing = sorted(declared - set(structure))
            raise InvariantFailure(f"Fixture did not create declared manifest keys: {missing}")
        validation_checks = _validate_fixture_snapshot(
            args.scenario,
            snapshot,
            structure,
            copy_fixture,
        )
    except InvariantFailure as exc:
        manifest["fixture_validation"] = {
            "status": "failed",
            "error": str(exc),
        }
        fixture_result["validation"] = {
            "passed": False,
            "checks": [],
            "error": str(exc),
        }
        write_json(manifest_path(options.run_dir), manifest)
        write_json(options.run_dir / "fixture-result.json", fixture_result)
        raise

    manifest["fixture_validation"] = {
        "status": "passed",
        "checks": validation_checks,
    }
    fixture_result["validation"] = {"passed": True, "checks": validation_checks}
    write_json(manifest_path(options.run_dir), manifest)
    write_json(options.run_dir / "fixture-result.json", fixture_result)
    return manifest, fixture_result


__all__ = ["prepare_scenario_fixture"]
