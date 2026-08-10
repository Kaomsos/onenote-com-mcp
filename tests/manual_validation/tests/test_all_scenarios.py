"""Pure subprocess-orchestration contracts for the special ``all`` entry."""

from __future__ import annotations

import argparse
from dataclasses import replace
import importlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.manual_validation import all_scenarios
from tests.manual_validation.all_scenarios import run_all
from tests.manual_validation.runner import build_parser, main
from tests.manual_validation.scenarios.base import Scenario
from tests.manual_validation.scenarios.common.registry import (
    SCENARIO_REGISTRY,
    ScenarioRegistry,
    get_all_scenario_names,
)
from tests.manual_validation.scenarios.common import specs


SCENARIO_MODULES = {
    "create": "CreateScenario",
    "rename": "RenameScenario",
    "reorder_page": "ReorderPageScenario",
    "reorder_section": "ReorderSectionScenario",
    "reorder_section_group": "ReorderSectionGroupScenario",
    "reparent_section": "ReparentSectionScenario",
    "reparent_page": "ReparentPageScenario",
    "reparent_section_group": "ReparentSectionGroupScenario",
    "delete": "DeleteScenario",
    "copy_page": "CopyPageScenario",
    "copy_section": "CopySectionScenario",
    "copy_section_group": "CopySectionGroupScenario",
    "copy_notebook": "CopyNotebookScenario",
    "move_page": "MovePageScenario",
}
SCENARIO_INFRASTRUCTURE_MODULES = {
    "__init__",
    "base",
    "copy_scenario_base",
}


def _args(**overrides):
    values = {
        "command": "all",
        "timeout": None,
        "dry_run": False,
        "json_output": False,
        "verbosity": "quiet",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _fixture_recipe(name: str):
    return SimpleNamespace(
        scenario_name=name,
        profile=specs.SCENARIO_SPECS[name].fixture,
        manifest_keys=frozenset({"content_section"}),
        validate_registration=lambda _spec: None,
    )


def test_scenarios_root_contains_only_infrastructure_or_one_scenario_class() -> None:
    scenarios_root = Path(__file__).parents[1] / "scenarios"
    root_modules = {path.stem for path in scenarios_root.glob("*.py")}

    assert root_modules == set(SCENARIO_MODULES) | SCENARIO_INFRASTRUCTURE_MODULES
    for module_name, expected_class_name in SCENARIO_MODULES.items():
        module = importlib.import_module(
            f"tests.manual_validation.scenarios.{module_name}"
        )
        defined_scenarios = [
            member
            for _, member in inspect.getmembers(module, inspect.isclass)
            if issubclass(member, Scenario)
            and member is not Scenario
            and member.__module__ == module.__name__
        ]
        assert [scenario.__name__ for scenario in defined_scenarios] == [
            expected_class_name
        ]


def test_all_parser_has_no_run_dir_or_scenario_lifecycle_options() -> None:
    parser = build_parser()
    args = parser.parse_args(["all"])

    assert args.timeout is None
    assert args.verbosity == "quiet"
    assert not hasattr(args, "run_dir")
    assert not hasattr(args, "notebook_name")
    assert not hasattr(args, "keep_notebook")
    assert not hasattr(args, "keep_worksite")
    with pytest.raises(SystemExit):
        parser.parse_args(["all", "--run-dir", "shared-run"])
    with pytest.raises(SystemExit):
        parser.parse_args(["all", "--keep-worksite"])


def test_all_rejects_non_positive_timeout_without_starting_children(capsys) -> None:
    assert main(["all", "--timeout", "0", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "error": "--timeout must be at least 1 second.",
        "exit_code": 2,
        "ok": False,
    }


def test_all_uses_only_the_explicit_test_scenario_registry(monkeypatch) -> None:
    captured: dict = {}

    def fake_all(_args, *, scenarios):
        captured["scenarios"] = scenarios
        return 0

    monkeypatch.setattr(all_scenarios, "run_all", fake_all)
    assert main(["all"]) == 0
    assert captured["scenarios"] == get_all_scenario_names()


def test_unregistered_validation_scenario_does_not_enter_all(monkeypatch) -> None:
    class ValidationProbeScenario(Scenario):
        name = "validation-probe"

    monkeypatch.setitem(
        specs.SCENARIO_SPECS,
        "validation-probe",
        replace(specs.SCENARIO_SPECS["rename"], name="validation-probe"),
    )
    ValidationProbeScenario.fixture_recipe = _fixture_recipe("validation-probe")
    registry = ScenarioRegistry()
    wrapped = registry.register(ValidationProbeScenario)

    assert wrapped is ValidationProbeScenario
    assert "validation-probe" in registry.public_names
    assert "validation-probe" not in registry.all_scenario_names
    assert set(registry.all_scenario_names) < set(registry.public_names)


def test_failed_section_group_reorder_probe_is_public_but_excluded_from_all() -> None:
    scenario = SCENARIO_REGISTRY.get("reorder-section-group")

    assert scenario.included_in_all is False
    assert "reorder-section-group" in SCENARIO_REGISTRY.public_names
    assert "reorder-section-group" not in get_all_scenario_names()
    assert scenario.capability_assessment == {
        "capability_status": "limited",
        "validation_status": "failed",
        "reason": (
            "The backend keeps SectionGroups in fixed ascending name order and "
            "did not apply the requested sibling order after UpdateHierarchy returned success."
        ),
    }


def test_typed_page_reparent_passed_but_remains_excluded_from_all() -> None:
    name = "reparent-page"
    scenario = SCENARIO_REGISTRY.get(name)

    assert scenario.included_in_all is False
    assert name in SCENARIO_REGISTRY.public_names
    assert name not in get_all_scenario_names()
    assert scenario.capability_assessment["capability_status"] == "experimental"
    assert scenario.capability_assessment["validation_status"] == "passed"


def test_typed_section_group_reparent_passed_but_remains_excluded_from_all() -> None:
    scenario = SCENARIO_REGISTRY.get("reparent-section-group")

    assert scenario.included_in_all is False
    assert "reparent-section-group" in SCENARIO_REGISTRY.public_names
    assert "reparent-section-group" not in get_all_scenario_names()
    assert scenario.capability_assessment["capability_status"] == "experimental"
    assert scenario.capability_assessment["validation_status"] == "passed"


def test_registry_wrapper_rejects_duplicate_scenario_names() -> None:
    registry = ScenarioRegistry()

    class FirstRenameScenario(Scenario):
        name = "rename"
        fixture_recipe = _fixture_recipe("rename")

    class DuplicateRenameScenario(Scenario):
        name = "rename"
        fixture_recipe = _fixture_recipe("rename")

    assert registry.register(FirstRenameScenario) is FirstRenameScenario
    with pytest.raises(ValueError, match="Duplicate scenario registration: rename"):
        registry.register(DuplicateRenameScenario)


def test_all_runs_every_scenario_serially_and_is_quiet_by_default(capsys) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        return SimpleNamespace(returncode=0, stdout=f"result for {command[2]}", stderr="")

    registered = get_all_scenario_names()
    assert run_all(_args(), scenarios=registered, run_child=fake_run) == 0

    assert [command[2] for command in commands] == list(registered)
    output = capsys.readouterr().out
    assert "[1/10] create ..." in output
    assert "PASS move-page" in output
    assert "Completed 10 scenarios: 10 passed, 0 failed" in output
    assert "result for" not in output


def test_all_passes_dry_run_timeout_and_json_to_each_child(capsys) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"ok": True, "scenario": command[2]}),
            stderr="",
        )

    args = _args(timeout=42, dry_run=True, json_output=True, verbosity="normal")
    assert run_all(args, scenarios=("create", "rename"), run_child=fake_run) == 0

    for command, scenario in zip(commands, ("create", "rename"), strict=True):
        assert command[2:] == [scenario, "--timeout", "42", "--dry-run", "--json"]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    result_events = [event for event in events if event["event"] == "scenario-output"]
    assert [event["text"]["scenario"] for event in result_events] == ["create", "rename"]
    assert events[-1]["event"] == "all-completed"


