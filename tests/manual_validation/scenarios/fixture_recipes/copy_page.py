"""Fixture recipe owned by the six-case, two-Notebook Page Copy scenario."""

from __future__ import annotations

from dataclasses import replace

from ...runtime import InvariantFailure
from ...test_utils import display_name
from ..common.fixture_builders import (
    capture_page_image_binary_evidence,
    create_fresh_root_page_anchor,
    ensure_page,
    ensure_section,
)
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from ..common.page_readback import SPECIAL_PAGE_TITLE, SPECIAL_PAGE_TITLE_CASEFOLD
from ..common.specs import get_scenario_spec
from .layered_copy import LayeredCopyFixtureRecipe, LayeredFixtureConfig, LayeredFixtureKind
from .recipe_base import (
    FixtureBundleObservation,
    FixtureValidationReport,
    NotebookRoleSpec,
)


DESCRIPTION_TITLE = "00-Copy-Page-Description"
DESCRIPTION = f"""Copy Page 人工验收说明

本场景从同一个 Source/{SPECIAL_PAGE_TITLE} 依次执行六次 Copy：
同 Section、跨 Section、跨 Notebook，各自覆盖不带子树与带子树。

原始状态：
Source/{SPECIAL_PAGE_TITLE}：page_level=1，包含 Rich Text、行内公式、单行公式、Table 和 Image
  Source/02-Source-Child：page_level=2，缩进在 Parent 下，包含 List 和 Tag
Source-Destination 另有与源根同标题、仅大小写不同的一级 Page，以及 02-Source-Child 跨 Section child-title anchor
Destination Notebook/Cross-Notebook-Destination 另有与源根同标题的一级 Page 和 02-Source-Child child-title anchor

每个目标范围的“不带子树” case 使用 include_subpages=false，只复制 Parent。
每个目标范围的“带子树” case 使用 include_subpages=true，复制 Parent 与 Child。

same-section root-only 与 cross-Notebook root-only 省略 destination_title，
目标标题必须精确保留 source 的特殊字符标题；
cross-section root-only 使用源标题的 casefold 作为显式 destination_title；
cross-Notebook subtree 使用源标题作为显式 destination_title；
其余 explicit 目标仍使用 02/04 唯一前缀；
带子树目标中的 Child 必须是 fresh ID 并保持相对层级，不能复用源 Child 或同名 anchor；
全部 case 后 Source 与两个 anchors 的 ID、顺序、层级和内容不变。

使用 --keep-worksite 时，六个目标会同时保留在两个 working Notebook 中供 UI 对照；
默认运行会在自动 read-back 验证后清理六个目标，并恢复两个 Notebook 的原始状态。
"""

