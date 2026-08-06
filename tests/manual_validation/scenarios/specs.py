"""Static, reviewable scenario policies and minimal fixture profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..mcp_stdio_client import (
    COPY_NO_DELETE_POLICY,
    COPY_POLICY,
    MOVE_POLICY,
    RECONSTRUCTIVE_MOVE_PAGE_POLICY,
    ScenarioPolicy,
    WRITE_POLICY,
)
from ._config import (
    COPY_NOTEBOOK_TOOLS,
    COPY_TOOLS,
    CREATE_TOOLS,
    DELETE_TOOLS,
    MOVE_TOOLS,
    RECONSTRUCTIVE_MOVE_PAGE_TOOLS,
    RENAME_TOOLS,
    REORDER_TOOLS,
)


@dataclass(frozen=True)
class FixtureProfile:
    name: str
    expected_structure: tuple[str, ...]
    content_capabilities: tuple[str, ...]
    manifest_keys: tuple[str, ...]
    creation_tools: frozenset[str]
    validation_conditions: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "expected_structure": list(self.expected_structure),
            "content_capabilities": list(self.content_capabilities),
            "manifest_keys": list(self.manifest_keys),
            "creation_tools": sorted(self.creation_tools),
            "validation_conditions": list(self.validation_conditions),
        }


@dataclass(frozen=True)
class ScenarioSpec:
    name: str
    fixture: FixtureProfile
    policy: ScenarioPolicy
    tool_allowlist: frozenset[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.name,
            "fixture_profile": self.fixture.as_dict(),
            "mutation_policy": self.policy.as_dict(),
            "tool_allowlist": sorted(self.tool_allowlist),
        }


def _profile(
    name: str,
    structure: tuple[str, ...],
    keys: tuple[str, ...],
    tools: set[str],
    *,
    content: tuple[str, ...] = (),
    checks: tuple[str, ...] = ("all declared manifest keys resolve to fresh IDs",),
) -> FixtureProfile:
    return FixtureProfile(
        name=name,
        expected_structure=structure,
        content_capabilities=content,
        manifest_keys=keys,
        creation_tools=frozenset(tools),
        validation_conditions=checks,
    )


DELETE_SCENARIO_POLICY = ScenarioPolicy(writes_enabled=True, deletes_enabled=True)
CREATE_FIXTURE_TOOLS = set(CREATE_TOOLS) - {"create_notebook"}

SCENARIO_SPECS = {
    "create": ScenarioSpec(
        "create",
        _profile(
            "full-preset",
            (
                "Group-A/Move-Source/{Parent,Child,Sibling}",
                "Group-B",
                "Delete-Sandbox/Disposable-Group",
                "Delete-Sandbox/Disposable-Section/Disposable-Page",
            ),
            (
                "group_a", "group_b", "delete_sandbox", "move_source",
                "parent_page", "child_page", "sibling_page", "disposable_group",
                "disposable_section", "disposable_page",
            ),
            CREATE_FIXTURE_TOOLS,
            content=("RichText", "Table", "Image", "page_tree"),
        ),
        WRITE_POLICY,
        frozenset(CREATE_FIXTURE_TOOLS),
    ),
    "rename": ScenarioSpec(
        "rename",
        _profile(
            "rename-target",
            ("selected Group or Group/Section target",),
            ("one_of(group_a,group_b,move_source)",),
            {"create_section_group", "create_section"},
            checks=(
                "exactly one CLI-selected rename target key is created",
                "the selected key resolves to a fresh active ID",
            ),
        ),
        WRITE_POLICY,
        frozenset(RENAME_TOOLS | {"create_section_group", "create_section"}),
    ),
    "reorder": ScenarioSpec(
        "reorder",
        _profile(
            "page-tree",
            ("Move-Source/{Parent(level=1),Child(level=2),Sibling(level=1)}",),
            ("move_source", "parent_page", "child_page", "sibling_page"),
            {"create_section", "create_page", "reorder_page"},
            content=("plain_text", "page_tree"),
        ),
        WRITE_POLICY,
        frozenset(REORDER_TOOLS | {"create_section", "create_page"}),
    ),
    "move": ScenarioSpec(
        "move",
        _profile(
            "section-move",
            ("Group-A/Move-Source", "Group-B"),
            ("group_a", "group_b", "move_source"),
            {"create_section_group", "create_section"},
        ),
        MOVE_POLICY,
        frozenset(MOVE_TOOLS | {"create_section_group", "create_section"}),
    ),
    "delete": ScenarioSpec(
        "delete",
        _profile(
            "disposable-group",
            ("Delete-Sandbox/Disposable-Group",),
            ("delete_sandbox", "disposable_group"),
            {"create_section_group"},
            checks=(
                "disposable_group is a descendant of delete_sandbox",
                "delete target ID is manifest-allowlisted",
            ),
        ),
        DELETE_SCENARIO_POLICY,
        frozenset(DELETE_TOOLS | {"create_section_group"}),
    ),
    "copy-page": ScenarioSpec(
        "copy-page",
        _profile(
            "rich-page-copy",
            ("Source/Parent[rich text+table+image]", "Destination"),
            ("move_source", "parent_page", "disposable_section"),
            {"create_section", "create_page", "append_to_page", "add_image_to_page"},
            content=("RichText", "Table", "Image", "Outline"),
        ),
        COPY_POLICY,
        frozenset(
            COPY_TOOLS
            | {"create_section", "create_page", "append_to_page", "add_image_to_page"}
        ),
    ),
    "copy-section": ScenarioSpec(
        "copy-section",
        _profile(
            "rich-section-copy",
            ("Source-Group/Move-Source/Rich-Page", "Group-B"),
            ("group_a", "group_b", "move_source", "parent_page"),
            {"create_section_group", "create_section", "create_page", "append_to_page", "add_image_to_page"},
            content=("RichText", "Table", "Image", "Outline"),
        ),
        COPY_POLICY,
        frozenset(
            COPY_TOOLS
            | {"create_section_group", "create_section", "create_page", "append_to_page", "add_image_to_page"}
        ),
    ),
    "copy-section-group": ScenarioSpec(
        "copy-section-group",
        _profile(
            "rich-group-copy",
            ("Group-A/Move-Source/Rich-Page",),
            ("group_a", "move_source", "parent_page"),
            {"create_section_group", "create_section", "create_page", "append_to_page", "add_image_to_page"},
            content=("RichText", "Table", "Image", "Outline"),
        ),
        COPY_POLICY,
        frozenset(
            COPY_TOOLS
            | {"create_section_group", "create_section", "create_page", "append_to_page", "add_image_to_page"}
        ),
    ),
    "copy-notebook": ScenarioSpec(
        "copy-notebook",
        _profile(
            "rich-notebook-copy",
            ("Move-Source/Rich-Page", "allowlisted local Copy root"),
            ("move_source", "parent_page"),
            {"create_section", "create_page", "append_to_page", "add_image_to_page"},
            content=("RichText", "Table", "Image", "Outline"),
        ),
        COPY_NO_DELETE_POLICY,
        frozenset(
            COPY_NOTEBOOK_TOOLS
            | {"create_section", "create_page", "append_to_page", "add_image_to_page"}
        ),
    ),
    "reconstructive-move-page": ScenarioSpec(
        "reconstructive-move-page",
        _profile(
            "disposable-page-move",
            ("Source/Disposable-Page", "Destination"),
            ("disposable_page", "move_source"),
            {"create_section", "create_page"},
            content=("plain_text",),
        ),
        RECONSTRUCTIVE_MOVE_PAGE_POLICY,
        frozenset(RECONSTRUCTIVE_MOVE_PAGE_TOOLS | {"create_section", "create_page"}),
    ),
}


def get_scenario_spec(name: str) -> ScenarioSpec:
    try:
        return SCENARIO_SPECS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown scenario spec: {name}") from exc


__all__ = [
    "FixtureProfile",
    "SCENARIO_SPECS",
    "ScenarioSpec",
    "get_scenario_spec",
]
