"""Fixture recipe owned by the typed Section reparent scenario."""

from __future__ import annotations

from ...runtime import InvariantFailure
from ...test_utils import display_name
from ..common.fixture_builders import ensure_group, ensure_page, ensure_section
from ..common.fixture_models import FixtureBuildResult, FixtureContext, FixtureValidationContext, resolve_active_structure
from .recipe_base import RecipeBase


DESCRIPTION_TITLE = "00-Reparent-Section-Description"
DESCRIPTION = """Reparent Section 人工验收说明

本场景在同一个 disposable Notebook 中覆盖三种合法父级变化，并保持 Section ID 与 Page 后代。

场景一：Notebook 父级 → SectionGroup 父级
操作前：01-Notebook-To-Group-Section 位于 Notebook 根级
操作后：01-Notebook-To-Group-Section 位于 01-Destination-Group
默认恢复后：01-Notebook-To-Group-Section 回到 Notebook 根级

场景二：SectionGroup 父级 → Notebook 父级
操作前：02-Group-To-Notebook-Section 位于 02-Source-Group
操作后：02-Group-To-Notebook-Section 位于 Notebook 根级
默认恢复后：02-Group-To-Notebook-Section 回到 02-Source-Group

场景三：SectionGroup 父级 → SectionGroup 父级
操作前：03-Group-To-Group-Section 位于 03-Source-Group
操作后：03-Group-To-Group-Section 位于 03-Destination-Group
默认恢复后：03-Group-To-Group-Section 回到 03-Source-Group

三个目标 Section 各自包含同编号 Page。Reparent 前后必须保持 Section ID、Page ID、Page 顺序、缩进关系和正文不变。
"""