def test_all_omits_timeout_to_preserve_per_scenario_defaults() -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    assert run_all(_args(dry_run=True), scenarios=("rename",), run_child=fake_run) == 0
    assert "--timeout" not in commands[0]
    assert commands[0][-1] == "--dry-run"


def test_all_reports_failure_continues_and_returns_first_failure(capsys) -> None:
    attempted: list[str] = []

    def fake_run(command, **_kwargs):
        scenario = command[2]
        attempted.append(scenario)
        if scenario == "rename":
            return SimpleNamespace(returncode=5, stdout="invariant failed", stderr="details")
        return SimpleNamespace(returncode=0, stdout="hidden success", stderr="")

    assert run_all(
        _args(),
        scenarios=("create", "rename", "reparent-section"),
        run_child=fake_run,
    ) == 5

    assert attempted == ["create", "rename", "reparent-section"]
    output = capsys.readouterr().out
    assert "FAIL rename (exit 5" in output
    assert "stdout: invariant failed" in output
    assert "stderr: details" in output
    assert "hidden success" not in output
    assert "2 passed, 1 failed" in output


def test_normal_and_verbose_expand_success_output(capsys) -> None:
    def fake_run(_command, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="scenario result", stderr="diagnostic")

    assert run_all(
        _args(verbosity="normal"),
        scenarios=("create",),
        run_child=fake_run,
    ) == 0
    normal = capsys.readouterr().out
    assert "stdout: scenario result" in normal
    assert "stderr: diagnostic" not in normal

    assert run_all(
        _args(verbosity="verbose"),
        scenarios=("create",),
        run_child=fake_run,
    ) == 0
    verbose = capsys.readouterr().out
    assert "command:" in verbose
    assert "stdout: scenario result" in verbose
    assert "stderr: diagnostic" in verbose
