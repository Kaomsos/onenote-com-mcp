"""Fixture recipe owned by the typed SectionGroup reparent scenario."""

from __future__ import annotations

from ...runtime import InvariantFailure
from ...test_utils import display_name
from ..common.fixture_builders import ensure_group, ensure_page, ensure_section
from ..common.fixture_models import FixtureBuildResult, FixtureContext, FixtureValidationContext, resolve_active_structure
from .recipe_base import RecipeBase


DESCRIPTION_TITLE = "00-Reparent-SectionGroup-Description"
DESCRIPTION = """Reparent SectionGroup 人工验收说明

本场景探索同一个 disposable Notebook 内三种保持 Group ID 的父级变化。

场景一：Notebook 父级 → SectionGroup 父级
操作前：01-Notebook-To-Group-Target 位于 Notebook 根级
操作后：01-Notebook-To-Group-Target 位于 01-Destination-Parent
默认恢复后：01-Notebook-To-Group-Target 回到 Notebook 根级

场景二：SectionGroup 父级 → Notebook 父级
操作前：02-Group-To-Notebook-Target 位于 02-Source-Parent
操作后：02-Group-To-Notebook-Target 位于 Notebook 根级
默认恢复后：02-Group-To-Notebook-Target 回到 02-Source-Parent

场景三：SectionGroup 父级 → SectionGroup 父级
操作前：03-Group-To-Group-Target 位于 03-Source-Parent
操作后：03-Group-To-Group-Target 位于 03-Destination-Parent
默认恢复后：03-Group-To-Group-Target 回到 03-Source-Parent

三个目标 Group 各自包含同编号 Section 和 Page。前后必须保持全树 ID、后代关系、Page 内容 hash 和内容对象 ID 不变。
两个作为恢复目标的源 SectionGroup 各自保留两个固定 source anchor，目标搬出后源容器仍非空。
"""


