"""Static, reviewable scenario policies and minimal fixture profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...mcp_stdio_client import (
    COPY_NO_DELETE_POLICY,
    COPY_POLICY,
    MOVE_PAGE_POLICY,
    MOVE_CONTAINERS_POLICY,
    REPARENT_POLICY,
    REORDER_SECTION_GROUP_POLICY,
    REORDER_SECTION_POLICY,
    ScenarioPolicy,
    WRITE_POLICY,
)
from .config import (
    COPY_NOTEBOOK_TOOLS,
    COPY_PAGE_TOOLS,
    COPY_TOOLS,
    CREATE_TOOLS,
    DELETE_TOOLS,
    MOVE_PAGE_TOOLS,
    MOVE_SECTION_GROUP_TOOLS,
    MOVE_SECTION_TOOLS,
    REPARENT_PAGE_TOOLS,
    REPARENT_SECTION_TOOLS,
    REPARENT_SECTION_GROUP_TOOLS,
    READ_TOOLS,
    RENAME_TOOLS,
    REORDER_PAGE_TOOLS,
    REORDER_SECTION_GROUP_TOOLS,
    REORDER_SECTION_TOOLS,
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
    execution_contract: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.name,
            "fixture_profile": self.fixture.as_dict(),
            "mutation_policy": self.policy.as_dict(),
            "tool_allowlist": sorted(self.tool_allowlist),
            "execution_contract": dict(self.execution_contract),
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
LAYERED_PAGE_FIXTURE_TOOLS = {
    "create_page",
    "append_to_page",
    "add_image_to_page",
    "reorder_page",
}


SCENARIO_SPECS = {
    "create": ScenarioSpec(
        "create",
        _profile(
            "full-preset",
            (
                "Group-A/Content-Section/{Parent,Child,Sibling}",
                "Group-A/Duplicate-Title-Target (empty before scenario execution)",
                "Group-B",
                "Delete-Sandbox/Disposable-Group",
                "Delete-Sandbox/Disposable-Section/Disposable-Page",
            ),
            (
                "group_a", "group_b", "delete_sandbox", "content_section",
                "duplicate_title_section",
                "parent_page", "child_page", "sibling_page", "disposable_group",
                "disposable_section", "disposable_page",
            ),
            CREATE_FIXTURE_TOOLS,
            content=("RichText", "Table", "Image", "page_tree"),
        ),
        DELETE_SCENARIO_POLICY,
        frozenset(CREATE_FIXTURE_TOOLS | {"delete_page"}),
    ),
    "rename": ScenarioSpec(
        "rename",
        _profile(
            "rename-target",
            ("selected Group or Group/Section target",),
            ("one_of(group_a,group_b,content_section)",),
            {"create_section_group", "create_section"},
            checks=(
                "exactly one CLI-selected rename target key is created",
                "the selected key resolves to a fresh active ID",
            ),
        ),
        WRITE_POLICY,
        frozenset(RENAME_TOOLS | {"create_section_group", "create_section"}),
    ),
    "reorder-page": ScenarioSpec(
        "reorder-page",
        _profile(
            "page-tree",
            (
                "Description/00-Reorder-Description explains before/after/restore",
                "01-Reorder-Page-Section/{01-Parent(level=1),02-Child(level=2),03-Sibling(level=1)}",
            ),
            (
                "description_section", "description_page", "reorder_section",
                "parent_page", "child_page", "sibling_page",
            ),
            {"create_section", "create_page", "reorder_page"},
            content=("plain_text", "page_tree", "numbered_page_titles", "human_readable_description"),
            checks=(
                "all scenario Pages use stable 00/01/02/03 title prefixes",
                "Description Page states 01,02,03 before; 01,03,02 after; 01,02,03 restored",
                "numbered Page levels and derived relationships match the profile",
            ),
        ),
        WRITE_POLICY,
        frozenset(REORDER_PAGE_TOOLS | {"create_section", "create_page"}),
    ),
    "reorder-section": ScenarioSpec(
        "reorder-section",
        _profile(
            "section-order",
            (
                "00-Description/00-Reorder-Section-Description explains both legal parents",
                "Notebook/{01-Root-Section-A,02-Root-Section-B,03-Root-Section-C}",
                "01-Section-Parent/{01-Group-Section-A,02-Group-Section-B,03-Group-Section-C}",
            ),
            (
                "description_section", "description_page",
                "root_section_a", "root_section_b", "root_section_c",
                "section_parent_group", "group_section_a", "group_section_b", "group_section_c",
                "root_page_a", "root_page_b", "root_page_c",
                "group_page_a", "group_page_b", "group_page_c",
            ),
            {"create_section_group", "create_section", "create_page"},
            content=("plain_text", "section_order", "page_identity", "numbered_sections", "human_readable_description"),
            checks=(
                "Notebook-parent Sections have numbered 01/02/03 order and one numbered Page each",
                "SectionGroup-parent Sections have numbered 01/02/03 order and one numbered Page each",
                "Description Page states before 01,02,03; after 01,03,02; restored 01,02,03 for both parents",
            ),
        ),
        REORDER_SECTION_POLICY,
        frozenset(REORDER_SECTION_TOOLS),
    ),
    "reorder-section-group": ScenarioSpec(
        "reorder-section-group",
        _profile(
            "section-group-order",
            (
                "00-Description/00-Reorder-SectionGroup-Description explains both legal parents",
                "Notebook/{01-Root-Group-A,02-Root-Group-B,03-Root-Group-C}",
                "00-Group-Parent/{01-Nested-Group-A,02-Nested-Group-B,03-Nested-Group-C}",
                "each numbered target Group contains one numbered Section and Page",
            ),
            (
                "description_section", "description_page",
                "root_group_a", "root_group_b", "root_group_c",
                "root_section_a", "root_section_b", "root_section_c",
                "root_page_a", "root_page_b", "root_page_c",
                "section_group_parent",
                "nested_group_a", "nested_group_b", "nested_group_c",
                "nested_section_a", "nested_section_b", "nested_section_c",
                "nested_page_a", "nested_page_b", "nested_page_c",
            ),
            {"create_section_group", "create_section", "create_page"},
            content=("plain_text", "section_group_order", "descendant_identity", "numbered_section_groups", "human_readable_description"),
            checks=(
                "Notebook-parent SectionGroups have exact numbered 01/02/03 order",
                "SectionGroup-parent SectionGroups have exact numbered 01/02/03 order",
                "each numbered target SectionGroup has one numbered Section/Page descendant",
                "Description Page states before/after/restore for both legal parents",
            ),
        ),
        REORDER_SECTION_GROUP_POLICY,
        frozenset(REORDER_SECTION_GROUP_TOOLS),
    ),
    "reparent-section": ScenarioSpec(
        "reparent-section",
        _profile(
            "section-reparent",
            (
                "00-Description/00-Reparent-Section-Description explains all three transitions",
                "Notebook/01-Notebook-To-Group-Section/01-Notebook-To-Group-Page -> 01-Destination-Group",
                "02-Source-Group/02-Group-To-Notebook-Section/02-Group-To-Notebook-Page -> Notebook",
                "03-Source-Group/03-Group-To-Group-Section/03-Group-To-Group-Page -> 03-Destination-Group",
            ),
            (
                "description_section",
                "description_page",
                "notebook_to_group_destination",
                "notebook_to_group_section",
                "notebook_to_group_page",
                "group_to_notebook_source",
                "group_to_notebook_section",
                "group_to_notebook_page",
                "group_to_group_source",
                "group_to_group_destination",
                "group_to_group_section",
                "group_to_group_page",
            ),
            {"create_section_group", "create_section", "create_page"},
            content=(
                "plain_text",
                "section_parent_transition",
                "page_identity",
                "numbered_sections",
                "human_readable_description",
            ),
            checks=(
                "case 1 reparents a Notebook-root Section to a root SectionGroup",
                "case 2 reparents a SectionGroup child Section to the Notebook root",
                "case 3 reparents a Section between distinct root SectionGroups",
                "all three target Sections contain one numbered Page",
                "Description Page states before/after/restore for all three cases",
            ),
        ),
        REPARENT_POLICY,
        frozenset(
            REPARENT_SECTION_TOOLS
            | {"create_section_group", "create_section", "create_page", "get_page_text"}
        ),
    ),
    "reparent-page": ScenarioSpec(
        "reparent-page",
        _profile(
            "typed-page-reparent",
            (
                "00-Description/00-Reparent-Page-Description explains before/after/restore",
                "01-Source-Section/01-Reparent-Page -> 02-Destination-Section",
                "02-Destination-Section/02-Destination-Anchor remains unrelated",
            ),
            (
                "description_section",
                "description_page",
                "source_section",
                "destination_section",
                "reparent_page",
                "destination_anchor_page",
            ),
            {"create_section", "create_page", "append_to_page", "add_image_to_page"},
            content=(
                "plain_text",
                "page_identity_remap",
                "id_normalized_rich_content",
                "rich_text",
                "table",
                "list",
                "tag",
                "image",
                "numbered_pages",
                "human_readable_description",
            ),
            checks=(
                "source and destination Sections are distinct children of one Notebook",
                "target Page is a root Page in the source Section",
                "destination anchor remains outside the reparented target",
                "Description Page and Section belong to the fixture Notebook",
                "Description, Sections, target Page, and anchor use stable numbering",
                "target Page owns the declared rich-content fixture",
                "rich text, table, and image capabilities were created and observed",
                "target Page contains three mixed List/Tag items alongside rich content",
            ),
        ),
        REPARENT_POLICY,
        frozenset(REPARENT_PAGE_TOOLS | {"get_page_text"}),
    ),
    "reparent-section-group": ScenarioSpec(
        "reparent-section-group",
        _profile(
            "typed-section-group-reparent",
            (
                "00-Description/00-Reparent-SectionGroup-Description explains all three transitions",
                "Notebook/01-Notebook-To-Group-Target/{01-Descendant-Section/01-Descendant-Page} -> 01-Destination-Parent",
                "02-Source-Parent/02-Group-To-Notebook-Target/{02-Descendant-Section/02-Descendant-Page} -> Notebook",
                "03-Source-Parent/03-Group-To-Group-Target/{03-Descendant-Section/03-Descendant-Page} -> 03-Destination-Parent",
            ),
            (
                "description_section",
                "description_page",
                "notebook_to_group_destination",
                "notebook_to_group_target",
                "notebook_to_group_section",
                "notebook_to_group_page",
                "group_to_notebook_source",
                "group_to_notebook_target",
                "group_to_notebook_section",
                "group_to_notebook_page",
                "group_to_group_source",
                "group_to_group_destination",
                "group_to_group_target",
                "group_to_group_section",
                "group_to_group_page",
            ),
            {"create_section_group", "create_section", "create_page"},
            content=(
                "plain_text",
                "descendant_identity",
                "page_content",
                "numbered_section_groups",
                "human_readable_description",
            ),
            checks=(
                "case 1 target is Notebook-root and destination is a root SectionGroup",
                "case 2 target is under a root SectionGroup and destination is Notebook",
                "case 3 source and destination are distinct root SectionGroups",
                "all three reparent cases and descendants use stable numbering",
                "all three reparent cases use distinct target Group IDs",
                "Description Page and Section belong to the fixture Notebook",
            ),
        ),
        REPARENT_POLICY,
        frozenset(REPARENT_SECTION_GROUP_TOOLS | {"get_page_text"}),
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
            (
                "source:00-Description/00-Copy-Page-Description[3 scopes x 2 subtree modes]",
                "Source/01-Source-Parent[strict rich text+inline/display equations+table+image]",
                "Source/01-Source-Parent/02-Source-Child[semantic list+tag]",
                "source:Destination/02-Source-Child[duplicate-title anchor]",
                "destination:Cross-Notebook-Destination/02-Source-Child[duplicate-title anchor]",
            ),
            (
                "description_section",
                "description_page",
                "source_section",
                "parent_page",
                "semantic_page",
                "disposable_section",
                "cross_section_anchor",
                "cross_notebook_section",
                "cross_notebook_anchor",
            ),
            {"create_section", "get_page_text"} | LAYERED_PAGE_FIXTURE_TOOLS,
            content=(
                "RichText",
                "DisplayEquation",
                "Table",
                "Image",
                "Outline",
                "List",
                "Tag",
            ),
            checks=(
                "Description Page states all three destination scopes and both subtree modes",
                "source parent contains one inline RichText equation and one DisplayEquation",
                "every omitted scope maps only the strict parent",
                "every explicit true scope maps the complete parent/child subtree",
                "source parent and child remain unchanged after all six copies",
                "same-title destination anchors remain unchanged after all six copies",
                "cross-Notebook targets appear only in the destination role",
            ),
        ),
        COPY_POLICY,
        frozenset(
            COPY_PAGE_TOOLS
            | {"create_section", "get_page_text"}
            | LAYERED_PAGE_FIXTURE_TOOLS
        ),
        {
            "cases": [
                {
                    "name": "same-section-root-only",
                    "destination_role": "source",
                    "destination_key": "source_section",
                    "destination_scope": "same-section",
                    "include_descendants": "omitted",
                    "expected_page_count": 1,
                },
                {
                    "name": "same-section-subtree",
                    "destination_role": "source",
                    "destination_key": "source_section",
                    "destination_scope": "same-section",
                    "include_descendants": True,
                    "expected_page_count": 2,
                },
                {
                    "name": "cross-section-root-only",
                    "destination_role": "source",
                    "destination_key": "disposable_section",
                    "destination_scope": "cross-section",
                    "include_descendants": "omitted",
                    "expected_page_count": 1,
                },
                {
                    "name": "cross-section-subtree",
                    "destination_role": "source",
                    "destination_key": "disposable_section",
                    "destination_scope": "cross-section",
                    "include_descendants": True,
                    "expected_page_count": 2,
                },
                {
                    "name": "cross-notebook-root-only",
                    "destination_role": "destination",
                    "destination_key": "cross_notebook_section",
                    "destination_scope": "cross-notebook",
                    "include_descendants": "omitted",
                    "expected_page_count": 1,
                },
                {
                    "name": "cross-notebook-subtree",
                    "destination_role": "destination",
                    "destination_key": "cross_notebook_section",
                    "destination_scope": "cross-notebook",
                    "include_descendants": True,
                    "expected_page_count": 2,
                },
            ]
        },
    ),
    "copy-section": ScenarioSpec(
        "copy-section",
        _profile(
            "rich-section-copy",
            (
                "source:Source-Group/Source-Section/Rich-Page[strict rich text+table+image]",
                "source:Source-Group/Source-Section/Rich-Page/List-Tag-Page[semantic list+tag]",
                "source:Group-B",
                "destination:Cross-Notebook-Group",
            ),
            (
                "group_a",
                "group_b",
                "source_section",
                "parent_page",
                "semantic_page",
                "cross_notebook_group",
            ),
            {"create_section_group", "create_section"} | LAYERED_PAGE_FIXTURE_TOOLS,
            content=("RichText", "Table", "Image", "Outline", "List", "Tag"),
            checks=(
                "strict parent uses canonical read-back verification",
                "semantic child uses List/Tag semantic read-back verification",
                "same-Notebook and cross-Notebook destination Groups are role-bound",
            ),
        ),
        COPY_POLICY,
        frozenset(
            COPY_TOOLS
            | {"create_section_group", "create_section"}
            | LAYERED_PAGE_FIXTURE_TOOLS
        ),
        {
            "cases": [
                {
                    "name": "same-notebook",
                    "destination_role": "source",
                    "destination_key": "group_b",
                    "destination_scope": "same-notebook",
                },
                {
                    "name": "cross-notebook",
                    "destination_role": "destination",
                    "destination_key": "cross_notebook_group",
                    "destination_scope": "cross-notebook",
                },
            ]
        },
    ),
    "copy-section-group": ScenarioSpec(
        "copy-section-group",
        _profile(
            "rich-group-copy",
            (
                "source:Group-A/Source-Section/Rich-Page[strict rich text+table+image]",
                "source:Group-A/Source-Section/Rich-Page/List-Tag-Page[semantic list+tag]",
                "destination:Cross-Notebook-Anchor",
            ),
            (
                "group_a",
                "source_section",
                "parent_page",
                "semantic_page",
                "cross_notebook_anchor_section",
            ),
            {"create_section_group", "create_section"} | LAYERED_PAGE_FIXTURE_TOOLS,
            content=("RichText", "Table", "Image", "Outline", "List", "Tag"),
            checks=(
                "strict parent uses canonical read-back verification",
                "semantic child uses List/Tag semantic read-back verification",
                "same-Notebook and cross-Notebook destination roots are role-bound",
            ),
        ),
        COPY_POLICY,
        frozenset(
            COPY_TOOLS
            | {"create_section_group", "create_section"}
            | LAYERED_PAGE_FIXTURE_TOOLS
        ),
        {
            "cases": [
                {
                    "name": "same-notebook",
                    "destination_role": "source",
                    "destination_key": "source_notebook",
                    "destination_scope": "same-notebook",
                },
                {
                    "name": "cross-notebook",
                    "destination_role": "destination",
                    "destination_key": "destination_notebook",
                    "destination_scope": "cross-notebook",
                },
            ]
        },
    ),
    "copy-notebook": ScenarioSpec(
        "copy-notebook",
        _profile(
            "rich-notebook-copy",
            (
                "Source-Section/Rich-Page[strict rich text+table+image]",
                "Source-Section/Rich-Page/List-Tag-Page[semantic list+tag]",
                "Source-Group/Grouped-Section/Grouped-Page[plain synthetic content]",
                "allowlisted local Copy root",
            ),
            (
                "source_section",
                "parent_page",
                "semantic_page",
                "source_group",
                "group_section",
                "group_page",
            ),
            {"create_section_group", "create_section"} | LAYERED_PAGE_FIXTURE_TOOLS,
            content=("RichText", "Table", "Image", "Outline", "List", "Tag"),
            checks=(
                "strict parent uses canonical read-back verification",
                "semantic child uses List/Tag semantic read-back verification",
                "source Notebook contains a SectionGroup/Section/Page subtree",
            ),
        ),
        COPY_NO_DELETE_POLICY,
        frozenset(
            COPY_NOTEBOOK_TOOLS
            | {"create_section_group", "create_section"}
            | LAYERED_PAGE_FIXTURE_TOOLS
        ),
    ),
    "move-page": ScenarioSpec(
        "move-page",
        _profile(
            "disposable-page-move",
            (
                "source:Source/01-Root-Only/02-Root-Only-Child",
                "source:Source/03-Subtree/04-Subtree-Child",
                "destination:Destination",
            ),
            (
                "source_section",
                "root_only_page",
                "root_only_child",
                "subtree_page",
                "subtree_child",
                "destination_section",
            ),
            {"create_section", "create_page", "reorder_page"},
            content=("Outline", "RichText"),
            checks=(
                "root-only Move copies one Page and preserves/promotes its excluded child",
                "subtree Move copies and removes exactly the two selected Pages",
                "both cases use cross-Notebook destination and non-permanent source deletion",
            ),
        ),
        MOVE_PAGE_POLICY,
        frozenset(
            MOVE_PAGE_TOOLS
            | {"create_section", "create_page", "reorder_page"}
        ),
        {
            "cases": [
                {
                    "name": "cross-notebook-root-only",
                    "source_key": "root_only_page",
                    "child_key": "root_only_child",
                    "include_descendants": "omitted",
                    "expected_page_count": 1,
                },
                {
                    "name": "cross-notebook-subtree",
                    "source_key": "subtree_page",
                    "child_key": "subtree_child",
                    "include_descendants": True,
                    "expected_page_count": 2,
                },
            ]
        },
    ),
    "move-section": ScenarioSpec(
        "move-section",
        _profile(
            "disposable-cross-notebook-section-move",
            (
                "source:Move-Section-Source/Move-Section-Page",
                "destination:Notebook root",
            ),
            ("source_section", "source_page"),
            {"create_section", "create_page"},
            content=("Outline", "RichText"),
            checks=(
                "one complete Section subtree is copied into another Notebook",
                "exactly one non-permanent source Section root delete is authorized after Copy verification",
                "all original source subtree IDs become inactive",
            ),
        ),
        MOVE_CONTAINERS_POLICY,
        frozenset(MOVE_SECTION_TOOLS | {"create_section", "create_page"}),
        {"source_key": "source_section", "destination_role": "destination"},
    ),
    "move-section-group": ScenarioSpec(
        "move-section-group",
        _profile(
            "disposable-cross-notebook-section-group-move",
            (
                "source:Move-Group-Source/Move-Group-Section/Move-Group-Page",
                "destination:Notebook root",
            ),
            ("source_group", "source_section", "source_page"),
            {"create_section_group", "create_section", "create_page"},
            content=("Outline", "RichText"),
            checks=(
                "one complete SectionGroup subtree is copied into another Notebook",
                "exactly one non-permanent source SectionGroup root delete is authorized after Copy verification",
                "all original source subtree IDs become inactive",
            ),
        ),
        MOVE_CONTAINERS_POLICY,
        frozenset(
            MOVE_SECTION_GROUP_TOOLS
            | {"create_section_group", "create_section", "create_page"}
        ),
        {"source_key": "source_group", "destination_role": "destination"},
    ),
}

_INTERACTIVE_TOOLS = READ_TOOLS | {"create_section", "create_page"}
for _scenario_name, _capability in (
    ("bootstrap-inserted-file-fixture", "InsertedFile"),
    ("bootstrap-ink-drawing-fixture", "InkDrawing"),
    ("bootstrap-media-file-fixture", "MediaFile"),
    ("bootstrap-shape-fixture", "UIShape"),
):
    SCENARIO_SPECS[_scenario_name] = ScenarioSpec(
        _scenario_name,
        _profile(
            f"interactive-{_capability.casefold()}",
            (f"00-{_capability}-Canvas/01-Interactive-Canvas",),
            ("canvas_section", "canvas_page"),
            {"create_section", "create_page"},
            content=(_capability,),
            checks=(
                "exact Canvas IDs remain active",
                "exactly one requested synthetic content object is present after checkpoint",
                "unexpected or misplaced content fails closed",
            ),
        ),
        WRITE_POLICY,
        frozenset(_INTERACTIVE_TOOLS),
        {"interactive_bootstrap": True, "included_in_all": False},
    )

for _scenario_name, _bootstrap_name, _capability in (
    ("interactive-copy-inserted-file", "bootstrap-inserted-file-fixture", "InsertedFile"),
    ("interactive-copy-ink-drawing", "bootstrap-ink-drawing-fixture", "InkDrawing"),
    ("interactive-copy-media-file", "bootstrap-media-file-fixture", "MediaFile"),
    ("interactive-copy-ui-shape", "bootstrap-shape-fixture", "UIShape"),
):
    _copy_tools = READ_TOOLS | {"plan_copy", "copy_page"}
    if _scenario_name == "interactive-copy-media-file":
        _copy_tools |= {"create_section"}
    SCENARIO_SPECS[_scenario_name] = ScenarioSpec(
        _scenario_name,
        SCENARIO_SPECS[_bootstrap_name].fixture,
        COPY_NO_DELETE_POLICY,
        frozenset(_copy_tools),
        {
            "interactive_copy_evidence": True,
            "capability": _capability,
            "cache_only": True,
            "bootstrap_on_miss": _bootstrap_name,
            "delete_permission": False,
            "page_xml_capture": "explicit_opt_in_sensitive_evidence",
            "same_and_cross_section": _scenario_name
            == "interactive-copy-media-file",
            "included_in_all": False,
        },
    )

SCENARIO_SPECS["copy-display-equation"] = ScenarioSpec(
    "copy-display-equation",
    _profile(
        "programmatic-display-equation-copy",
        (
            "Source/01-Source-Parent[prepared rich text, table, image, and one automatic display equation]",
        ),
        ("canvas_section", "canvas_page"),
        {"create_section", "create_page", "append_to_page", "add_image_to_page"},
        content=("Outline", "RichText", "Table", "Image", "DisplayEquation"),
        checks=(
            "exact Source Parent IDs remain active",
            "prepared rich text, table, and image remain present",
            "exactly one complete standalone display-block MathML equation is observed",
            "content-free MathML OE placement evidence is captured",
        ),
    ),
    COPY_POLICY,
    frozenset(
        COPY_PAGE_TOOLS
        | {"create_section", "create_page", "append_to_page", "add_image_to_page"}
    ),
    {
        "programmatic_display_equation": True,
        "bounded_copy_chain": 3,
        "documented_com_normalization": (
            "zero_or_one_empty_span_br_per_display_equation"
        ),
        "included_in_all": False,
    },
)

SCENARIO_SPECS["bootstrap-inline-equation-fixture"] = ScenarioSpec(
    "bootstrap-inline-equation-fixture",
    _profile(
        "interactive-inline-equation",
        (
            "Source/01-Source-Parent[prepared rich text, table, image, and one automatic inline equation]",
        ),
        ("canvas_section", "canvas_page"),
        {"create_section", "create_page", "append_to_page", "add_image_to_page"},
        content=("Outline", "RichText", "Table", "Image", "InlineEquation"),
        checks=(
            "exact Source Parent IDs remain active",
            "prepared rich text, table, and image remain present",
            "exactly one complete MathML equation remains inline with visible surrounding text",
            "the inline equation has no display attribute and no standalone formula line",
        ),
    ),
    WRITE_POLICY,
    frozenset(_INTERACTIVE_TOOLS | {"append_to_page", "add_image_to_page"}),
    {
        "interactive_bootstrap": True,
        "programmatic_inline_equation": True,
        "included_in_all": False,
    },
)

SCENARIO_SPECS["interactive-copy-inline-equation"] = ScenarioSpec(
    "interactive-copy-inline-equation",
    SCENARIO_SPECS["bootstrap-inline-equation-fixture"].fixture,
    COPY_NO_DELETE_POLICY,
    frozenset(READ_TOOLS | {"plan_copy", "copy_page"}),
    {
        "interactive_copy_evidence": True,
        "capability": "InlineEquation",
        "cache_only": True,
        "bootstrap_on_miss": "bootstrap-inline-equation-fixture",
        "delete_permission": False,
        "page_xml_capture": "explicit_opt_in_sensitive_evidence",
        "same_and_cross_section": False,
        "included_in_all": False,
    },
)

SCENARIO_SPECS["bootstrap-user-authored-fixture"] = ScenarioSpec(
    "bootstrap-user-authored-fixture",
    _profile(
        "user-authored-zone",
        (
            "00-System-Instructions/00-Reserved-Marker-Do-Not-Edit",
            "01-Authoring-Zone/01-Author-Here",
        ),
        (
            "instructions_section",
            "instructions_page",
            "authoring_zone_section",
            "authoring_zone_page",
        ),
        {"create_section", "create_page"},
        content=("bounded_user_authored_content",),
        checks=(
            "reserved marker remains unchanged",
            "all edits remain inside the exact authoring zone",
            "unknown capabilities publish evidence_only and are mutation-ineligible",
        ),
    ),
    WRITE_POLICY,
    frozenset(_INTERACTIVE_TOOLS),
    {"interactive_bootstrap": True, "user_authored": True, "included_in_all": False},
)

SCENARIO_SPECS["user-authored-fixture-consumer"] = ScenarioSpec(
    "user-authored-fixture-consumer",
    SCENARIO_SPECS["bootstrap-user-authored-fixture"].fixture,
    ScenarioPolicy(),
    frozenset(READ_TOOLS),
    {"interactive_consumer": True, "requires_explicit_instance": True, "included_in_all": False},
)

SCENARIO_SPECS["cache-invalidation"] = ScenarioSpec(
    "cache-invalidation",
    _profile(
        "cache-invalidation-probe",
        ("00-Cache-Invalidation/00-Owned-Probe",),
        ("probe_section", "probe_page"),
        {"create_section", "create_page"},
        content=("plain_text",),
        checks=(
            "probe IDs remain active before publication",
            "only this Recipe's exact fingerprint/instance may be invalidated",
        ),
    ),
    WRITE_POLICY,
    frozenset(_INTERACTIVE_TOOLS),
    {"cache_invalidation_probe": True, "included_in_all": False},
)

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
