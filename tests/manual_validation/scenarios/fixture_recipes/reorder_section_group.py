"""Fixture recipe owned by the SectionGroup reorder capability probe."""

from __future__ import annotations

from ...runtime import InvariantFailure
from ...test_utils import display_name
from ..common.fixture_builders import ensure_group, ensure_page, ensure_section
from ..common.fixture_models import FixtureBuildResult, FixtureContext, FixtureValidationContext, resolve_active_structure
from .recipe_base import RecipeBase


DESCRIPTION_TITLE = "00-Reorder-SectionGroup-Description"
DESCRIPTION = """Reorder SectionGroup 人工验收说明

本场景同时覆盖 SectionGroup 的两种合法父级。

场景一：父级为 Notebook
操作前：00-Group-Parent, 01-Root-Group-A, 02-Root-Group-B, 03-Root-Group-C
操作后：00-Group-Parent, 01-Root-Group-A, 03-Root-Group-C, 02-Root-Group-B
恢复后：00-Group-Parent, 01-Root-Group-A, 02-Root-Group-B, 03-Root-Group-C

场景二：父级为 00-Group-Parent（SectionGroup）
操作前：01-Nested-Group-A, 02-Nested-Group-B, 03-Nested-Group-C
操作后：01-Nested-Group-A, 03-Nested-Group-C, 02-Nested-Group-B
恢复后：01-Nested-Group-A, 02-Nested-Group-B, 03-Nested-Group-C

两种情况都只改变同父级 SectionGroup 顺序，不改变 parent_id、Group ID 或 Section/Page 后代。
"""


class ReorderSectionGroupFixtureRecipe(RecipeBase):
    def __init__(self) -> None:
        super().__init__("reorder-section-group")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        r = context.recorder
        ds = r.record_structure("description_section", await ensure_section(context.client, context.notebook_id, "00-Description"))
        dp = r.record_structure("description_page", await ensure_page(context.client, ds["id"], DESCRIPTION_TITLE, f"{DESCRIPTION}\nFixture token: {context.token}"))
        text = str((await context.client.call_tool("get_page_text", {"page_id": dp["id"], "mode": "plain"}))["text"])
        markers = ("场景一：父级为 Notebook", "场景二：父级为 00-Group-Parent（SectionGroup）", "操作后：00-Group-Parent, 01-Root-Group-A, 03-Root-Group-C, 02-Root-Group-B", "操作后：01-Nested-Group-A, 03-Nested-Group-C, 02-Nested-Group-B")
        if not all(marker in text for marker in markers):
            raise InvariantFailure("Reorder SectionGroup Description is missing a parent or order marker.")
        parent = r.record_structure("section_group_parent", await ensure_group(context.client, context.notebook_id, "00-Group-Parent"))
        for index, letter in enumerate("ABC", start=1):
            key = letter.casefold()
            root_group = r.record_structure(f"root_group_{key}", await ensure_group(context.client, context.notebook_id, f"{index:02d}-Root-Group-{letter}"))
            root_section = r.record_structure(f"root_section_{key}", await ensure_section(context.client, root_group["id"], f"{index:02d}-Root-Section-{letter}"))
            r.record_structure(f"root_page_{key}", await ensure_page(context.client, root_section["id"], f"{index:02d}-Root-Page-{letter}", f"Root SectionGroup token: {context.token}-{letter}"))
            nested_group = r.record_structure(f"nested_group_{key}", await ensure_group(context.client, parent["id"], f"{index:02d}-Nested-Group-{letter}"))
            nested_section = r.record_structure(f"nested_section_{key}", await ensure_section(context.client, nested_group["id"], f"{index:02d}-Nested-Section-{letter}"))
            r.record_structure(f"nested_page_{key}", await ensure_page(context.client, nested_section["id"], f"{index:02d}-Nested-Page-{letter}", f"Nested SectionGroup token: {context.token}-{letter}"))
        return FixtureBuildResult(r.structure, r.evidence)

    def validate(self, context: FixtureValidationContext, build: FixtureBuildResult) -> tuple[str, ...]:
        resolved, by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        roots = [resolved[f"root_group_{letter}"] for letter in "abc"]
        nested = [resolved[f"nested_group_{letter}"] for letter in "abc"]
        parent = resolved["section_group_parent"]
        notebook_id = roots[0]["parent_id"]
        checks.require(by_id.get(str(notebook_id), {}).get("resource_type") == "notebook" and all(group.get("parent_id") == notebook_id for group in roots) and parent.get("parent_id") == notebook_id and parent.get("resource_type") == "section_group" and all(group.get("parent_id") == parent["id"] for group in nested), "SectionGroup reorder fixture does not cover Notebook and SectionGroup parents.", "SectionGroup fixture covers both legal parent types: Notebook and SectionGroup")
        checks.require(resolved["description_section"].get("parent_id") == notebook_id and resolved["description_page"].get("section_id") == resolved["description_section"]["id"], "Reorder SectionGroup Description escaped the fixture Notebook.", "Description Page and Section belong to the fixture Notebook")
        expected = {"description_section": "00-Description", "description_page": DESCRIPTION_TITLE, "section_group_parent": "00-Group-Parent"}
        for prefix, label in (("root", "Root"), ("nested", "Nested")):
            for index, letter in enumerate("abc", start=1):
                expected[f"{prefix}_group_{letter}"] = f"{index:02d}-{label}-Group-{letter.upper()}"
                expected[f"{prefix}_section_{letter}"] = f"{index:02d}-{label}-Section-{letter.upper()}"
                expected[f"{prefix}_page_{letter}"] = f"{index:02d}-{label}-Page-{letter.upper()}"
        checks.require(all(display_name(resolved[key]) == name for key, name in expected.items()), "SectionGroup reorder fixture Groups/Sections/Pages do not have stable numbering.", "both SectionGroup sequences and descendants use stable 01/02/03 numbering")
        items = context.snapshot["items"]
        direct_roots = [item["id"] for item in items if item.get("resource_type") == "section_group" and item.get("parent_id") == notebook_id]
        direct_nested = [item["id"] for item in items if item.get("resource_type") == "section_group" and item.get("parent_id") == parent["id"]]
        expected_roots = [parent["id"]] + [group["id"] for group in roots]
        checks.require([value for value in direct_roots if value in set(expected_roots)] == expected_roots and direct_nested == [group["id"] for group in nested], "SectionGroup reorder fixture is not in exact A/B/C order for both parents.", "both SectionGroup sibling sequences are exactly A/B/C")
        for prefix, groups in (("root", roots), ("nested", nested)):
            for letter, group in zip("abc", groups):
                section = resolved[f"{prefix}_section_{letter}"]
                page = resolved[f"{prefix}_page_{letter}"]
                checks.require(section.get("parent_id") == group["id"] and page.get("section_id") == section["id"], "SectionGroup reorder fixture descendant escaped its declared Group.", f"{prefix} Group {letter.upper()} contains its declared Section/Page descendants")
        checks.checks.append("Description Page states 01,02,03 before; 01,03,02 after; 01,02,03 restored for Notebook and SectionGroup parents")
        return tuple(checks.checks)


RECIPE = ReorderSectionGroupFixtureRecipe()

__all__ = ["DESCRIPTION", "DESCRIPTION_TITLE", "RECIPE", "ReorderSectionGroupFixtureRecipe"]
