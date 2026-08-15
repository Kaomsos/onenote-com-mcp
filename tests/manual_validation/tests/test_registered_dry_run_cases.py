"""Registered dry-run catalog, pure-plan, CLI, and documentation contracts."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re

import pytest

from tests.manual_validation import all_scenarios, lifecycle, mcp_stdio_client, test_utils
from tests.manual_validation.runner import build_parser, main
from tests.manual_validation.scenarios.common import fixture_runtime, orchestrator
from tests.manual_validation.scenarios.common.dry_run import DryRunCase
from tests.manual_validation.scenarios.common.registry import (
    SCENARIO_REGISTRY,
    get_all_scenario_names,
)


DRY_RUN_CASES = SCENARIO_REGISTRY.dry_run_cases
NAMED_DRY_RUN_CASES = tuple(
    case for case in DRY_RUN_CASES if case.scenario_name != "all"
)


def _side_effect_called(*_args, **_kwargs):
    raise AssertionError("registered dry-run attempted a guarded side effect")


def _install_sentinels(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "MCPStdioClient", _side_effect_called)
    monkeypatch.setattr(orchestrator, "NotebookLifecycleWrapper", _side_effect_called)
    monkeypatch.setattr(orchestrator, "write_json", _side_effect_called)
    monkeypatch.setattr(fixture_runtime, "write_json", _side_effect_called)
    monkeypatch.setattr(test_utils, "write_json", _side_effect_called)
    monkeypatch.setattr(lifecycle, "OneNoteBridge", _side_effect_called)
    monkeypatch.setattr(lifecycle, "write_json", _side_effect_called)
    monkeypatch.setattr(mcp_stdio_client, "stdio_client", _side_effect_called)
    monkeypatch.setattr(all_scenarios.subprocess, "run", _side_effect_called)
    monkeypatch.setattr(all_scenarios.subprocess, "Popen", _side_effect_called)


def test_catalog_has_stable_unique_coverage_independent_from_all() -> None:
    ids = [case.case_id for case in DRY_RUN_CASES]
    assert len(ids) == len(set(ids))
    assert ids[-1] == "all.default"
    covered = {case.scenario_name for case in NAMED_DRY_RUN_CASES}
    assert covered == set(SCENARIO_REGISTRY.public_names)
    for scenario in SCENARIO_REGISTRY.values():
        scenario_cases = [case for case in NAMED_DRY_RUN_CASES if case.scenario_name == scenario.name]
        assert f"{scenario.name}.default" in {case.case_id for case in scenario_cases}
        assert f"{scenario.name}.keep-worksite" in {case.case_id for case in scenario_cases}
    excluded = set(SCENARIO_REGISTRY.public_names) - set(get_all_scenario_names())
    assert excluded == {
        "bootstrap-inserted-file-fixture",
        "bootstrap-ink-drawing-fixture",
        "bootstrap-media-file-fixture",
        "bootstrap-shape-fixture",
        "copy-display-equation",
        "bootstrap-inline-equation-fixture",
        "bootstrap-user-authored-fixture",
        "cache-invalidation",
        "user-authored-fixture-consumer",
        "interactive-copy-inserted-file",
        "interactive-copy-ink-drawing",
        "interactive-copy-media-file",
        "interactive-copy-ui-shape",
        "interactive-copy-inline-equation",
        "onenote-convergence",
        "hierarchy-navigation",
    }
    assert excluded <= covered


@pytest.mark.parametrize("case", NAMED_DRY_RUN_CASES, ids=lambda case: case.case_id)
def test_registered_named_case_round_trips_through_guarded_cli(
    case: DryRunCase, monkeypatch, tmp_path, capsys
) -> None:
    _install_sentinels(monkeypatch)
    run_dir = tmp_path / case.case_id
    argv = case.argv(run_dir)
    parsed = build_parser().parse_args(argv)
    assert parsed.command == case.scenario_name
    assert parsed.dry_run is True
    assert parsed.json_output is True
    assert parsed.run_dir == run_dir

    assert main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    scenario = SCENARIO_REGISTRY.get(case.scenario_name)
    parsed.scenario = parsed.command
    spec = scenario.runtime_spec(parsed)
    multi_role = len(scenario.fixture_recipe.cache_identity.notebook_roles) > 1
    assert payload["dry_run_contract"] is True
    assert payload["server_started"] is case.expected.server_started
    assert payload["expected_mcp_process_starts"] == case.expected.expected_mcp_process_starts
    assert payload["lifecycle"] == case.expected.lifecycle
    assert payload["scenario_spec"] == spec.as_dict()
    assert payload["fixture_profile"] == scenario.fixture_profile.as_dict()
    assert payload["human_only"] is True
    assert payload["agent_execution_prohibited"] is True
    assert payload["copy_budget"]["max_pages"] == 200
    if case.scenario_name.startswith("bootstrap-"):
        checkpoint = payload["cache"]["interactive_checkpoint"]
        assert checkpoint["stdin_read_performed"] is False
        assert checkpoint["authoring_instruction"] == (
            scenario.fixture_recipe.authoring_instruction
        )
    consumer_cache_required = (
        scenario.fixture_recipe.consumer_scenario
        and "--use-cache" not in case.scenario_args
    )
    if consumer_cache_required:
        assert [step["step"] for step in payload["ordered_steps"]] == [
            "preflight-cache-required"
        ]
        assert payload["cache"]["decision"] == "rejected_missing_use_cache"
    elif (
        getattr(scenario.fixture_recipe, "representation_discovery_only", False)
        and "--use-cache" in case.scenario_args
    ):
        assert [step["step"] for step in payload["ordered_steps"]] == [
            "preflight-discovery-rejects-cache"
        ]
        assert payload["cache"]["decision"] == (
            "rejected_cache_for_representation_discovery"
        )
        assert payload["expected_mcp_process_starts"] == 0
    else:
        assert payload["ordered_steps"][0]["step"] == (
            "resolve-fixture-bundle"
            if "--use-cache" in case.scenario_args
            and payload["cache"]["cache_mode"] != "interactive_bootstrap"
            else (
                "create-notebook-bundle" if multi_role else "create-source-notebook"
            )
        )
        mutation_step = next(
            step
            for step in payload["ordered_steps"]
            if step["step"] == case.scenario_name
        )
        assert mutation_step["tool_allowlist"] == sorted(spec.tool_allowlist)
        if (
            "--use-cache" in case.scenario_args
            and payload["cache"]["cache_mode"] != "interactive_bootstrap"
        ):
            assert payload["ordered_steps"][1]["step"] == (
                "prepare-materialized-fixture"
            )
            preparation = payload["ordered_steps"][1]
            assert preparation["allowed_operations"] == [
                "batch OpenHierarchy(exact parent)",
                "typed relative-address ID rebind",
                "two stable hierarchy observations",
                "one full read per declared Page",
            ]
    if getattr(scenario.fixture_recipe, "representation_discovery_only", False):
        assert payload["cache"]["cache_mode"] == "representation_discovery"
        assert payload["cache"]["enabled"] is False
        assert payload["cache"]["templates_opened"] is False
        if case.case_id == f"{scenario.name}.default" and not consumer_cache_required:
            expected_steps = [
                "create-notebook-bundle" if multi_role else "create-source-notebook",
            ]
            if getattr(scenario, "requires_index_activation_checkpoint", False):
                expected_steps.extend(
                    [
                        "prepare-search-fixture",
                        "activate-search-index-fixture",
                        scenario.name,
                    ]
                )
            else:
                expected_steps.append(scenario.name)
        if scenario.name.startswith("bootstrap-"):
            expected_steps.extend(
                [
                    "interactive-checkpoint",
                    (
                        "record-evidence-only-and-close"
                        if getattr(
                            scenario.fixture_recipe,
                            "representation_discovery_only",
                            False,
                        )
                        else "close-stage-publish-materialize-live-validate"
                    ),
                ]
            )
        expected_steps.extend(
            [
                "report",
                "close-notebook-bundle" if multi_role else "close-source-notebook",
            ]
        )
        assert [step["step"] for step in payload["ordered_steps"]] == expected_steps
        assert payload["ordered_steps"][0]["allowed_operations"] == [
            "create_fresh_notebook"
        ]
        expected_lifecycle_operations = ["get_exact_notebook", "close_exact_notebook"]
        if scenario.production_close_handoff:
            expected_lifecycle_operations.insert(0, "adopt_production_close")
        assert (
            payload["ordered_steps"][-1]["allowed_operations"]
            == expected_lifecycle_operations
        )
    if "--keep-worksite" in case.scenario_args:
        assert payload["worksite"] == {
            "preserved": True,
            "target_cleanup": scenario.worksite_dry_run_action,
        }
        expected_last_step = (
            "preflight-cache-required" if consumer_cache_required else "report"
        )
        assert payload["ordered_steps"][-1]["step"] == expected_last_step
    assert payload["filesystem_cleanup"]["enabled"] is False
    if case.scenario_name == "copy-page":
        cases = payload["scenario_spec"]["execution_contract"]["cases"]
        assert len(cases) == 6
        assert [item["destination_scope"] for item in cases] == [
            "same-section",
            "same-section",
            "cross-section",
            "cross-section",
            "cross-notebook",
            "cross-notebook",
        ]
        assert [item["include_descendants"] for item in cases] == [
            "omitted",
            True,
            "omitted",
            True,
            "omitted",
            True,
        ]
    assert not run_dir.exists()


def test_registered_all_case_only_builds_forced_dry_run_children(monkeypatch) -> None:
    case = next(case for case in DRY_RUN_CASES if case.scenario_name == "all")
    captured: list[list[str]] = []

    def guarded_all(args, *, scenarios):
        assert args.dry_run is True
        assert args.json_output is True
        assert tuple(scenarios) == get_all_scenario_names()
        for scenario in scenarios:
            command = all_scenarios._child_command(args, scenario)
            assert "--dry-run" in command
            assert "--json" in command
            captured.append(command)
        return 0

    monkeypatch.setattr(all_scenarios, "run_all", guarded_all)
    monkeypatch.setattr(all_scenarios.subprocess, "run", _side_effect_called)
    assert main(case.argv()) == 0
    assert [command[2] for command in captured] == list(get_all_scenario_names())


def test_case_schema_rejects_harness_owned_or_command_arguments() -> None:
    for arguments in (
        ("--dry-run",),
        ("--json",),
        ("--run-dir", "unsafe"),
        ("--notebook-label", "unsafe"),
        ("--notebook-name", "unsafe"),
        ("rename",),
        ("all",),
    ):
        with pytest.raises(ValueError):
            DryRunCase("invalid", "rename", arguments)


def test_pure_plan_module_has_no_runtime_or_side_effect_imports() -> None:
    path = Path(__file__).parents[1] / "scenarios" / "common" / "dry_run.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert not any(
        forbidden in module
        for module in imported
        for forbidden in ("mcp_stdio_client", "lifecycle", "fixture_runtime", "subprocess")
    )


def test_documented_case_blocks_are_registry_projections_only() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    pattern = re.compile(
        r"<!-- dry-run-case: (?P<key>[a-z0-9.-]+) -->\s*"
        r"```powershell\s*(?P<command>[^\r\n]+)\s*```",
        re.MULTILINE,
    )
    documented = {match.group("key"): match.group("command") for match in pattern.finditer(readme)}
    expected = {
        case.documentation_key: case.documented_command()
        for case in DRY_RUN_CASES
        if case.documentation_key is not None
    }
    assert documented == expected
