"""Fixture recipe for the two-case Page reparent scope scenario."""

from __future__ import annotations

from ...runtime import InvariantFailure
from ..common.fixture_builders import (
    enforce_page_position,
    ensure_page,
    ensure_reparent_page_rich_fixture,
    ensure_section,
)
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from .recipe_base import RecipeBase


DESCRIPTION_TITLE = "00-Reparent-Page-Scope-Description"
DESCRIPTION = """Reparent Page 范围人工验收说明

本场景只使用 disposable Notebook，验证两个相互独立的选择范围：
1. root-only-default：使用 page_scope=page_only，只迁移选中的 level-2 Page；其后代留在源
   Section 并整体提升一级。
2. full-subtree：使用 page_scope=indentation_subtree，迁移选中的 level-2 Page 及完整缩进子树；
   目标根归一化为 level 1，后代保持相对顺序和相对层级。

Fixture 只使用 OneNote Desktop 支持的 page level 1-3；两棵树的后代均位于 level 3。

两个 case 的 destination_position 都仅描述 fresh 目标根 Page 在目标 Section 完整扁平
Page 序列中的执行后位置，不包含 page_level，也不返回后代位置列表。
"""


class ReparentPageWithLevelFixtureRecipe(RecipeBase):
    recipe_version = 2

    def __init__(self) -> None:
        super().__init__("reparent-page-with-level")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        r = context.recorder
        description_section = r.record_structure(
            "description_section",
            await ensure_section(context.client, context.notebook_id, "00-Description"),
        )
        r.record_structure(
            "description_page",
            await ensure_page(
                context.client,
                description_section["id"],
                DESCRIPTION_TITLE,
                f"{DESCRIPTION}\nFixture token: {context.token}",
            ),
        )
        source = r.record_structure(
            "source_section",
            await ensure_section(context.client, context.notebook_id, "01-Source-Section"),
        )
        destination = r.record_structure(
            "destination_section",
            await ensure_section(context.client, context.notebook_id, "02-Destination-Section"),
        )

        async def page(key: str, title: str, content: str) -> dict:
            return r.record_structure(
                key,
                await ensure_page(context.client, source["id"], title, content),
            )

        root_parent = await page("root_only_parent", "01-Root-Only-Parent", context.token)
        root_selected = await page("root_only_selected", "02-Root-Only-Selected", context.token)
        root_child = await page("root_only_child", "03-Root-Only-Child", context.token)
        subtree_parent = await page("subtree_parent", "04-Subtree-Parent", context.token)
        subtree_selected = await page("subtree_selected", "05-Subtree-Selected", context.token)
        subtree_child_a = await page("subtree_child_a", "06-Subtree-Child-A", context.token)
        subtree_child_b = await page("subtree_child_b", "07-Subtree-Child-B", context.token)

        ordered = (
            (root_parent, "", 1),
            (root_selected, root_parent["id"], 2),
            (root_child, root_selected["id"], 3),
            (subtree_parent, root_child["id"], 1),
            (subtree_selected, subtree_parent["id"], 2),
            (subtree_child_a, subtree_selected["id"], 3),
            (subtree_child_b, subtree_child_a["id"], 3),
        )
        for page_item, after_id, level in ordered:
            refreshed = await enforce_page_position(
                context.client,
                source["id"],
                page_item["id"],
                after_id,
                level,
            )
            for key, value in list(r.structure.items()):
                if isinstance(value, dict) and value.get("id") == page_item["id"]:
                    r.refresh_structure(key, refreshed)

        scope_rich: dict[str, dict] = {}
        for key in ("root_only_selected", "subtree_selected"):
            selected = r.structure[key]
            selected, rich = await ensure_reparent_page_rich_fixture(
                context.client,
                selected,
                context.options.run_dir,
            )
            r.refresh_structure(key, selected)
            scope_rich[key] = rich
        r.record_evidence(
            "reparent_page_fixture",
            {
                "page_id": scope_rich["root_only_selected"]["page_id"],
                "automated_content": ["rich_text", "table", "image"],
                "scope_pages": scope_rich,
            },
        )

        r.record_structure(
            "destination_anchor_a",
            await ensure_page(
                context.client,
                destination["id"],
                "01-Destination-Anchor-A",
                context.token,
            ),
        )
        r.record_structure(
            "destination_anchor_b",
            await ensure_page(
                context.client,
                destination["id"],
                "02-Destination-Anchor-B",
                context.token,
            ),
        )
        return FixtureBuildResult(r.structure, r.evidence)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        source_id = resolved["source_section"]["id"]
        destination_id = resolved["destination_section"]["id"]
        expected = {
            "root_only_parent": (1, None),
            "root_only_selected": (2, resolved["root_only_parent"]["id"]),
            "root_only_child": (3, resolved["root_only_selected"]["id"]),
            "subtree_parent": (1, None),
            "subtree_selected": (2, resolved["subtree_parent"]["id"]),
            "subtree_child_a": (3, resolved["subtree_selected"]["id"]),
            "subtree_child_b": (3, resolved["subtree_selected"]["id"]),
        }
        for key, (level, parent_id) in expected.items():
            item = resolved[key]
            checks.require(
                item.get("section_id") == source_id
                and int(item.get("page_level", 0)) == level
                and item.get("parent_page_id") in {parent_id, "" if parent_id is None else parent_id},
                f"Page scope fixture topology is invalid for {key}.",
                f"{key} has the declared source indentation topology",
            )
        checks.require(
            all(
                resolved[key].get("section_id") == destination_id
                and int(resolved[key].get("page_level", 0)) == 1
                for key in ("destination_anchor_a", "destination_anchor_b")
            ),
            "Destination anchors are invalid.",
            "destination contains two root Page anchors",
        )
        scope_evidence = build.evidence.get("reparent_page_fixture", {})
        scope_pages = scope_evidence.get("scope_pages", {}) if isinstance(scope_evidence, dict) else {}
        for key in ("root_only_selected", "subtree_selected"):
            rich = scope_pages.get(key)
            checks.require(
                isinstance(rich, dict) and rich.get("page_id") == resolved[key]["id"],
                f"Rich fixture is not bound to {key}.",
                f"{key} owns stable rich-content evidence",
            )
        checks.require(
            resolved["source_section"].get("parent_id")
            == resolved["destination_section"].get("parent_id"),
            "Source and destination Sections escaped the same Notebook.",
            "source and destination are distinct Sections in one Notebook",
        )
        return tuple(checks.checks)


RECIPE = ReparentPageWithLevelFixtureRecipe()

__all__ = [
    "DESCRIPTION",
    "DESCRIPTION_TITLE",
    "RECIPE",
    "ReparentPageWithLevelFixtureRecipe",
]
