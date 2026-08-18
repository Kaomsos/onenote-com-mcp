"""Fixture recipe owned by the Page reorder scenario."""

from __future__ import annotations

from ...runtime import InvariantFailure
from ...test_utils import display_name
from ..common.fixture_builders import enforce_page_position, ensure_page, ensure_section
from ..common.fixture_models import FixtureBuildResult, FixtureContext, FixtureValidationContext, resolve_active_structure
from .recipe_base import RecipeBase


DESCRIPTION_TITLE = "00-Reorder-Description"
DESCRIPTION = """Reorder Page 人工验收说明

目标分区：01-Reorder-Page-Section

操作前（顺序 01,02,03）：
01-Parent：page_level=1
  02-Child：page_level=2，缩进在 01-Parent 下
03-Sibling：page_level=1

子页范围验收（每个 case 后恢复）：
1. include_subpages=false：把 01-Parent 单独移到 03-Sibling 后；02-Child 必须留在原位置、提升为 level 1，ID 与内容不变。
2. include_subpages=true：把 01-Parent 与 02-Child 作为连续块移到 03-Sibling 后；块内顺序和相对层级不变。

正向 Reorder：
把 03-Sibling 移到 01-Parent 后，并设为 page_level=2。

预期操作后（顺序 01,03,02）：
01-Parent：page_level=1
  03-Sibling：page_level=2，缩进在 01-Parent 下
  02-Child：page_level=2，仍缩进在 01-Parent 下

默认恢复后（顺序 01,02,03）：
01-Parent：page_level=1
  02-Child：page_level=2，缩进在 01-Parent 下
03-Sibling：page_level=1
"""


class ReorderPageFixtureRecipe(RecipeBase):
    recipe_version = 2

    def __init__(self) -> None:
        super().__init__("reorder-page")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        r = context.recorder
        description_section = r.record_structure("description_section", await ensure_section(context.client, context.notebook_id, "Description"))
        description_page = r.record_structure("description_page", await ensure_page(context.client, description_section["id"], DESCRIPTION_TITLE, f"{DESCRIPTION}\nFixture token: {context.token}"))
        description_text = str((await context.client.call_tool("get_page_text", {"page_id": description_page["id"], "mode": "plain"}))["text"])
        if not all(marker in description_text for marker in ("操作前（顺序 01,02,03）", "include_subpages=false", "include_subpages=true", "预期操作后（顺序 01,03,02）", "默认恢复后（顺序 01,02,03）")):
            raise InvariantFailure("Reorder Page Description is missing a before/after/restore marker.")
        section = r.record_structure("reorder_section", await ensure_section(context.client, context.notebook_id, "01-Reorder-Page-Section"))
        parent = r.record_structure("parent_page", await ensure_page(context.client, section["id"], "01-Parent", f"01 Parent token: {context.token}"))
        child = r.record_structure("child_page", await ensure_page(context.client, section["id"], "02-Child", f"02 Child token: {context.token}"))
        sibling = r.record_structure("sibling_page", await ensure_page(context.client, section["id"], "03-Sibling", f"03 Sibling token: {context.token}"))
        r.refresh_structure("parent_page", await enforce_page_position(context.client, section["id"], parent["id"], "", 1))
        r.refresh_structure("child_page", await enforce_page_position(context.client, section["id"], child["id"], parent["id"], 2))
        r.refresh_structure("sibling_page", await enforce_page_position(context.client, section["id"], sibling["id"], child["id"], 1))
        return FixtureBuildResult(r.structure, r.evidence)

    def validate(self, context: FixtureValidationContext, build: FixtureBuildResult) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        parent, child, sibling = (resolved[key] for key in ("parent_page", "child_page", "sibling_page"))
        section = resolved["reorder_section"]
        checks.require(all(page.get("section_id") == section["id"] for page in (parent, child, sibling)), "Fixture Page tree is not contained by the declared source Section.", "Parent/Child/Sibling share the declared source Section")
        checks.require(int(parent.get("page_level", 0)) == 1 and int(child.get("page_level", 0)) == 2 and int(sibling.get("page_level", 0)) == 1 and child.get("parent_page_id") == parent["id"] and sibling.get("parent_page_id") in {None, ""}, "Fixture Parent/Child/Sibling Page topology is invalid.", "Page levels and derived parent relationships match the profile")
        checks.require(resolved["description_page"].get("section_id") == resolved["description_section"]["id"], "Reorder Page Description Page escaped its Description Section.", "Description Page belongs to the Description Section")
        expected = {"description_page": DESCRIPTION_TITLE, "parent_page": "01-Parent", "child_page": "02-Child", "sibling_page": "03-Sibling"}
        checks.require(all(display_name(resolved[key]) == title for key, title in expected.items()), "Reorder Page fixture Page titles do not have the required stable numbering.", "all scenario Pages use stable 00/01/02/03 title prefixes")
        checks.checks.append("Description Page covers include_subpages=false/true, 01,02,03 before; 01,03,02 after; 01,02,03 restored")
        return tuple(checks.checks)


RECIPE = ReorderPageFixtureRecipe()

__all__ = ["DESCRIPTION", "DESCRIPTION_TITLE", "RECIPE", "ReorderPageFixtureRecipe"]
