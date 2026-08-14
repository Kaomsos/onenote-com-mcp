"""Cache-capable two-Notebook fixture for live OneNote index Search validation."""

from __future__ import annotations

from dataclasses import replace
import random
import re
import secrets
import string
from typing import Any, Mapping

from local_onenote_mcp.page import text_from_page_xml
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
    recipe_version = 3
    bundle_invariants = (
        "source and search-b Notebook IDs and resolved paths are unique",
        "both working Notebook roles remain open for root Search validation",
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
            name="index-search-probes-source",
            manifest_keys=source_keys,
        )
        search_b_profile = replace(
            profile,
            name="index-search-probes-search-b",
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
        self._snapshot_content: list[dict[str, Any]] = []

    def begin_snapshot_content_validation(self) -> None:
        self._snapshot_content = []

    def snapshot_page_observer(
        self,
        role: str,
        build: FixtureBuildResult,
    ):
        del build

        def observe(page: Mapping[str, Any], xml: str) -> None:
            text = text_from_page_xml(xml)
            self._snapshot_content.append(
                {
                    "role": role,
                    "page_id": str(page.get("id", "")),
                    "title": str(page.get("title", "")),
                    "search": re.findall(
                        r"SEARCH_PROBE:([A-Za-z0-9]{15}-[A-Za-z0-9]{16})",
                        text,
                    ),
                    "budget": re.findall(r"BUDGET_MARKER:([A-Za-z0-9]{24})", text),
                    "long": re.findall(r"LONG_TEXT_MARKER:([A-Za-z0-9]{24})", text),
                }
            )

        return observe

    def complete_snapshot_content_validation(self) -> None:
        observations = self._snapshot_content
        primary_addresses = {
            ("source", "Probe Page A1"),
            ("source", "Probe Page A2"),
            ("source", "Probe Page A3"),
            ("search-b", "Probe Page B1"),
        }
        long_address = ("search-b", "Budget Long Text Page B2")
        observed_addresses = {
            (str(item["role"]), str(item["title"])) for item in observations
        }
        if len(observations) != 5 or observed_addresses != primary_addresses | {
            long_address
        }:
            raise InvariantFailure(
                "Search fixture one-read content validation did not observe all five Pages."
            )
        search_values = [item["search"][0] for item in observations if item["search"]]
        budget_values = [
            values[0]
            for item in observations
            if len(values := item["budget"]) == 1
        ]
        long_values = [
            values[0]
            for item in observations
            if len(values := item["long"]) == 1
        ]
        if (
            any(len(item["search"]) > 1 for item in observations)
            or any(len(item["budget"]) != 1 for item in observations)
            or any(len(item["long"]) > 1 for item in observations)
            or any(
                len(item["search"])
                != (1 if (item["role"], item["title"]) in primary_addresses else 0)
                for item in observations
            )
            or any(
                len(item["long"])
                != (1 if (item["role"], item["title"]) == long_address else 0)
                for item in observations
            )
            or len(search_values) != 4
            or len(set(search_values)) != 1
            or len(budget_values) != 5
            or len(set(budget_values)) != 1
            or len(long_values) != 1
        ):
            raise InvariantFailure(
                "Search fixture Page bodies do not contain one coherent probe set."
            )
        self.probe = search_values[0]
        self.budget_marker = budget_values[0]
        self.long_text_marker = long_values[0]

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
        expected_keys = self.manifest_keys_for_role(context.role, context.args)
        if set(build.structure) != set(expected_keys):
            raise InvariantFailure(
                f"Search fixture role {context.role} received another role's structure."
            )
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        notebook_id = str(context.snapshot.get("notebook_id", ""))
        if context.role == "source":
            group = resolved["probe_group"]
            section_1 = resolved["probe_section_1"]
            section_2 = resolved["probe_section_2"]
            root_section = resolved["root_section"]
            checks.require(
                group.get("resource_type") == "section_group"
                and group.get("parent_id") == notebook_id
                and section_1.get("resource_type") == "section"
                and section_1.get("parent_id") == group.get("id")
                and section_2.get("resource_type") == "section"
                and section_2.get("parent_id") == group.get("id")
                and root_section.get("resource_type") == "section"
                and root_section.get("parent_id") == notebook_id,
                "Source Search fixture container topology is invalid.",
                "source Group and Sections have exact types and parent IDs",
            )
            expected_sections = {
                "probe_page_a1": section_1["id"],
                "probe_page_a2": section_2["id"],
                "probe_page_a3": root_section["id"],
            }
        elif context.role == "search-b":
            section = resolved["probe_section_b"]
            checks.require(
                section.get("resource_type") == "section"
                and section.get("parent_id") == notebook_id,
                "Secondary Search fixture Section topology is invalid.",
                "secondary Search Section has exact type and Notebook parent",
            )
            expected_sections = {
                "probe_page_b1": section["id"],
                "budget_page_b2": section["id"],
            }
        else:
            raise InvariantFailure(f"Unsupported Search validation role: {context.role}")
        checks.require(
            all(
                resolved[key].get("resource_type") == "page"
                and resolved[key].get("section_id") == section_id
                and resolved[key].get("parent_page_id") in {None, ""}
                and int(resolved[key].get("page_level", 0)) == 1
                for key, section_id in expected_sections.items()
            ),
            "Search fixture Page parentage is invalid.",
            "all Search fixture Pages have exact types, Sections, and root levels",
        )
        page_hashes = context.snapshot.get("page_hashes", {})
        checks.require(
            isinstance(page_hashes, dict)
            and all(str(resolved[key]["id"]) in page_hashes for key in expected_sections),
            "Search fixture snapshot lacks complete Page content evidence.",
            "every Search Page was read once during fixture snapshot validation",
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
        if any(role.build.evidence for role in observation.roles.values()):
            raise InvariantFailure(
                "Search fixture persisted raw run-unique probes outside disposable Page bodies."
            )
        return FixtureValidationReport(
            passed=report.passed,
            role_checks=report.role_checks,
            bundle_checks=report.bundle_checks
            + (
                "two Search Notebook roles are distinct",
                "raw Search probes were not persisted in fixture evidence",
            ),
        )


RECIPE = SearchAllOpenNotebooksFixtureRecipe()

__all__ = ["RECIPE", "SearchAllOpenNotebooksFixtureRecipe", "generate_search_probe"]