class CopyPageFixtureRecipe(LayeredCopyFixtureRecipe):
    recipe_version = 16
    bundle_invariants = (
        "source and destination Notebook IDs and resolved paths are unique",
        "the cross-Notebook destination Section belongs only to destination",
        "both non-source destinations contain distinct same-title anchors",
    )

    def __init__(self) -> None:
        source_keys = (
            "description_section",
            "description_page",
            "source_section",
            "parent_page",
            "semantic_page",
            "disposable_section",
            "cross_section_anchor",
            "cross_section_root_title_anchor",
            "cross_section_root_title_casefold_anchor",
            "cross_section_position_anchor",
        )
        destination_keys = (
            "cross_notebook_section",
            "cross_notebook_anchor",
            "cross_notebook_root_title_anchor",
            "cross_notebook_position_anchor",
        )
        profile = get_scenario_spec("copy-page").fixture
        source_profile = replace(
            profile,
            name="rich-page-copy-source",
            expected_structure=profile.expected_structure[:-1],
            manifest_keys=source_keys,
            validation_conditions=profile.validation_conditions[:-1],
        )
        destination_profile = replace(
            profile,
            name="copy-page-destination",
            expected_structure=(
                "Cross-Notebook-Destination/{exact root title, 02-Source-Child, 99-Position-Anchor}",
            ),
            content_capabilities=("plain_text",),
            manifest_keys=destination_keys,
            creation_tools=frozenset({"create_section", "create_page"}),
            validation_conditions=(
                "cross-Notebook destination is an active root Section",
                "cross-Notebook duplicate-title anchor is bound to that Section",
            ),
        )
        super().__init__(
            "copy-page",
            LayeredFixtureConfig(
                LayeredFixtureKind.PAGE,
                parent_title=SPECIAL_PAGE_TITLE,
                semantic_title="02-Source-Child",
                include_equations=True,
            ),
            notebook_roles=(
                NotebookRoleSpec(
                    "destination",
                    destination_profile,
                    {"manifest_keys": list(destination_keys)},
                ),
                NotebookRoleSpec(
                    "source",
                    source_profile,
                    {"manifest_keys": list(source_keys)},
                ),
            ),
        )

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        if context.role == "destination":
            destination = context.recorder.record_structure(
                "cross_notebook_section",
                await ensure_section(
                    context.client,
                    context.notebook_id,
                    "Cross-Notebook-Destination",
                ),
            )
            context.recorder.record_structure(
                "cross_notebook_root_title_anchor",
                await ensure_page(
                    context.client,
                    destination["id"],
                    SPECIAL_PAGE_TITLE,
                    f"Cross-Notebook root-title anchor token: {context.token}",
                ),
            )
            context.recorder.record_structure(
                "cross_notebook_anchor",
                await ensure_page(
                    context.client,
                    destination["id"],
                    "02-Source-Child",
                    f"Cross-Notebook duplicate-title anchor token: {context.token}",
                ),
            )
            context.recorder.record_structure(
                "cross_notebook_position_anchor",
                await ensure_page(
                    context.client,
                    destination["id"],
                    "99-Position-Anchor",
                    f"Cross-Notebook position anchor token: {context.token}",
                ),
            )
            return FixtureBuildResult(context.recorder.structure, context.recorder.evidence)
        if context.role != "source":
            raise InvariantFailure(f"Unsupported Copy Page Notebook role: {context.role}")
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
                    "get_page_text",
                    {"page_id": description_page["id"], "mode": "plain"},
                )
            )["text"]
        )
        markers = (
            "原始状态：",
            "同 Section、跨 Section、跨 Notebook",
            "行内公式、单行公式",
            "不带子树",
            "带子树",
            "省略 destination_title",
            "默认运行会在自动 read-back 验证后清理六个目标",
        )
        if not all(marker in description_text for marker in markers):
            raise InvariantFailure("Copy Page Description is missing a state marker.")
        build = await super().build(context)
        r.record_evidence(
            "page_content_object_binary",
            await capture_page_image_binary_evidence(
                context.client,
                str(build.structure["parent_page"]["id"]),
            ),
        )
        disposable_section = build.structure["disposable_section"]
        root_title_anchor = r.record_structure(
            "cross_section_root_title_anchor",
            await create_fresh_root_page_anchor(
                context.client,
                str(disposable_section["id"]),
                SPECIAL_PAGE_TITLE,
                f"Cross-Section root-title anchor token: {context.token}",
            ),
        )
        r.record_structure(
            "cross_section_root_title_casefold_anchor",
            await create_fresh_root_page_anchor(
                context.client,
                str(disposable_section["id"]),
                SPECIAL_PAGE_TITLE_CASEFOLD,
                f"Cross-Section casefold root-title anchor token: {context.token}",
                distinct_from_ids=(str(root_title_anchor["id"]),),
            ),
        )
        r.record_structure(
            "cross_section_anchor",
            await ensure_page(
                context.client,
                str(disposable_section["id"]),
                "02-Source-Child",
                f"Cross-Section duplicate-title anchor token: {context.token}",
            ),
        )
        r.record_structure(
            "cross_section_position_anchor",
            await ensure_page(
                context.client,
                str(disposable_section["id"]),
                "99-Position-Anchor",
                f"Cross-Section position anchor token: {context.token}",
            ),
        )
        return FixtureBuildResult(r.structure, r.evidence)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        if set(build.structure) == {
            "cross_notebook_section",
            "cross_notebook_anchor",
            "cross_notebook_root_title_anchor",
            "cross_notebook_position_anchor",
        }:
            resolved, _by_id, state = resolve_active_structure(
                context.snapshot, build.structure
            )
            destination = resolved["cross_notebook_section"]
            state.require(
                destination.get("resource_type") == "section",
                "Cross-Notebook destination is not an active Section.",
                "destination role exposes one active cross-Notebook Section",
            )
            root_title = resolved["cross_notebook_root_title_anchor"]
            state.require(
                root_title.get("resource_type") == "page"
                and root_title.get("section_id") == destination["id"]
                and display_name(root_title) == SPECIAL_PAGE_TITLE
                and root_title.get("parent_page_id") is None,
                "Cross-Notebook root-title anchor is missing or misplaced.",
                "cross-Notebook destination contains a first-level exact root-title anchor",
            )
            anchor = resolved["cross_notebook_anchor"]
            state.require(
                anchor.get("resource_type") == "page"
                and anchor.get("section_id") == destination["id"]
                and display_name(anchor) == "02-Source-Child"
                and anchor["id"] != root_title["id"],
                "Cross-Notebook duplicate-title anchor is missing or misplaced.",
                "cross-Notebook duplicate-title anchor belongs to its destination Section",
            )
            position_anchor = resolved["cross_notebook_position_anchor"]
            state.require(
                position_anchor.get("resource_type") == "page"
                and position_anchor.get("section_id") == destination["id"]
                and position_anchor["id"] != anchor["id"]
                and position_anchor["id"] != root_title["id"],
                "Cross-Notebook position anchor is missing or misplaced.",
                "cross-Notebook destination contains distinct Page anchors",
            )
            return tuple(state.checks)
        checks = list(super().validate(context, build))
        binary_evidence = build.evidence.get("page_content_object_binary")
        state_binary_valid = (
            isinstance(binary_evidence, dict)
            and binary_evidence.get("tool") == "get_page_content_object_binary"
            and binary_evidence.get("selection") == "exact_page_content_object_id"
            and binary_evidence.get("callback_resolution_verified") is True
            and isinstance(
                binary_evidence.get("public_id_distinct_from_callback"), bool
            )
            and binary_evidence.get("kind") == "Image"
            and isinstance(binary_evidence.get("media_type"), str)
            and int(binary_evidence.get("decoded_bytes", 0)) > 0
            and isinstance(binary_evidence.get("sha256"), str)
            and len(binary_evidence["sha256"]) == 64
            and binary_evidence.get("payload_persisted") is False
        )
        if not state_binary_valid:
            raise InvariantFailure(
                "Copy Page fixture has no valid public Image binary retrieval evidence."
            )
        checks.append(
            "rich source Image binary was read by exact PageContentObject ID without persisting Base64"
        )
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
            "parent_page": SPECIAL_PAGE_TITLE,
            "semantic_page": "02-Source-Child",
        }
        state.require(
            all(display_name(resolved[key]) == name for key, name in expected.items()),
            "Copy Page fixture does not have stable Description/Source numbering.",
            "Description and source Pages use stable 00/01/02 prefixes",
        )
        checks.append("Description and source Pages use stable 00/01/02 prefixes")
        checks.append(
            "Description Page states the three destination scopes, two subtree modes, and cleanup state"
        )
        anchor = resolved["cross_section_anchor"]
        root_title = resolved["cross_section_root_title_anchor"]
        casefold_title = resolved["cross_section_root_title_casefold_anchor"]
        position_anchor = resolved["cross_section_position_anchor"]
        semantic = resolved["semantic_page"]
        parent = resolved["parent_page"]
        disposable_id = resolved["disposable_section"]["id"]
        state.require(
            anchor.get("resource_type") == "page"
            and anchor.get("section_id") == disposable_id
            and display_name(anchor) == display_name(semantic)
            and anchor["id"] != semantic["id"],
            "Cross-Section duplicate-title anchor is missing, misplaced, or aliases the source Child.",
            "cross-Section destination contains a distinct same-title anchor",
        )
        state.require(
            root_title.get("resource_type") == "page"
            and casefold_title.get("resource_type") == "page"
            and root_title.get("section_id") == disposable_id
            and casefold_title.get("section_id") == disposable_id
            and display_name(root_title) == SPECIAL_PAGE_TITLE
            and display_name(casefold_title) == SPECIAL_PAGE_TITLE_CASEFOLD
            and root_title.get("parent_page_id") is None
            and casefold_title.get("parent_page_id") is None
            and root_title["id"] != parent["id"]
            and casefold_title["id"] != parent["id"]
            and root_title["id"] != casefold_title["id"],
            "Cross-Section root-title anchors are missing, nested, or alias the source root.",
            "cross-Section destination contains distinct first-level root-title anchors",
        )
        state.require(
            position_anchor.get("resource_type") == "page"
            and position_anchor.get("section_id") == disposable_id
            and position_anchor["id"] != anchor["id"]
            and position_anchor["id"] != root_title["id"],
            "Cross-Section position anchor is missing or misplaced.",
            "cross-Section destination contains distinct Page anchors",
        )
        hashes = context.snapshot.get("page_hashes", {})
        state.require(
            isinstance(hashes, dict)
            and hashes.get(anchor["id"])
            and hashes.get(anchor["id"]) != hashes.get(semantic["id"])
            and hashes.get(root_title["id"])
            and hashes.get(casefold_title["id"])
            and hashes.get(root_title["id"]) != hashes.get(parent["id"])
            and hashes.get(casefold_title["id"]) != hashes.get(parent["id"]),
            "Cross-Section title anchors are not independently observable.",
            "cross-Section anchor body hashes differ from the same-title source Pages",
        )
        checks.extend(
            (
                "cross-Section destination contains a distinct same-title anchor",
                "cross-Section destination contains distinct first-level root-title anchors",
                "cross-Section anchor body hashes differ from the same-title source Pages",
            )
        )
        return tuple(checks)

    def validate_live(
        self,
        observation: FixtureBundleObservation,
    ) -> FixtureValidationReport:
        report = super().validate_live(observation)
        source = observation.roles["source"]
        destination = observation.roles["destination"]
        destination_section = destination.build.structure["cross_notebook_section"]
        destination_anchor = destination.build.structure["cross_notebook_anchor"]
        destination_root_title = destination.build.structure[
            "cross_notebook_root_title_anchor"
        ]
        if str(destination_section.get("parent_id", "")) != str(
            destination.notebook["id"]
        ) or str(destination_section.get("notebook_id", "")) not in {
            "",
            str(destination.notebook["id"]),
        }:
            raise InvariantFailure(
                "Cross-Notebook destination Section escaped the destination role."
            )
        if (
            str(destination_anchor.get("section_id", ""))
            != str(destination_section["id"])
            or display_name(dict(destination_anchor)) != "02-Source-Child"
        ):
            raise InvariantFailure(
                "Cross-Notebook duplicate-title anchor escaped its destination Section."
            )
        if (
            str(destination_root_title.get("section_id", ""))
            != str(destination_section["id"])
            or display_name(dict(destination_root_title)) != SPECIAL_PAGE_TITLE
            or destination_root_title.get("parent_page_id") is not None
            or str(destination_root_title.get("id", ""))
            == str(source.build.structure["parent_page"]["id"])
        ):
            raise InvariantFailure(
                "Cross-Notebook root-title anchor escaped its destination Section."
            )
        source_hashes = source.snapshot.get("page_hashes", {})
        destination_hashes = destination.snapshot.get("page_hashes", {})
        source_child_id = str(source.build.structure["semantic_page"]["id"])
        source_parent_id = str(source.build.structure["parent_page"]["id"])
        destination_anchor_id = str(destination_anchor["id"])
        destination_root_id = str(destination_root_title["id"])
        if (
            not isinstance(source_hashes, dict)
            or not isinstance(destination_hashes, dict)
            or not source_hashes.get(source_child_id)
            or not destination_hashes.get(destination_anchor_id)
            or source_hashes[source_child_id]
            == destination_hashes[destination_anchor_id]
            or not source_hashes.get(source_parent_id)
            or not destination_hashes.get(destination_root_id)
            or source_hashes[source_parent_id]
            == destination_hashes[destination_root_id]
        ):
            raise InvariantFailure(
                "Cross-Notebook same-title anchors are not distinct from the source Pages."
            )
        if str(source.notebook["id"]) == str(destination.notebook["id"]):
            raise InvariantFailure("Copy Page bundle roles resolved to the same Notebook ID.")
        return FixtureValidationReport(
            passed=report.passed,
            role_checks=report.role_checks,
            bundle_checks=report.bundle_checks
            + (
                "cross-Notebook destination is bound to the destination role",
                "cross-Notebook duplicate-title anchor is bound to that destination",
                "cross-Notebook root-title anchor is bound to that destination",
                "cross-Notebook anchor body hashes differ from the same-title source Pages",
            ),
        )

RECIPE = CopyPageFixtureRecipe()
__all__ = ["CopyPageFixtureRecipe", "DESCRIPTION", "DESCRIPTION_TITLE", "RECIPE"]
