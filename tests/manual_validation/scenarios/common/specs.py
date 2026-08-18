"""Static, reviewable scenario policies and minimal fixture profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...mcp_stdio_client import (
    COPY_NO_DELETE_POLICY,
    COPY_POLICY,
    RICH_COPY_NO_DELETE_POLICY,
    RICH_COPY_NOTEBOOK_POLICY,
    RICH_COPY_POLICY,
    RICH_REPARENT_POLICY,
    RICH_WRITE_POLICY,
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
    search_budget: dict[str, int] = field(default_factory=dict)
    batch_mutation_budget: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.name,
            "fixture_profile": self.fixture.as_dict(),
            "mutation_policy": self.policy.as_dict(),
            "tool_allowlist": sorted(self.tool_allowlist),
            "execution_contract": dict(self.execution_contract),
            "search_budget": dict(self.search_budget),
            "batch_mutation_budget": dict(self.batch_mutation_budget),
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


DELETE_SCENARIO_POLICY = ScenarioPolicy(
    create_enabled=True,
    writes_enabled=True,
    deletes_enabled=True,
)
CONVERGENCE_SCENARIO_POLICY = ScenarioPolicy(
    create_enabled=True,
    writes_enabled=True,
    deletes_enabled=True,
    local_file_io_enabled=True,
    ui_control_enabled=True,
    notebook_lifecycle_enabled=True,
)
CREATE_SCENARIO_TOOLS = READ_TOOLS | {
    "create_section_group", "create_section", "create_page",
}
LAYERED_PAGE_FIXTURE_TOOLS = {
    "create_page",
    "append_page_content",
    "add_page_image_from_file",
    "reorder_page",
}


SCENARIO_SPECS = {
    "onenote-convergence": ScenarioSpec(
        "onenote-convergence",
        _profile(
            "fresh-com-convergence",
            (
                "01-Convergence-Section/{01-Anchor,02-Anchor}",
                "a second run-scoped Notebook is created through the public Tool and closed",
                "run-scoped 03-Convergence-Probe is created, title/body/content updated, content-deleted, reordered, and deleted before production Close",
            ),
            (
                "convergence_section",
                "first_anchor_page",
                "second_anchor_page",
            ),
            {"create_section", "create_page", "reorder_page"},
            content=("plain_text", "page_content_object", "page_order", "production_convergence_evidence"),
            checks=(
                "both anchor Pages resolve to fresh exact IDs in one Section",
                "Sync reports accepted without observable completion",
                "Publish creates one exact run-scoped PDF target",
                "typed navigate_to reports UI action accepted without persistence claims",
                "public create_notebook returns an exact run-scoped identity and is closed",
                "Replace Body proves convergence and its non-atomic saga contract",
                "all mutation responses prove at least two stable live observations",
                "the disposable probe is non-permanently deleted before production close_notebook",
            ),
        ),
        CONVERGENCE_SCENARIO_POLICY,
        frozenset(
            READ_TOOLS
            | {
                "create_section",
                "create_notebook",
                "create_page",
                "rename_page",
                "replace_page_body",
                "append_page_content",
                "delete_page_content_object",
                "reorder_page",
                "delete_page",
                "close_notebook",
                "request_notebook_sync",
                "export_object_to_pdf",
                "get_hyperlink",
                "navigate_to",
            }
        ),
        {
            "fresh_only": True,
            "included_in_all": False,
            "effect_operations": [
                "request_notebook_sync",
                "export_object_to_pdf",
                "navigate_to",
            ],
        },
    ),
    "create": ScenarioSpec(
        "create",
        _profile(
            "minimal-create-target",
            (
                "Duplicate-Title-Target Section",
                "scenario same-title Pages are absent before execution",
            ),
            ("duplicate_title_section",),
            CREATE_SCENARIO_TOOLS,
            checks=(
                "the exact target Section resolves under the disposable Notebook",
                "a normalized duplicate Section batch is rejected before mutation and leaves the snapshot unchanged",
            ),
        ),
        DELETE_SCENARIO_POLICY,
        frozenset(
            CREATE_SCENARIO_TOOLS
            | {"delete_page", "delete_section", "delete_section_group"}
        ),
    ),
    "rename": ScenarioSpec(
        "rename",
        _profile(
            "rename-target",
            (
                "Rename-Group/Rename-Section/Rename-Page",
            ),
            ("section_group_target", "section_target", "page_target"),
            {"create_section_group", "create_section", "create_page"},
            checks=(
                "one fixed SectionGroup, nested Section, and Page resolve to fresh active IDs",
                "the scenario batch-renames and restores all three typed targets in one run",
            ),
        ),
        WRITE_POLICY,
        frozenset(RENAME_TOOLS | {"create_section_group", "create_section", "create_page"}),
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
                "Page-parent child_type=section is rejected before mutation and leaves the sorted snapshot unchanged",
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
                "notebook_to_group_anchor_a",
                "notebook_to_group_anchor_b",
                "notebook_to_group_section",
                "notebook_to_group_page",
                "group_to_notebook_source",
                "group_to_notebook_section",
                "group_to_notebook_page",
                "group_to_notebook_anchor_a",
                "group_to_notebook_anchor_b",
                "group_to_group_source",
                "group_to_group_destination",
                "group_to_group_anchor_a",
                "group_to_group_anchor_b",
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
                "destination_anchor_page_b",
            ),
            {"create_section", "create_page", "append_page_content", "add_page_image_from_file"},
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
                "destination contains two distinct Page anchors outside the reparented target",
                "Description Page and Section belong to the fixture Notebook",
                "Description, Sections, target Page, and anchor use stable numbering",
                "target Page owns the declared rich-content fixture",
                "rich text, table, and image capabilities were created and observed",
                "target Page contains three mixed List/Tag items alongside rich content",
            ),
        ),
        RICH_REPARENT_POLICY,
        frozenset(REPARENT_PAGE_TOOLS),
        {"page_text_projection": "before_after_restore_default_rich_v1"},
    ),
    "reparent-page-with-level": ScenarioSpec(
        "reparent-page-with-level",
        _profile(
            "typed-page-reparent-scope",
            (
                "one source Section contains independent root-only and subtree indentation trees",
                "both selected Pages start at level 2 and use only OneNote's legal levels 1-3",
                "destination Section contains two root Page anchors",
            ),
            (
                "description_section",
                "description_page",
                "source_section",
                "destination_section",
                "root_only_parent",
                "root_only_selected",
                "root_only_child",
                "subtree_parent",
                "subtree_selected",
                "subtree_child_a",
                "subtree_child_b",
                "destination_anchor_a",
                "destination_anchor_b",
            ),
            {
                "create_section",
                "create_page",
                "reorder_page",
                "append_page_content",
                "add_page_image_from_file",
            },
            content=("include_subpages", "rich_text", "table", "image", "numbered_pages"),
            checks=(
                "root-only selected Page starts at level 2 with one level-3 descendant",
                "full-subtree selected Page starts at level 2 with two branched level-3 descendants",
                "destination contains two root Page anchors",
                "both selected Pages own stable rich-content evidence",
            ),
        ),
        RICH_REPARENT_POLICY,
        frozenset(REPARENT_PAGE_TOOLS | {"reorder_page"}),
    ),
    "reparent-section-group": ScenarioSpec(
        "reparent-section-group",
        _profile(
            "typed-section-group-reparent",
            (
                "00-Description/00-Reparent-SectionGroup-Description explains all three transitions",
                "Notebook/01-Notebook-To-Group-Target/{01-Descendant-Section/01-Descendant-Page} -> 01-Destination-Parent",
                "02-Source-Parent/{00-Source-Anchor-A,02-Group-To-Notebook-Target/{02-Descendant-Section/02-Descendant-Page},99-Source-Anchor-B} -> Notebook",
                "03-Source-Parent/{00-Source-Anchor-A,03-Group-To-Group-Target/{03-Descendant-Section/03-Descendant-Page},99-Source-Anchor-B} -> 03-Destination-Parent",
            ),
            (
                "description_section",
                "description_page",
                "notebook_to_group_destination",
                "notebook_to_group_anchor_a",
                "notebook_to_group_anchor_b",
                "notebook_to_group_target",
                "notebook_to_group_section",
                "notebook_to_group_page",
                "group_to_notebook_source",
                "group_to_notebook_source_anchor_a",
                "group_to_notebook_source_anchor_b",
                "group_to_notebook_target",
                "group_to_notebook_section",
                "group_to_notebook_page",
                "group_to_notebook_anchor_a",
                "group_to_notebook_anchor_b",
                "group_to_group_source",
                "group_to_group_source_anchor_a",
                "group_to_group_source_anchor_b",
                "group_to_group_destination",
                "group_to_group_anchor_a",
                "group_to_group_anchor_b",
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
                "both restorable source SectionGroups retain two distinct stability anchors",
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
            (
                "Delete-Sandbox/Disposable-Group/Disposable-Section",
                "Delete-Sandbox/Disposable-Section-Target",
                "Delete-Sandbox/Disposable-Page-Section/root-only and subtree Page targets",
                "Delete-Sandbox/Budget-Overlimit-Section/six direct Pages",
            ),
            (
                "delete_sandbox", "disposable_group", "disposable_section",
                "disposable_section_target", "disposable_page_section", "disposable_page_target",
                "disposable_page_protected_child", "disposable_page_target_second",
                "disposable_page_subtree_child", "budget_section", "budget_page_1",
                "budget_page_2", "budget_page_3", "budget_page_4", "budget_page_5",
                "budget_page_6",
            ),
            {"create_section_group", "create_section", "create_page", "reorder_page"},
            checks=(
                "disposable_group is a descendant of delete_sandbox",
                "disposable_group contains a persisted sentinel Section",
                "Page, Section, and SectionGroup batch Delete target IDs are manifest-allowlisted",
                "Notebook total Page count exceeds the test Batch effective Page limit",
                "mixed include_subpages Page batch protects one child and deletes one subtree",
                "six-Page Section scope is rejected before mutation",
            ),
        ),
        DELETE_SCENARIO_POLICY,
        frozenset(
            DELETE_TOOLS
            | {"create_section_group", "create_section", "create_page", "reorder_page"}
        ),
        batch_mutation_budget={"max_effective_pages": 5},
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
                "cross_section_position_anchor",
                "cross_notebook_section",
                "cross_notebook_anchor",
                "cross_notebook_position_anchor",
            ),
            {
                "create_section",
                "get_page_text",
                "get_page_content_object_binary",
            }
            | LAYERED_PAGE_FIXTURE_TOOLS,
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
                "the rich source Image binary is read by exact PageContentObject ID without persisting Base64",
            ),
        ),
        RICH_COPY_POLICY,
        frozenset(
            COPY_PAGE_TOOLS
            | {
                "create_section",
                "get_page_text",
                "get_page_content_object_binary",
            }
            | LAYERED_PAGE_FIXTURE_TOOLS
        ),
        {
            "page_text_projection": "default_rich_plain_and_bounded_v1",
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
                "same_notebook_anchor_a",
                "same_notebook_anchor_b",
                "cross_notebook_group",
                "cross_notebook_anchor_a",
                "cross_notebook_anchor_b",
            ),
            {"create_section_group", "create_section"} | LAYERED_PAGE_FIXTURE_TOOLS,
            content=("RichText", "Table", "Image", "Outline", "List", "Tag"),
            checks=(
                "strict parent uses canonical read-back verification",
                "semantic child uses List/Tag semantic read-back verification",
                "same-Notebook and cross-Notebook destination Groups are role-bound",
            ),
        ),
        RICH_COPY_POLICY,
        frozenset(
            COPY_TOOLS
            | {"create_section_group", "create_section", "get_page_text"}
            | LAYERED_PAGE_FIXTURE_TOOLS
        ),
        {
            "page_text_projection": "source_and_targets_default_rich_v1",
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
                "source:{00-Group-Anchor-A,99-Group-Anchor-B}/Fixture-Sentinel",
                "destination:Cross-Notebook-Anchor",
                "destination:{00-Group-Anchor-A,99-Group-Anchor-B}/Fixture-Sentinel",
            ),
            (
                "group_a",
                "source_section",
                "parent_page",
                "semantic_page",
                "same_notebook_anchor_a",
                "same_notebook_anchor_a_sentinel",
                "same_notebook_anchor_b",
                "same_notebook_anchor_b_sentinel",
                "cross_notebook_anchor_section",
                "cross_notebook_anchor_group_a",
                "cross_notebook_anchor_group_a_sentinel",
                "cross_notebook_anchor_group_b",
                "cross_notebook_anchor_group_b_sentinel",
            ),
            {"create_section_group", "create_section"} | LAYERED_PAGE_FIXTURE_TOOLS,
            content=("RichText", "Table", "Image", "Outline", "List", "Tag"),
            checks=(
                "strict parent uses canonical read-back verification",
                "semantic child uses List/Tag semantic read-back verification",
                "same-Notebook and cross-Notebook destination roots are role-bound",
                "each otherwise-empty destination SectionGroup is persisted by one typed sentinel Section",
            ),
        ),
        RICH_COPY_POLICY,
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
        RICH_COPY_NOTEBOOK_POLICY,
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
                "destination:Destination/{00-Destination-Anchor-A,99-Destination-Anchor-B}",
            ),
            (
                "source_section",
                "root_only_page",
                "root_only_child",
                "subtree_page",
                "subtree_child",
                "destination_section",
                "destination_anchor_a",
                "destination_anchor_b",
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
                "destination:Notebook root/{00-Destination-Anchor-A,99-Destination-Anchor-B}",
            ),
            (
                "source_section",
                "source_page",
                "destination_anchor_a",
                "destination_anchor_b",
            ),
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
                "destination:Notebook root/{00-Destination-Anchor-A,99-Destination-Anchor-B}",
            ),
            (
                "source_group",
                "source_section",
                "source_page",
                "destination_anchor_a",
                "destination_anchor_b",
            ),
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

_SEARCH_FIXTURE_TOOLS = {
    "create_section_group",
    "create_section",
    "create_page",
}
SCENARIO_SPECS["search-all-open-notebooks"] = ScenarioSpec(
    "search-all-open-notebooks",
    _profile(
        "cacheable-index-search-probes",
        (
            "source:Probe Group/{Probe Section 1/Probe Page A1,Probe Section 2/Probe Page A2}",
            "source:Notebook Root Section/Probe Page A3",
            "search-b:Probe Section B/{Probe Page B1,Budget Long Text Page B2}",
        ),
        (
            "probe_group",
            "probe_section_1",
            "probe_page_a1",
            "probe_section_2",
            "probe_page_a2",
            "root_section",
            "probe_page_a3",
            "probe_section_b",
            "probe_page_b1",
            "budget_page_b2",
        ),
        _SEARCH_FIXTURE_TOOLS,
        content=("32-character search probe", "candidate budget marker", "long-text marker"),
        checks=(
            "both role Notebooks are simultaneously active and distinct",
            "main probe topology yields exact root/notebook/group/section counts 4/3/2/1",
            "raw probes remain only in memory and disposable Page bodies",
        ),
    ),
    WRITE_POLICY,
    frozenset(READ_TOOLS | _SEARCH_FIXTURE_TOOLS | {"search_pages"}),
    {
        "cache_supported": True,
        "included_in_all": True,
        "scope_counts": {"root": 4, "notebook": 3, "section_group": 2, "section": 1},
        "pagination": {"page_size": 2, "consistency": "live_index"},
        "probe_persistence": "sha256_length_character_classes_and_ids_only",
        "fresh_index_activation_checkpoint": "close_false_reopen_exact_paths",
    },
    search_budget={
        "max_pages": 4,
        "max_page_chars": 2_048,
        "max_total_chars": 512,
        "max_seconds": 60,
        "snippet_chars": 200,
    },
)

_TYPED_QUERY_FIXTURE_TOOLS = {
    "create_section_group",
    "create_section",
    "create_page",
    "reorder_page",
}
_TYPED_QUERY_READ_TOOLS = {
    "health_check",
    "query_notebook",
    "query_section_group",
    "query_section",
    "query_page",
}
SCENARIO_SPECS["query"] = ScenarioSpec(
    "query",
    _profile(
        "cacheable-typed-query-scopes",
        (
            "source:{Outer,OuterSibling}",
            "source:Outer/{Inner,InnerSibling}",
            "source:Outer/Inner/{Deep,DeepSibling}",
            "source:Outer/Inner/Deep/{Parent/{Child,ChildSibling}(level 2),Sibling}",
            "source:{Root/RootPage,RootSibling/RootPageSibling}",
            "query-b:{BOuter,BOuterSibling}",
            "query-b:BOuter/{BInner,BInnerSibling}",
            "query-b:BOuter/BInner/{BDeep,BDeepSibling}",
            "query-b:BOuter/BInner/BDeep/BParent/{BChild,BChildSibling}(level 2)",
            "query-b:{BRoot/BRootPage,BRootSibling/BRootPageSibling}",
        ),
        (
            "query_outer_group", "query_outer_group_sibling",
            "query_inner_group", "query_inner_group_sibling",
            "query_deep_section", "query_deep_section_sibling",
            "query_root_section", "query_root_section_sibling",
            "query_parent_page", "query_child_page",
            "query_child_page_sibling", "query_sibling_page", "query_root_page",
            "query_root_page_sibling", "query_b_outer_group",
            "query_b_outer_group_sibling", "query_b_inner_group",
            "query_b_inner_group_sibling", "query_b_deep_section",
            "query_b_deep_section_sibling", "query_b_root_section",
            "query_b_root_section_sibling",
            "query_b_parent_page", "query_b_child_page",
            "query_b_child_page_sibling", "query_b_sibling_page",
            "query_b_root_page", "query_b_root_page_sibling",
        ),
        _TYPED_QUERY_FIXTURE_TOOLS,
        content=("hierarchy metadata only", "Page indentation"),
        checks=(
            "two open role Notebooks have unique IDs and paths",
            "each typed hierarchy scope returns at least two fixture-owned Query items",
            "Page Section and two direct indentation children are proven",
        ),
    ),
    WRITE_POLICY,
    frozenset(_TYPED_QUERY_READ_TOOLS | _TYPED_QUERY_FIXTURE_TOOLS),
    {
        "cache_supported": True,
        "included_in_all": True,
        "query_kind": "hierarchy_metadata",
        "pagination": {"page_size": 2, "consistency": "live_hierarchy"},
        "lifecycle_close_probe_role": "query-b",
    },
)

_HIERARCHY_NAVIGATION_FIXTURE_TOOLS = {
    "create_section_group",
    "create_section",
    "create_page",
    "reorder_page",
}
SCENARIO_SPECS["hierarchy-navigation"] = ScenarioSpec(
    "hierarchy-navigation",
    _profile(
        "cacheable-hierarchy-navigation",
        (
            "Navigation-Root-Section",
            "Navigation-Group/{Navigation-Group-Section,Navigation-Inner-Group/Navigation-Target-Section}",
            (
                "Navigation-Target-Section/Navigation-Parent/"
                "{Navigation-Child/Navigation-Grandchild,Navigation-Child-Sibling}"
            ),
            "Navigation-Target-Section/Navigation-Root-Sibling",
            "second open Notebook role",
        ),
        (
            "navigation_root_section",
            "navigation_group",
            "navigation_inner_group",
            "navigation_section",
            "navigation_section_sibling",
            "navigation_parent_page",
            "navigation_child_page",
            "navigation_grandchild_page",
            "navigation_child_page_sibling",
            "navigation_root_page_sibling",
        ),
        _HIERARCHY_NAVIGATION_FIXTURE_TOOLS,
        content=("nested container ancestry", "Page indentation levels 1/2/3"),
        checks=(
            "list_notebooks returns both open fixture Notebook roles with unique IDs",
            "typed Expand tools preserve boundaries, order, uniqueness, and one shared tree schema",
            "expand_hierarchy projects parent_page_id/page_level as a branched Page tree",
            "expand_hierarchy accepts all four hierarchy root types",
            "expand_hierarchy max_depth truncates below direct Page children",
            "scenario browsing audit contains hierarchy metadata reads only",
        ),
    ),
    WRITE_POLICY,
    frozenset(
        _HIERARCHY_NAVIGATION_FIXTURE_TOOLS
        | {
            "health_check",
            "list_notebooks",
            "expand_notebook",
            "expand_section_group",
            "expand_section",
            "expand_page",
            "expand_hierarchy",
        }
    ),
    {
        "cache_supported": True,
        "included_in_all": False,
        "notebook_roles": ["source", "browse-b"],
        "page_parent_semantics": {"expand_hierarchy": "derived_indentation"},
    },
)

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
    _copy_tools = READ_TOOLS | {"copy_page"}
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
        {"create_section", "create_page", "append_page_content", "add_page_image_from_file"},
        content=("Outline", "RichText", "Table", "Image", "DisplayEquation"),
        checks=(
            "exact Source Parent IDs remain active",
            "prepared rich text, table, and image remain present",
            "exactly one complete standalone display-block MathML equation is observed",
            "content-free MathML OE placement evidence is captured",
        ),
    ),
    RICH_COPY_POLICY,
    frozenset(
        COPY_PAGE_TOOLS
        | {"create_section", "create_page", "append_page_content", "add_page_image_from_file"}
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
        {"create_section", "create_page", "append_page_content", "add_page_image_from_file"},
        content=("Outline", "RichText", "Table", "Image", "InlineEquation"),
        checks=(
            "exact Source Parent IDs remain active",
            "prepared rich text, table, and image remain present",
            "exactly one complete MathML equation remains inline with visible surrounding text",
            "the inline equation has no display attribute and no standalone formula line",
        ),
    ),
    RICH_WRITE_POLICY,
    frozenset(_INTERACTIVE_TOOLS | {"append_page_content", "add_page_image_from_file"}),
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
    frozenset(READ_TOOLS | {"copy_page"}),
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
