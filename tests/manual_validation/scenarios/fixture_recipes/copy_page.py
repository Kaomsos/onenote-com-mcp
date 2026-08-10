"""Fixture recipe owned by the two-scope Page Copy scenario."""

from __future__ import annotations

from ...runtime import InvariantFailure
from ...test_utils import display_name
from ..common.fixture_builders import ensure_page, ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from .layered_copy import LayeredCopyFixtureRecipe, LayeredFixtureConfig, LayeredFixtureKind


DESCRIPTION_TITLE = "00-Copy-Page-Description"
DESCRIPTION = """Copy Page 人工验收说明

本场景在同一个目标 Section 中依次验证两种 Page Copy 范围。

原始状态：
Source/01-Source-Parent：page_level=1，包含 Rich Text、Table 和 Image
  Source/02-Source-Child：page_level=2，缩进在 Parent 下，包含 List 和 Tag
Destination：为空

情况一——默认范围（省略 include_descendants）：
Destination/01-Root-Only-Copy-*：page_level=1，只复制 Parent
预期：该副本没有缩进子页；Source 中 Parent/Child 的 ID、层级和内容保持不变。

情况二——完整子树（include_descendants=true）：
Destination/02-Full-Subtree-Copy-*：page_level=1，复制 Parent
  Destination/02-Source-Child：page_level=2，缩进在完整子树副本下
预期：Parent 与 Child 均进入 id_map，父子关系、相对层级和内容保持不变。

使用 --keep-worksite 时，两种目标会同时保留以便在 OneNote UI 中对照验收；
默认运行会在自动 read-back 验证后清理两个目标，并恢复到上述原始状态。
"""

class CopyPageFixtureRecipe(LayeredCopyFixtureRecipe):
    def __init__(self) -> None:
        super().__init__(
            "copy-page",
            LayeredFixtureConfig(
                LayeredFixtureKind.PAGE,
                parent_title="01-Source-Parent",
                semantic_title="02-Source-Child",
            ),
        )

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        r = context.recorder
        description_section = r.record_structure(
            "description_section",
            await ensure_section(context.client, context.notebook_id, "00-Description"),
        )
        description_page = r.record_structure(
            "description_page",
            await ensure_page(
                context.client,
                description_section["id"],
                DESCRIPTION_TITLE,
                f"{DESCRIPTION}\nFixture token: {context.token}",
            ),
        )
        description_text = str(
            (
                await context.client.call_tool(
                    "get_page_text", {"page_id": description_page["id"]}
                )
            )["text"]
        )
        markers = (
            "原始状态：",
            "情况一——默认范围（省略 include_descendants）：",
            "情况二——完整子树（include_descendants=true）：",
            "默认运行会在自动 read-back 验证后清理两个目标",
        )
        if not all(marker in description_text for marker in markers):
            raise InvariantFailure("Copy Page Description is missing a state marker.")
        return await super().build(context)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        checks = list(super().validate(context, build))
        resolved, _by_id, state = resolve_active_structure(
            context.snapshot, build.structure
        )
        state.require(
            resolved["description_page"].get("section_id")
            == resolved["description_section"]["id"],
            "Copy Page Description escaped its Description Section.",
            "Description Page belongs to the fixture Description Section",
        )
        checks.append("Description Page belongs to the fixture Description Section")
        expected = {
            "description_section": "00-Description",
            "description_page": DESCRIPTION_TITLE,
            "parent_page": "01-Source-Parent",
            "semantic_page": "02-Source-Child",
        }
        state.require(
            all(display_name(resolved[key]) == name for key, name in expected.items()),
            "Copy Page fixture does not have stable Description/Source numbering.",
            "Description and source Pages use stable 00/01/02 prefixes",
        )
        checks.append("Description and source Pages use stable 00/01/02 prefixes")
        checks.append(
            "Description Page states original, default root-only, full-subtree, and cleanup states"
        )
        return tuple(checks)

RECIPE = CopyPageFixtureRecipe()
__all__ = ["CopyPageFixtureRecipe", "DESCRIPTION", "DESCRIPTION_TITLE", "RECIPE"]
