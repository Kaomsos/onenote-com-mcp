"""Fresh two-Notebook fixture for live OneNote index Search validation."""

from __future__ import annotations

from dataclasses import replace
import random
import secrets
import string

from ...runtime import InvariantFailure
from ..common.fixture_builders import ensure_group, ensure_page, ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from ..common.specs import get_scenario_spec
from .recipe_base import (
    FixtureBundleObservation,
    FixtureValidationReport,
    NotebookRoleSpec,
    RecipeBase,
)


def _random_alphanumeric(length: int) -> str:
    if length < 3:
        raise ValueError("Random probe segments must be at least three characters.")
    alphabet = string.ascii_letters + string.digits
    values = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        *(secrets.choice(alphabet) for _ in range(length - 3)),
    ]
    random.SystemRandom().shuffle(values)
    return "".join(values)


def generate_search_probe() -> str:
    """Return exactly 32 safe characters with a fixed hyphen at position 16."""

    return f"{_random_alphanumeric(15)}-{_random_alphanumeric(16)}"


class SearchAllOpenNotebooksFixtureRecipe(RecipeBase):
    recipe_version = 1
    supports_cache = False
    bundle_invariants = (
        "source and search-b Notebook IDs and resolved paths are unique",
        "both fresh Notebook roles remain open for root Search validation",
        "raw search probes exist only in process memory and disposable Page bodies",
    )

    def __init__(self) -> None:
        self.probe = generate_search_probe()
        self.budget_marker = _random_alphanumeric(24)
        self.long_text_marker = _random_alphanumeric(24)
        profile = get_scenario_spec("search-all-open-notebooks").fixture
        source_keys = (
            "probe_group",
            "probe_section_1",
            "probe_page_a1",
            "probe_section_2",
            "probe_page_a2",
            "root_section",
            "probe_page_a3",
        )
        search_b_keys = ("probe_section_b", "probe_page_b1", "budget_page_b2")
        source_profile = replace(
            profile,
            name="fresh-index-search-probes-source",
            manifest_keys=source_keys,
        )
        search_b_profile = replace(
            profile,
            name="fresh-index-search-probes-search-b",
            expected_structure=(
                "Probe Section B/{Probe Page B1,Budget Long Text Page B2}",
            ),
            manifest_keys=search_b_keys,
        )
        super().__init__(
            "search-all-open-notebooks",
            notebook_roles=(
                NotebookRoleSpec(
                    "search-b", search_b_profile, {"manifest_keys": list(search_b_keys)}
                ),
                NotebookRoleSpec(
                    "source", source_profile, {"manifest_keys": list(source_keys)}
                ),
            ),
        )

    def _probe_body(self, label: str) -> str:
        return (
            f"{label}\n"
            f"SEARCH_PROBE:{self.probe}\n"
            f"BUDGET_MARKER:{self.budget_marker}"
        )

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        recorder = context.recorder
        if context.role == "source":
            group = recorder.record_structure(
                "probe_group",
                await ensure_group(context.client, context.notebook_id, "Probe Group"),
            )
            section_1 = recorder.record_structure(
                "probe_section_1",
                await ensure_section(context.client, str(group["id"]), "Probe Section 1"),
            )
            recorder.record_structure(
                "probe_page_a1",
                await ensure_page(
                    context.client,
                    str(section_1["id"]),
                    "Probe Page A1",
                    self._probe_body("A1"),
                ),
            )
            section_2 = recorder.record_structure(
                "probe_section_2",
                await ensure_section(context.client, str(group["id"]), "Probe Section 2"),
            )
            recorder.record_structure(
                "probe_page_a2",
                await ensure_page(
                    context.client,
                    str(section_2["id"]),
                    "Probe Page A2",
                    self._probe_body("A2"),
                ),
            )
            root_section = recorder.record_structure(
                "root_section",
                await ensure_section(
                    context.client, context.notebook_id, "Notebook Root Section"
                ),
            )
            recorder.record_structure(
                "probe_page_a3",
                await ensure_page(
                    context.client,
                    str(root_section["id"]),
                    "Probe Page A3",
                    self._probe_body("A3"),
                ),
            )
        elif context.role == "search-b":
            section = recorder.record_structure(
                "probe_section_b",
                await ensure_section(context.client, context.notebook_id, "Probe Section B"),
            )
            recorder.record_structure(
                "probe_page_b1",
                await ensure_page(
                    context.client,
                    str(section["id"]),
                    "Probe Page B1",
                    self._probe_body("B1"),
                ),
            )
            recorder.record_structure(
                "budget_page_b2",
                await ensure_page(
                    context.client,
                    str(section["id"]),
                    "Budget Long Text Page B2",
                    (
                        "B2\n"
                        f"BUDGET_MARKER:{self.budget_marker}\n"
                        f"LONG_TEXT_MARKER:{self.long_text_marker}\n"
                        + ("X" * 800)
                    ),
                ),
            )
        else:
            raise InvariantFailure(f"Unsupported Search fixture role: {context.role}")
        return FixtureBuildResult(recorder.structure, recorder.evidence)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        if "probe_group" in resolved:
            group = resolved["probe_group"]
            section_1 = resolved["probe_section_1"]
            section_2 = resolved["probe_section_2"]
            root_section = resolved["root_section"]
            checks.require(
                section_1.get("parent_id") == group.get("id")
                and section_2.get("parent_id") == group.get("id")
                and root_section.get("parent_id") == context.snapshot.get("notebook_id"),
                "Source Search fixture container topology is invalid.",
                "source group and root Sections have exact parent IDs",
            )
            expected_sections = {
                "probe_page_a1": section_1["id"],
                "probe_page_a2": section_2["id"],
                "probe_page_a3": root_section["id"],
            }
        else:
            expected_sections = {
                "probe_page_b1": resolved["probe_section_b"]["id"],
                "budget_page_b2": resolved["probe_section_b"]["id"],
            }
        checks.require(
            all(resolved[key].get("section_id") == section_id for key, section_id in expected_sections.items()),
            "Search fixture Page parentage is invalid.",
            "all Search fixture Pages have exact Section IDs",
        )
        return tuple(checks.checks)

    def validate_live(
        self,
        observation: FixtureBundleObservation,
    ) -> FixtureValidationReport:
        report = super().validate_live(observation)
        if str(observation.roles["source"].notebook["id"]) == str(
            observation.roles["search-b"].notebook["id"]
        ):
            raise InvariantFailure("Search fixture roles resolved to the same Notebook ID.")
        return FixtureValidationReport(
            passed=report.passed,
            role_checks=report.role_checks,
            bundle_checks=report.bundle_checks + ("two Search Notebook roles are distinct",),
        )


RECIPE = SearchAllOpenNotebooksFixtureRecipe()

__all__ = ["RECIPE", "SearchAllOpenNotebooksFixtureRecipe", "generate_search_probe"]