class ReparentSectionGroupFixtureRecipe(RecipeBase):
    recipe_version = 3

    def __init__(self) -> None:
        super().__init__("reparent-section-group")

    async def _descendants(self, context: FixtureContext, prefix: str, target: dict, number: str) -> None:
        r = context.recorder
        section = r.record_structure(f"{prefix}_section", await ensure_section(context.client, target["id"], f"{number}-Descendant-Section"))
        r.record_structure(f"{prefix}_page", await ensure_page(context.client, section["id"], f"{number}-Descendant-Page", f"SectionGroup reparent {prefix} token: {context.token}"))

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        r = context.recorder
        ds = r.record_structure("description_section", await ensure_section(context.client, context.notebook_id, "00-Description"))
        dp = r.record_structure("description_page", await ensure_page(context.client, ds["id"], DESCRIPTION_TITLE, f"{DESCRIPTION}\nFixture token: {context.token}"))
        text = str((await context.client.call_tool("get_page_text", {"page_id": dp["id"], "mode": "plain"}))["text"])
        if not all(marker in text for marker in ("场景一：Notebook 父级 → SectionGroup 父级", "场景二：SectionGroup 父级 → Notebook 父级", "场景三：SectionGroup 父级 → SectionGroup 父级", "三个目标 Group 各自包含同编号 Section 和 Page", "源 SectionGroup 各自保留两个固定 source anchor")):
            raise InvariantFailure("Reparent SectionGroup Description is missing a transition marker.")
        d1 = r.record_structure("notebook_to_group_destination", await ensure_group(context.client, context.notebook_id, "01-Destination-Parent"))
        for key, name in (
            ("notebook_to_group_anchor_a", "00-Group-Anchor-A"),
            ("notebook_to_group_anchor_b", "99-Group-Anchor-B"),
        ):
            r.record_structure(key, await ensure_group(context.client, d1["id"], name))
        t1 = r.record_structure("notebook_to_group_target", await ensure_group(context.client, context.notebook_id, "01-Notebook-To-Group-Target"))
        await self._descendants(context, "notebook_to_group", t1, "01")
        s2 = r.record_structure("group_to_notebook_source", await ensure_group(context.client, context.notebook_id, "02-Source-Parent"))
        for key, name in (
            ("group_to_notebook_source_anchor_a", "00-Source-Anchor-A"),
            ("group_to_notebook_source_anchor_b", "99-Source-Anchor-B"),
        ):
            r.record_structure(key, await ensure_group(context.client, s2["id"], name))
        for key, name in (
            ("group_to_notebook_anchor_a", "00-Notebook-Group-Anchor-A"),
            ("group_to_notebook_anchor_b", "99-Notebook-Group-Anchor-B"),
        ):
            r.record_structure(key, await ensure_group(context.client, context.notebook_id, name))
        t2 = r.record_structure("group_to_notebook_target", await ensure_group(context.client, s2["id"], "02-Group-To-Notebook-Target"))
        await self._descendants(context, "group_to_notebook", t2, "02")
        s3 = r.record_structure("group_to_group_source", await ensure_group(context.client, context.notebook_id, "03-Source-Parent"))
        for key, name in (
            ("group_to_group_source_anchor_a", "00-Source-Anchor-A"),
            ("group_to_group_source_anchor_b", "99-Source-Anchor-B"),
        ):
            r.record_structure(key, await ensure_group(context.client, s3["id"], name))
        d3 = r.record_structure("group_to_group_destination", await ensure_group(context.client, context.notebook_id, "03-Destination-Parent"))
        for key, name in (
            ("group_to_group_anchor_a", "00-Group-Anchor-A"),
            ("group_to_group_anchor_b", "99-Group-Anchor-B"),
        ):
            r.record_structure(key, await ensure_group(context.client, d3["id"], name))
        t3 = r.record_structure("group_to_group_target", await ensure_group(context.client, s3["id"], "03-Group-To-Group-Target"))
        await self._descendants(context, "group_to_group", t3, "03")
        return FixtureBuildResult(r.structure, r.evidence)

    def validate(self, context: FixtureValidationContext, build: FixtureBuildResult) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        notebook_id = str(resolved["description_section"].get("parent_id", ""))
        checks.require(resolved["description_page"].get("section_id") == resolved["description_section"]["id"] and bool(notebook_id), "Reparent SectionGroup Description escaped the fixture Notebook.", "Description Page and Section belong to the fixture Notebook")
        checks.require(resolved["notebook_to_group_target"].get("parent_id") == notebook_id and resolved["notebook_to_group_destination"].get("parent_id") == notebook_id, "Notebook-to-SectionGroup reparent fixture relationship is invalid.", "case 1 target is Notebook-root and destination is a root SectionGroup")
        checks.require(resolved["group_to_notebook_source"].get("parent_id") == notebook_id and resolved["group_to_notebook_target"].get("parent_id") == resolved["group_to_notebook_source"]["id"], "SectionGroup-to-Notebook reparent fixture relationship is invalid.", "case 2 target is under a root SectionGroup and destination is Notebook")
        checks.require(resolved["group_to_group_source"].get("parent_id") == notebook_id and resolved["group_to_group_destination"].get("parent_id") == notebook_id and resolved["group_to_group_source"]["id"] != resolved["group_to_group_destination"]["id"] and resolved["group_to_group_target"].get("parent_id") == resolved["group_to_group_source"]["id"], "SectionGroup-to-SectionGroup reparent fixture relationship is invalid.", "case 3 source and destination are distinct root SectionGroups")
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
            "A Reparent SectionGroup destination is missing its two Group anchors.",
            "all three destinations contain two distinct SectionGroup anchors",
        )
        source_anchor_groups = (
            (
                "group_to_notebook_source",
                "group_to_notebook_source_anchor_a",
                "group_to_notebook_source_anchor_b",
            ),
            (
                "group_to_group_source",
                "group_to_group_source_anchor_a",
                "group_to_group_source_anchor_b",
            ),
        )
        checks.require(
            all(
                resolved[anchor_a].get("parent_id") == resolved[parent_key]["id"]
                and resolved[anchor_b].get("parent_id") == resolved[parent_key]["id"]
                and resolved[anchor_a]["id"] != resolved[anchor_b]["id"]
                for parent_key, anchor_a, anchor_b in source_anchor_groups
            ),
            "A SectionGroup reparent source is missing its two stability anchors.",
            "both restorable source SectionGroups retain two distinct stability anchors",
        )
        expected = {"description_section":"00-Description", "description_page":DESCRIPTION_TITLE, "notebook_to_group_destination":"01-Destination-Parent", "notebook_to_group_target":"01-Notebook-To-Group-Target", "notebook_to_group_section":"01-Descendant-Section", "notebook_to_group_page":"01-Descendant-Page", "group_to_notebook_source":"02-Source-Parent", "group_to_notebook_source_anchor_a":"00-Source-Anchor-A", "group_to_notebook_source_anchor_b":"99-Source-Anchor-B", "group_to_notebook_target":"02-Group-To-Notebook-Target", "group_to_notebook_section":"02-Descendant-Section", "group_to_notebook_page":"02-Descendant-Page", "group_to_group_source":"03-Source-Parent", "group_to_group_source_anchor_a":"00-Source-Anchor-A", "group_to_group_source_anchor_b":"99-Source-Anchor-B", "group_to_group_destination":"03-Destination-Parent", "group_to_group_target":"03-Group-To-Group-Target", "group_to_group_section":"03-Descendant-Section", "group_to_group_page":"03-Descendant-Page"}
        checks.require(all(display_name(resolved[key]) == name for key, name in expected.items()), "SectionGroup reparent fixture does not have stable numbering.", "all three reparent cases and descendants use stable numbering")
        for prefix in ("notebook_to_group", "group_to_notebook", "group_to_group"):
            target, section, page = (resolved[f"{prefix}_{suffix}"] for suffix in ("target", "section", "page"))
            checks.require(section.get("parent_id") == target["id"] and page.get("section_id") == section["id"], "SectionGroup reparent descendants escaped a target Group.", f"{prefix} target contains its numbered Section and Page descendants")
        checks.require(len({resolved[f"{prefix}_target"]["id"] for prefix in ("notebook_to_group", "group_to_notebook", "group_to_group")}) == 3, "SectionGroup reparent fixture must use three distinct targets.", "all three reparent cases use distinct target Group IDs")
        checks.checks.append("Description Page states before/after/restore for Notebook-to-SectionGroup, SectionGroup-to-Notebook, and SectionGroup-to-SectionGroup typed reparents")
        return tuple(checks.checks)


RECIPE = ReparentSectionGroupFixtureRecipe()

__all__ = ["DESCRIPTION", "DESCRIPTION_TITLE", "RECIPE", "ReparentSectionGroupFixtureRecipe"]
