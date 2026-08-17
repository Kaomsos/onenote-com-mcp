"""Fixture recipe owned by the Section reorder scenario."""

from __future__ import annotations

from ...runtime import InvariantFailure
from ...test_utils import display_name
from ..common.fixture_builders import ensure_group, ensure_page, ensure_section
from ..common.fixture_models import FixtureBuildResult, FixtureContext, FixtureValidationContext, resolve_active_structure
from .recipe_base import RecipeBase


DESCRIPTION_TITLE = "00-Reorder-Section-Description"
DESCRIPTION = """Reorder Section 人工验收说明

本场景同时覆盖 Section 的两种合法父级。

场景一：父级为 Notebook
操作前：00-Description, 01-Root-Section-A, 02-Root-Section-B, 03-Root-Section-C
操作后：00-Description, 01-Root-Section-A, 03-Root-Section-C, 02-Root-Section-B
恢复后：00-Description, 01-Root-Section-A, 02-Root-Section-B, 03-Root-Section-C

场景二：父级为 01-Section-Parent（SectionGroup）
操作前：01-Group-Section-A, 02-Group-Section-B, 03-Group-Section-C
操作后：01-Group-Section-A, 03-Group-Section-C, 02-Group-Section-B
恢复后：01-Group-Section-A, 02-Group-Section-B, 03-Group-Section-C

两种情况都只改变同父级 Section 顺序，不改变 parent_id、Section ID 或 Page 后代。
"""


class ReorderSectionFixtureRecipe(RecipeBase):
    def __init__(self) -> None:
        super().__init__("reorder-section")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        r = context.recorder
        ds = r.record_structure("description_section", await ensure_section(context.client, context.notebook_id, "00-Description"))
        dp = r.record_structure("description_page", await ensure_page(context.client, ds["id"], DESCRIPTION_TITLE, f"{DESCRIPTION}\nFixture token: {context.token}"))
        text = str((await context.client.call_tool("get_page_text", {"page_id": dp["id"], "mode": "plain"}))["text"])
        markers = ("场景一：父级为 Notebook", "场景二：父级为 01-Section-Parent（SectionGroup）", "操作后：00-Description, 01-Root-Section-A, 03-Root-Section-C, 02-Root-Section-B", "操作后：01-Group-Section-A, 03-Group-Section-C, 02-Group-Section-B")
        if not all(marker in text for marker in markers):
            raise InvariantFailure("Reorder Section Description is missing a parent or order marker.")
        parent = r.record_structure("section_parent_group", await ensure_group(context.client, context.notebook_id, "01-Section-Parent"))
        for index, letter in enumerate("ABC", start=1):
            key = letter.casefold()
            root_section = r.record_structure(f"root_section_{key}", await ensure_section(context.client, context.notebook_id, f"{index:02d}-Root-Section-{letter}"))
            group_section = r.record_structure(f"group_section_{key}", await ensure_section(context.client, parent["id"], f"{index:02d}-Group-Section-{letter}"))
            r.record_structure(f"root_page_{key}", await ensure_page(context.client, root_section["id"], f"{index:02d}-Root-Page-{letter}", f"Root Section token: {context.token}-{letter}"))
            r.record_structure(f"group_page_{key}", await ensure_page(context.client, group_section["id"], f"{index:02d}-Group-Page-{letter}", f"Group Section token: {context.token}-{letter}"))
        return FixtureBuildResult(r.structure, r.evidence)

    def validate(self, context: FixtureValidationContext, build: FixtureBuildResult) -> tuple[str, ...]:
        resolved, by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        root_parent_id = resolved["root_section_a"].get("parent_id")
        parent_group = resolved["section_parent_group"]
        checks.require(resolved["description_page"].get("section_id") == resolved["description_section"]["id"] and resolved["description_section"].get("parent_id") == root_parent_id, "Reorder Section Description escaped the fixture Notebook.", "Description Page and Section belong to the fixture Notebook")
        checks.require(by_id.get(str(root_parent_id), {}).get("resource_type") == "notebook" and parent_group.get("resource_type") == "section_group" and parent_group.get("parent_id") == root_parent_id, "Section reorder fixture does not cover Notebook and SectionGroup parents.", "Section fixture covers both legal parent types: Notebook and SectionGroup")
        expected = {"description_section": "00-Description", "description_page": DESCRIPTION_TITLE, "section_parent_group": "01-Section-Parent"}
        for prefix, label in (("root", "Root"), ("group", "Group")):
            for index, letter in enumerate("abc", start=1):
                expected[f"{prefix}_section_{letter}"] = f"{index:02d}-{label}-Section-{letter.upper()}"
                expected[f"{prefix}_page_{letter}"] = f"{index:02d}-{label}-Page-{letter.upper()}"
        checks.require(all(display_name(resolved[key]) == name for key, name in expected.items()), "Section reorder fixture Sections/Pages do not have stable numbering.", "both Section sequences and their Pages use stable 01/02/03 numbering")
        for prefix, parent_id in (("root", root_parent_id), ("group", parent_group["id"])):
            sections = [resolved[f"{prefix}_section_{letter}"] for letter in "abc"]
            pages = [resolved[f"{prefix}_page_{letter}"] for letter in "abc"]
            checks.require(all(section.get("parent_id") == parent_id for section in sections), "Section reorder fixture has a Section outside its declared parent.", f"{prefix} A/B/C Sections share one legal parent")
            checks.require(all(page.get("section_id") == section["id"] for page, section in zip(pages, sections)), "Section reorder fixture Page escaped its declared Section.", f"{prefix} A/B/C Sections each contain their declared Page")
        items = context.snapshot["items"]
        direct_root = [item["id"] for item in items if item.get("resource_type") == "section" and item.get("parent_id") == root_parent_id]
        direct_group = [item["id"] for item in items if item.get("resource_type") == "section" and item.get("parent_id") == parent_group["id"]]
        expected_root = [resolved["description_section"]["id"]] + [resolved[f"root_section_{letter}"]["id"] for letter in "abc"]
        expected_group = [resolved[f"group_section_{letter}"]["id"] for letter in "abc"]
        checks.require([value for value in direct_root if value in set(expected_root)] == expected_root and [value for value in direct_group if value in set(expected_group)] == expected_group, "Section reorder fixture is not in exact A/B/C order.", "both Section sibling sequences are exactly A/B/C")
        checks.checks.append("Description Page states 01,02,03 before; 01,03,02 after; 01,02,03 restored for Notebook and SectionGroup parents")
        return tuple(checks.checks)


RECIPE = ReorderSectionFixtureRecipe()

__all__ = ["DESCRIPTION", "DESCRIPTION_TITLE", "RECIPE", "ReorderSectionFixtureRecipe"]