class ReparentSectionFixtureRecipe(RecipeBase):
    recipe_version = 2

    def __init__(self) -> None:
        super().__init__("reparent-section")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        r = context.recorder
        ds = r.record_structure("description_section", await ensure_section(context.client, context.notebook_id, "00-Description"))
        dp = r.record_structure("description_page", await ensure_page(context.client, ds["id"], DESCRIPTION_TITLE, f"{DESCRIPTION}\nFixture token: {context.token}"))
        text = str((await context.client.call_tool("get_page_text", {"page_id": dp["id"]}))["text"])
        if not all(marker in text for marker in ("场景一：Notebook 父级 → SectionGroup 父级", "场景二：SectionGroup 父级 → Notebook 父级", "场景三：SectionGroup 父级 → SectionGroup 父级", "三个目标 Section 各自包含同编号 Page")):
            raise InvariantFailure("Reparent Section Description is missing a parent transition marker.")
        d1 = r.record_structure("notebook_to_group_destination", await ensure_group(context.client, context.notebook_id, "01-Destination-Group"))
        for key, name in (
            ("notebook_to_group_anchor_a", "00-Section-Anchor-A"),
            ("notebook_to_group_anchor_b", "99-Section-Anchor-B"),
        ):
            r.record_structure(key, await ensure_section(context.client, d1["id"], name))
        s1 = r.record_structure("notebook_to_group_section", await ensure_section(context.client, context.notebook_id, "01-Notebook-To-Group-Section"))
        r.record_structure("notebook_to_group_page", await ensure_page(context.client, s1["id"], "01-Notebook-To-Group-Page", f"Reparent case 1 token: {context.token}"))
        g2 = r.record_structure("group_to_notebook_source", await ensure_group(context.client, context.notebook_id, "02-Source-Group"))
        for key, name in (
            ("group_to_notebook_anchor_a", "00-Notebook-Section-Anchor-A"),
            ("group_to_notebook_anchor_b", "99-Notebook-Section-Anchor-B"),
        ):
            r.record_structure(key, await ensure_section(context.client, context.notebook_id, name))
        s2 = r.record_structure("group_to_notebook_section", await ensure_section(context.client, g2["id"], "02-Group-To-Notebook-Section"))
        r.record_structure("group_to_notebook_page", await ensure_page(context.client, s2["id"], "02-Group-To-Notebook-Page", f"Reparent case 2 token: {context.token}"))
        g3s = r.record_structure("group_to_group_source", await ensure_group(context.client, context.notebook_id, "03-Source-Group"))
        d3 = r.record_structure("group_to_group_destination", await ensure_group(context.client, context.notebook_id, "03-Destination-Group"))
        for key, name in (
            ("group_to_group_anchor_a", "00-Section-Anchor-A"),
            ("group_to_group_anchor_b", "99-Section-Anchor-B"),
        ):
            r.record_structure(key, await ensure_section(context.client, d3["id"], name))
        s3 = r.record_structure("group_to_group_section", await ensure_section(context.client, g3s["id"], "03-Group-To-Group-Section"))
        r.record_structure("group_to_group_page", await ensure_page(context.client, s3["id"], "03-Group-To-Group-Page", f"Reparent case 3 token: {context.token}"))
        return FixtureBuildResult(r.structure, r.evidence)

    def validate(self, context: FixtureValidationContext, build: FixtureBuildResult) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        notebook_id = str(resolved["description_section"].get("parent_id", ""))
        checks.require(resolved["description_page"].get("section_id") == resolved["description_section"]["id"], "Reparent Section Description escaped the fixture Notebook.", "Description Page and Section belong to the fixture Notebook")
        checks.require(resolved["notebook_to_group_section"].get("parent_id") == notebook_id and resolved["notebook_to_group_destination"].get("parent_id") == notebook_id, "Notebook-to-SectionGroup fixture relationship is invalid.", "case 1 source is Notebook-root and destination is a root SectionGroup")
        checks.require(resolved["group_to_notebook_source"].get("parent_id") == notebook_id and resolved["group_to_notebook_section"].get("parent_id") == resolved["group_to_notebook_source"]["id"], "SectionGroup-to-Notebook fixture relationship is invalid.", "case 2 source is under its root SectionGroup and destination is Notebook")
        checks.require(resolved["group_to_group_source"].get("parent_id") == notebook_id and resolved["group_to_group_destination"].get("parent_id") == notebook_id and resolved["group_to_group_source"]["id"] != resolved["group_to_group_destination"]["id"] and resolved["group_to_group_section"].get("parent_id") == resolved["group_to_group_source"]["id"], "SectionGroup-to-SectionGroup fixture relationship is invalid.", "case 3 source and destination are distinct root SectionGroups")
        anchor_groups = (
            ("notebook_to_group_destination", "notebook_to_group_anchor_a", "notebook_to_group_anchor_b"),
            (None, "group_to_notebook_anchor_a", "group_to_notebook_anchor_b"),
            ("group_to_group_destination", "group_to_group_anchor_a", "group_to_group_anchor_b"),
        )
        checks.require(
            all(
                resolved[anchor_a].get("parent_id")
                == (resolved[parent_key]["id"] if parent_key else notebook_id)
                and resolved[anchor_b].get("parent_id")
                == (resolved[parent_key]["id"] if parent_key else notebook_id)
                and resolved[anchor_a]["id"] != resolved[anchor_b]["id"]
                for parent_key, anchor_a, anchor_b in anchor_groups
            ),
            "A Reparent Section destination is missing its two Section anchors.",
            "all three destinations contain two distinct Section anchors",
        )
        expected = {"description_section":"00-Description", "description_page":DESCRIPTION_TITLE, "notebook_to_group_destination":"01-Destination-Group", "notebook_to_group_section":"01-Notebook-To-Group-Section", "notebook_to_group_page":"01-Notebook-To-Group-Page", "group_to_notebook_source":"02-Source-Group", "group_to_notebook_section":"02-Group-To-Notebook-Section", "group_to_notebook_page":"02-Group-To-Notebook-Page", "group_to_group_source":"03-Source-Group", "group_to_group_destination":"03-Destination-Group", "group_to_group_section":"03-Group-To-Group-Section", "group_to_group_page":"03-Group-To-Group-Page"}
        checks.require(all(display_name(resolved[key]) == name for key, name in expected.items()), "Reparent fixture Groups/Sections/Pages do not have stable numbering.", "all three reparent cases use stable 00/01/02/03 numbering")
        for section_key, page_key in (("notebook_to_group_section", "notebook_to_group_page"), ("group_to_notebook_section", "group_to_notebook_page"), ("group_to_group_section", "group_to_group_page")):
            checks.require(resolved[page_key].get("section_id") == resolved[section_key]["id"], "Reparent fixture Page escaped its declared Section.", f"{section_key} contains its numbered Page")
        checks.require(len({resolved[key]["id"] for key in ("notebook_to_group_section", "group_to_notebook_section", "group_to_group_section")}) == 3, "Reparent fixture must use three distinct target Sections.", "all three reparent cases use distinct target Section IDs")
        checks.checks.append("Description Page states before/after/restore for Notebook-to-SectionGroup, SectionGroup-to-Notebook, and SectionGroup-to-SectionGroup reparents")
        return tuple(checks.checks)


RECIPE = ReparentSectionFixtureRecipe()

__all__ = ["DESCRIPTION", "DESCRIPTION_TITLE", "RECIPE", "ReparentSectionFixtureRecipe"]
