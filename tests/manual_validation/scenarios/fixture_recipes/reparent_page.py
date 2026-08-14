"""Fixture recipe owned by the typed Page reparent scenario."""

from __future__ import annotations

from ...runtime import InvariantFailure
from ...test_utils import display_name
from ..common.fixture_builders import ensure_copy_list_tag_fixture, ensure_page, ensure_reparent_page_rich_fixture, ensure_section
from ..common.fixture_models import FixtureBuildResult, FixtureContext, FixtureValidationContext, resolve_active_structure
from .recipe_base import RecipeBase, evidence


DESCRIPTION_TITLE = "00-Reparent-Page-Description"
DESCRIPTION = """Reparent Page 人工验收说明

本场景验证同一 Notebook 内使用原生 UpdateHierarchy 更换 Page 所属 Section。
OneNote 可以为 Reparent 后的 Page 及其内容对象重新分配 ID；场景会记录旧 ID → 新 ID，
但不会调用 Copy、DeleteHierarchy 或把旧 Page 显式移入回收站。

操作前：01-Source-Section/01-Reparent-Page
操作后：02-Destination-Section/01-Reparent-Page；02-Destination-Anchor 保持不变
默认恢复后：01-Source-Section/01-Reparent-Page

目标 Page 包含 Rich Text、Table、List、Tag 和 Image。验收允许目标 Page/内容对象 ID
一对一重映射，但要求标题、page_level、富内容语义摘要、可见文本、List/Tag 语义、
Image 二进制和内容对象类型不变；所有无关对象的 ID、关系和内容必须保持不变。
"""


class ReparentPageFixtureRecipe(RecipeBase):
    recipe_version = 3

    def __init__(self) -> None:
        super().__init__("reparent-page")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        r = context.recorder
        ds = r.record_structure("description_section", await ensure_section(context.client, context.notebook_id, "00-Description"))
        dp = r.record_structure("description_page", await ensure_page(context.client, ds["id"], DESCRIPTION_TITLE, f"{DESCRIPTION}\nFixture token: {context.token}"))
        text = str((await context.client.call_tool("get_page_text", {"page_id": dp["id"]}))["text"])
        markers = ("操作前：01-Source-Section/01-Reparent-Page", "操作后：02-Destination-Section/01-Reparent-Page", "默认恢复后：01-Source-Section/01-Reparent-Page", "Rich Text、Table、List、Tag 和 Image", "旧 ID → 新 ID")
        if not all(marker in text for marker in markers):
            raise InvariantFailure("Reparent Page Description is missing a state marker.")
        source = r.record_structure("source_section", await ensure_section(context.client, context.notebook_id, "01-Source-Section"))
        destination = r.record_structure("destination_section", await ensure_section(context.client, context.notebook_id, "02-Destination-Section"))
        target = r.record_structure("reparent_page", await ensure_page(context.client, source["id"], "01-Reparent-Page", f"Page reparent token: {context.token}"))
        target, rich = await ensure_reparent_page_rich_fixture(context.client, target, context.options.run_dir)
        target, list_tag = await ensure_copy_list_tag_fixture(context.client, target)
        rich["automated_content"] = ["rich_text", "table", "image", "list", "tag"]
        rich["list_tag"] = list_tag
        r.refresh_structure("reparent_page", target)
        r.record_evidence("reparent_page_fixture", rich)
        r.record_structure("destination_anchor_page", await ensure_page(context.client, destination["id"], "02-Destination-Anchor", f"Destination anchor token: {context.token}"))
        r.record_structure("destination_anchor_page_b", await ensure_page(context.client, destination["id"], "03-Destination-Anchor", f"Second destination anchor token: {context.token}"))
        return FixtureBuildResult(r.structure, r.evidence)

    def validate(self, context: FixtureValidationContext, build: FixtureBuildResult) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        source, destination = resolved["source_section"], resolved["destination_section"]
        target = resolved["reparent_page"]
        anchors = [resolved["destination_anchor_page"], resolved["destination_anchor_page_b"]]
        checks.require(source.get("parent_id") == destination.get("parent_id") and source.get("parent_id") and source["id"] != destination["id"], "Page reparent fixture Sections are not distinct children of one Notebook.", "source and destination Sections are distinct children of one Notebook")
        checks.require(resolved["description_section"].get("parent_id") == source.get("parent_id") and resolved["description_page"].get("section_id") == resolved["description_section"]["id"], "Reparent Page Description escaped the fixture Notebook.", "Description Page and Section belong to the fixture Notebook")
        checks.require(target.get("section_id") == source["id"] and target.get("parent_id") == source["id"] and int(target.get("page_level", 0)) == 1 and target.get("parent_page_id") in {None, ""}, "Page reparent target is not a root Page in the source Section.", "target Page is a root Page in the source Section")
        checks.require(all(anchor.get("section_id") == destination["id"] and anchor["id"] != target["id"] for anchor in anchors) and len({anchor["id"] for anchor in anchors}) == 2, "Page reparent destination anchors are invalid.", "destination contains two distinct Page anchors outside the reparented target")
        expected = {"description_section":"00-Description", "description_page":DESCRIPTION_TITLE, "source_section":"01-Source-Section", "destination_section":"02-Destination-Section", "reparent_page":"01-Reparent-Page", "destination_anchor_page":"02-Destination-Anchor", "destination_anchor_page_b":"03-Destination-Anchor"}
        checks.require(all(display_name(resolved[key]) == name for key, name in expected.items()), "Page reparent fixture does not have stable numbering.", "Description, Sections, target Page, and anchor use stable numbering")
        rich = evidence(build, "reparent_page_fixture")
        checks.require(isinstance(rich, dict) and rich.get("page_id") == target["id"], "Page reparent rich-content evidence is not bound to the target Page.", "target Page owns the declared rich-content fixture")
        automated = {str(value).casefold() for value in (rich or {}).get("automated_content", [])}
        checks.require({"rich_text", "table", "image"}.issubset(automated), "Rich Copy fixture is missing a required automated content capability.", "rich text, table, and image capabilities were created and observed")
        list_tag = (rich or {}).get("list_tag")
        checks.require(isinstance(list_tag, dict) and list_tag.get("page_id") == target["id"] and {"List", "Tag"}.issubset(list_tag.get("observed_capabilities", [])) and list_tag.get("observed_counts", {}).get("List") == 3 and list_tag.get("observed_counts", {}).get("Tag") == 3, "Reparent Page fixture is missing its generated List/Tag content.", "target Page contains three mixed List/Tag items alongside rich content")
        checks.checks.append("Description Page states ID-remapping and rich-content acceptance for the numbered Page reparent")
        return tuple(checks.checks)


RECIPE = ReparentPageFixtureRecipe()

__all__ = ["DESCRIPTION", "DESCRIPTION_TITLE", "RECIPE", "ReparentPageFixtureRecipe"]
