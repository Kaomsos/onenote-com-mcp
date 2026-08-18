"""Cache-capable fixture for hierarchy navigation tool validation."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ...runtime import InvariantFailure
from ...test_utils import stable_item, utc_now
from ..common.fixture_builders import (
    enforce_page_position,
    ensure_group,
    ensure_page,
    ensure_section,
)
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from ..common.page_readback import SPECIAL_PAGE_TITLE
from .recipe_base import NotebookRoleSpec, RecipeBase


class HierarchyNavigationFixtureRecipe(RecipeBase):
    recipe_version = 4

    def __init__(self) -> None:
        profile = self._profile("hierarchy-navigation")
        source_keys = tuple(profile.manifest_keys)
        browse_keys: tuple[str, ...] = ()
        source_profile = replace(
            profile,
            name="hierarchy-navigation-source",
            manifest_keys=source_keys,
        )
        browse_profile = replace(
            profile,
            name="hierarchy-navigation-browse-b",
            expected_structure=("second open Notebook root",),
            content_capabilities=(),
            manifest_keys=browse_keys,
            creation_tools=frozenset(),
            validation_conditions=("second Notebook role is open and independently bound",),
        )
        super().__init__(
            "hierarchy-navigation",
            notebook_roles=(
                NotebookRoleSpec(
                    "browse-b",
                    browse_profile,
                    {"manifest_keys": []},
                ),
                NotebookRoleSpec(
                    "source",
                    source_profile,
                    {"manifest_keys": list(source_keys)},
                ),
            ),
        )

    @staticmethod
    async def capture_snapshot(client, notebook_id: str) -> dict[str, Any]:
        """Capture navigation fixture metadata through Expand only."""

        response = await client.call_tool(
            "expand_hierarchy", {"root_id": notebook_id, "max_depth": 8}
        )
        tree = response.get("tree")
        if not isinstance(tree, dict):
            raise InvariantFailure(
                "Hierarchy navigation fixture snapshot returned no expansion tree."
            )

        def flatten(node: dict[str, Any]) -> list[dict[str, Any]]:
            item = node.get("item")
            items = [stable_item(item)] if isinstance(item, dict) else []
            for child in node.get("children", []):
                if not isinstance(child, dict):
                    raise InvariantFailure(
                        "Hierarchy navigation fixture snapshot contains an invalid node."
                    )
                items.extend(flatten(child))
            return items

        items = flatten(tree)
        return {
            "captured_at": utc_now(),
            "notebook_id": notebook_id,
            "items": items,
            "page_hashes": {},
            "page_canonical_hashes": {},
            "page_reparent_hashes": {},
            "page_xml_hashes": {},
            "page_objects": {},
            "page_capability_projections": {},
            "page_mathml_structure_projections": {},
            "metadata_source": "expand_hierarchy",
        }

    @staticmethod
    def _profile(name: str):
        from ..common.specs import get_scenario_spec

        return get_scenario_spec(name).fixture

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        recorder = context.recorder
        if context.role == "browse-b":
            return FixtureBuildResult(recorder.structure, recorder.evidence)
        if context.role != "source":
            raise InvariantFailure(
                f"Unsupported hierarchy navigation Notebook role: {context.role}"
            )
        recorder.record_structure(
            "navigation_root_section",
            await ensure_section(
                context.client, context.notebook_id, "Navigation-Root-Section"
            ),
        )
        group = recorder.record_structure(
            "navigation_group",
            await ensure_group(
                context.client, context.notebook_id, "Navigation-Group"
            ),
        )
        recorder.record_structure(
            "navigation_section_sibling",
            await ensure_section(
                context.client, str(group["id"]), "Navigation-Group-Section"
            ),
        )
        inner = recorder.record_structure(
            "navigation_inner_group",
            await ensure_group(
                context.client, str(group["id"]), "Navigation-Inner-Group"
            ),
        )
        section = recorder.record_structure(
            "navigation_section",
            await ensure_section(
                context.client, str(inner["id"]), "Navigation-Target-Section"
            ),
        )
        parent = recorder.record_structure(
            "navigation_parent_page",
            await ensure_page(
                context.client,
                str(section["id"]),
                SPECIAL_PAGE_TITLE,
                "navigation parent page",
            ),
        )
        child = await ensure_page(
            context.client,
            str(section["id"]),
            "Navigation-Child",
            "navigation child page",
        )
        child = await enforce_page_position(
            context.client,
            str(section["id"]),
            str(child["id"]),
            str(parent["id"]),
            2,
        )
        recorder.record_structure("navigation_child_page", child)
        grandchild = await ensure_page(
            context.client,
            str(section["id"]),
            "Navigation-Grandchild",
            "navigation grandchild page",
        )
        grandchild = await enforce_page_position(
            context.client,
            str(section["id"]),
            str(grandchild["id"]),
            str(child["id"]),
            3,
        )
        recorder.record_structure("navigation_grandchild_page", grandchild)
        child_sibling = await ensure_page(
            context.client,
            str(section["id"]),
            "Navigation-Child-Sibling",
            "navigation child sibling page",
        )
        child_sibling = await enforce_page_position(
            context.client,
            str(section["id"]),
            str(child_sibling["id"]),
            str(grandchild["id"]),
            2,
        )
        recorder.record_structure("navigation_child_page_sibling", child_sibling)
        root_sibling = await ensure_page(
            context.client,
            str(section["id"]),
            "Navigation-Root-Sibling",
            "navigation root sibling page",
        )
        root_sibling = await enforce_page_position(
            context.client,
            str(section["id"]),
            str(root_sibling["id"]),
            str(child_sibling["id"]),
            1,
        )
        recorder.record_structure("navigation_root_page_sibling", root_sibling)
        return FixtureBuildResult(recorder.structure, recorder.evidence)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        if context.role == "browse-b":
            resolved, _by_id, checks = resolve_active_structure(
                context.snapshot, build.structure
            )
            checks.require(
                not resolved and bool(context.snapshot.get("notebook_id")),
                "Second hierarchy browsing Notebook role is not independently bound.",
                "second open Notebook role is independently bound",
            )
            return tuple(checks.checks)
        if context.role != "source":
            raise InvariantFailure(
                f"Unsupported hierarchy navigation validation role: {context.role}"
            )
        resolved, _by_id, checks = resolve_active_structure(
            context.snapshot, build.structure
        )
        notebook_id = str(context.snapshot.get("notebook_id", ""))
        root_section = resolved["navigation_root_section"]
        group = resolved["navigation_group"]
        inner = resolved["navigation_inner_group"]
        section = resolved["navigation_section"]
        section_sibling = resolved["navigation_section_sibling"]
        parent = resolved["navigation_parent_page"]
        child = resolved["navigation_child_page"]
        grandchild = resolved["navigation_grandchild_page"]
        child_sibling = resolved["navigation_child_page_sibling"]
        root_sibling = resolved["navigation_root_page_sibling"]
        checks.require(
            root_section.get("resource_type") == "section"
            and root_section.get("parent_id") == notebook_id
            and group.get("resource_type") == "section_group"
            and group.get("parent_id") == notebook_id
            and inner.get("resource_type") == "section_group"
            and inner.get("parent_id") == group.get("id")
            and section.get("resource_type") == "section"
            and section.get("parent_id") == inner.get("id")
            and section_sibling.get("resource_type") == "section"
            and section_sibling.get("parent_id") == group.get("id"),
            "Hierarchy navigation container topology is invalid.",
            "Notebook/root Section/nested SectionGroups/Section ancestry is exact",
        )
        checks.require(
            all(
                page.get("resource_type") == "page"
                and page.get("section_id") == section.get("id")
                for page in (
                    parent,
                    child,
                    grandchild,
                    child_sibling,
                    root_sibling,
                )
            )
            and (parent.get("page_level"), parent.get("parent_page_id"))
            == (1, None)
            and (child.get("page_level"), child.get("parent_page_id"))
            == (2, parent.get("id"))
            and (
                grandchild.get("page_level"),
                grandchild.get("parent_page_id"),
            )
            == (3, child.get("id"))
            and (
                child_sibling.get("page_level"),
                child_sibling.get("parent_page_id"),
            )
            == (2, parent.get("id"))
            and (
                root_sibling.get("page_level"),
                root_sibling.get("parent_page_id"),
            )
            == (1, None),
            "Hierarchy navigation Page indentation topology is invalid.",
            "Page levels 1/2/3 derive the exact branched indentation tree",
        )
        checks.require(
            all(
                str(page["id"]) in _by_id
                for page in (
                    parent,
                    child,
                    grandchild,
                    child_sibling,
                    root_sibling,
                )
            ),
            "Hierarchy navigation fixture lacks complete Page metadata evidence.",
            "every navigation Page was observed through Expand metadata",
        )
        checks.require(
            str(parent.get("title", "")) == SPECIAL_PAGE_TITLE,
            "Hierarchy navigation Page title lost special characters during fixture creation.",
            "path target preserves the exact special-character Page title",
        )
        return tuple(checks.checks)


RECIPE = HierarchyNavigationFixtureRecipe()

__all__ = ["HierarchyNavigationFixtureRecipe", "RECIPE"]
