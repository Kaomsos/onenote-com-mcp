"""Pure subprocess-orchestration contracts for the special ``all`` entry."""

from __future__ import annotations

import argparse
from dataclasses import replace
import importlib
import inspect
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.manual_validation import all_scenarios
from tests.manual_validation.all_scenarios import run_all
from tests.manual_validation.runner import build_parser, main
from tests.manual_validation.runtime import ALL_CHILD_ISOLATION_PREFIX
from tests.manual_validation.scenarios.base import Scenario
from tests.manual_validation.scenarios.common.registry import (
    SCENARIO_REGISTRY,
    ScenarioRegistry,
    get_all_scenario_names,
)
from tests.manual_validation.mcp_stdio_client import (
    REPARENT_POLICY,
    RICH_COPY_NO_DELETE_POLICY,
)
from tests.manual_validation.scenarios.common import specs


@pytest.fixture(autouse=True)
def _running_onenote_gui(monkeypatch):
    monkeypatch.setattr(all_scenarios, "require_onenote_desktop", lambda: None)


SCENARIO_MODULES = {
    "create": "CreateScenario",
    "rename": "RenameScenario",
    "reorder_page": "ReorderPageScenario",
    "reorder_section": "ReorderSectionScenario",
    "reparent_section": "ReparentSectionScenario",
    "reparent_page": "ReparentPageScenario",
    "reparent_page_with_level": "ReparentPageWithLevelScenario",
    "reparent_section_group": "ReparentSectionGroupScenario",
    "delete": "DeleteScenario",
    "copy_page": "CopyPageScenario",
    "copy_section": "CopySectionScenario",
    "copy_section_group": "CopySectionGroupScenario",
    "copy_notebook": "CopyNotebookScenario",
    "copy_display_equation": "CopyDisplayEquationScenario",
    "move_page": "MovePageScenario",
    "move_section": "MoveSectionScenario",
    "move_section_group": "MoveSectionGroupScenario",
    "onenote_convergence": "OneNoteConvergenceScenario",
    "search_all_open_notebooks": "SearchAllOpenNotebooksScenario",
    "query": "QueryScenario",
    "hierarchy_navigation": "HierarchyNavigationScenario",
    "interactive_copy_inserted_file": "InteractiveCopyInsertedFileScenario",
    "interactive_copy_ink_drawing": "InteractiveCopyInkDrawingScenario",
    "interactive_copy_media_file": "InteractiveCopyMediaFileScenario",
    "interactive_copy_ui_shape": "InteractiveCopyUIShapeScenario",
    "interactive_copy_inline_equation": "InteractiveCopyInlineEquationScenario",
    "interactive_move_page": "InteractiveMovePageScenario",
    "interactive_move_page_content": "InteractiveMovePageContentScenario",
    "interactive_user_authored_fixture": "InteractiveUserAuthoredFixtureScenario",
    "cache_invalidation": "CacheInvalidationScenario",
}
SCENARIO_INFRASTRUCTURE_MODULES = {
    "__init__",
    "base",
    "copy_scenario_base",
    "container_move_scenario",
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


def test_real_all_fails_preflight_once_before_any_child(monkeypatch, capsys) -> None:
    from local_onenote_mcp.onenote_errors import OneNoteDesktopNotRunningError

    child_calls = 0

    def absent():
        raise OneNoteDesktopNotRunningError(
            "OneNote Desktop is not running with a visible GUI. Start OneNote and retry.",
            operation="health_preflight",
        )

    def forbidden_child(*_args, **_kwargs):
        nonlocal child_calls
        child_calls += 1
        raise AssertionError("all preflight must run before the first child")

    monkeypatch.setattr(all_scenarios, "require_onenote_desktop", absent)

    assert run_all(_args(), scenarios=("create", "rename"), run_child=forbidden_child) == 3
    assert child_calls == 0
    output = capsys.readouterr().out
    assert "Start OneNote and retry" in output
    assert "No scenario was started" in output


def test_all_dry_run_never_probes_desktop(monkeypatch) -> None:
    monkeypatch.setattr(
        all_scenarios,
        "require_onenote_desktop",
        lambda: (_ for _ in ()).throw(AssertionError("dry-run must not inspect GUI state")),
    )

    assert run_all(
        _args(dry_run=True),
        scenarios=("create",),
        run_child=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
    ) == 0


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


def test_all_membership_and_reviewed_capabilities_are_exact() -> None:
    assert get_all_scenario_names() == (
        "create",
        "rename",
        "reorder-page",
        "reorder-section",
        "reparent-section",
        "reparent-page",
        "reparent-page-with-level",
        "reparent-section-group",
        "delete",
        "copy-page",
        "copy-section",
        "copy-section-group",
        "copy-notebook",
        "move-page",
        "move-section",
        "move-section-group",
        "search-all-open-notebooks",
        "query",
    )

    for name in (
        "reparent-page",
        "reparent-page-with-level",
        "reparent-section-group",
    ):
        assessment = SCENARIO_REGISTRY.get(name).capability_assessment
        assert assessment["capability_status"] == "experimental"
        assert assessment["validation_status"] == "passed"


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


def test_registry_rejects_fixture_policy_without_required_local_file_gate(
    monkeypatch,
) -> None:
    name = "policy-closure-probe"
    bad_spec = replace(
        specs.SCENARIO_SPECS["reparent-page"],
        name=name,
        policy=REPARENT_POLICY,
    )
    monkeypatch.setitem(specs.SCENARIO_SPECS, name, bad_spec)

    class PolicyClosureProbeScenario(Scenario):
        pass

    PolicyClosureProbeScenario.name = name
    PolicyClosureProbeScenario.fixture_recipe = SimpleNamespace(
        scenario_name=name,
        profile=bad_spec.fixture,
        manifest_keys=frozenset(bad_spec.fixture.manifest_keys),
        notebook_roles=(),
        consumer_scenario=False,
        validate_registration=lambda _spec: None,
    )

    with pytest.raises(
        ValueError,
        match="fixture policy is missing required gates: local_file_io_enabled",
    ):
        ScenarioRegistry().register(PolicyClosureProbeScenario)


def test_registry_rejects_allowed_close_without_notebook_lifecycle_gate(
    monkeypatch,
) -> None:
    name = "scenario-policy-closure-probe"
    bad_spec = replace(
        specs.SCENARIO_SPECS["copy-notebook"],
        name=name,
        policy=RICH_COPY_NO_DELETE_POLICY,
    )
    monkeypatch.setitem(specs.SCENARIO_SPECS, name, bad_spec)

    class ScenarioPolicyClosureProbe(Scenario):
        pass

    ScenarioPolicyClosureProbe.name = name
    ScenarioPolicyClosureProbe.fixture_recipe = SimpleNamespace(
        scenario_name=name,
        profile=bad_spec.fixture,
        manifest_keys=frozenset(bad_spec.fixture.manifest_keys),
        notebook_roles=(),
        consumer_scenario=False,
        validate_registration=lambda _spec: None,
    )

    with pytest.raises(
        ValueError,
        match="scenario-policy-closure-probe policy is missing required gates: notebook_lifecycle_enabled",
    ):
        ScenarioRegistry().register(ScenarioPolicyClosureProbe)


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
    assert "[1/18] create ..." in output
    assert "PASS move-page" in output
    assert "PASS move-section" in output
    assert "PASS move-section-group" in output
    assert "PASS search-all-open-notebooks" in output
    assert "PASS query" in output
    assert "Completed 18 scenarios: 18 passed, 0 failed" in output
    assert "result for" not in output


def test_non_json_all_streams_child_stdout_before_pass_and_prefixes_scenario(
    capsys,
) -> None:
    commands: list[list[str]] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = StringIO(
                "[run] create\n[1/5] notebook ...\n[2/5] fixture ...\n"
            )
            self.stderr = StringIO("hidden success diagnostic\n")

        def wait(self) -> int:
            return 0

    def fake_start(command, **kwargs):
        commands.append(command)
        assert kwargs == {
            "stdout": all_scenarios.subprocess.PIPE,
            "stderr": all_scenarios.subprocess.PIPE,
            "text": True,
            "bufsize": 1,
        }
        return FakeProcess()

    assert run_all(
        _args(verbosity="normal"),
        scenarios=("create",),
        start_child=fake_start,
    ) == 0

    output = capsys.readouterr().out
    assert commands[0][-2:] == ["--verbosity", "normal"]
    assert "  create | [run] create" in output
    assert "  create | [1/5] notebook ..." in output
    assert "hidden success diagnostic" not in output
    assert output.index("create | [2/5] fixture") < output.index("PASS create")


def test_verbose_all_streams_child_stderr_without_replaying_it(capsys) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = StringIO("live stdout\n")
            self.stderr = StringIO("live stderr\n")

        def wait(self) -> int:
            return 0

    assert run_all(
        _args(verbosity="verbose"),
        scenarios=("create",),
        start_child=lambda _command, **_kwargs: FakeProcess(),
    ) == 0

    output = capsys.readouterr().out
    assert output.count("create | live stdout") == 1
    assert output.count("create | live stderr") == 1
    assert output.index("create | live stdout") < output.index("PASS create")


def test_streaming_all_defers_bounded_stderr_until_failure(capsys) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = StringIO("live progress\n")
            self.stderr = StringIO("failure diagnostic\n")

        def wait(self) -> int:
            return 5

    assert run_all(
        _args(verbosity="normal"),
        scenarios=("rename",),
        start_child=lambda _command, **_kwargs: FakeProcess(),
    ) == 5

    output = capsys.readouterr().out
    assert output.index("rename | live progress") < output.index("FAIL rename")
    assert output.index("FAIL rename") < output.index("stderr: failure diagnostic")


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
        assert command[2:] == [
            scenario,
            "--timeout",
            "42",
            "--dry-run",
            "--json",
            "--verbosity",
            "normal",
        ]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    result_events = [event for event in events if event["event"] == "scenario-output"]
    assert [event["text"]["scenario"] for event in result_events] == ["create", "rename"]
    assert events[-1]["event"] == "all-completed"


def test_all_passes_use_cache_to_each_independent_child() -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    assert run_all(
        _args(dry_run=True, use_cache=True),
        scenarios=("create", "copy-page"),
        run_child=fake_run,
    ) == 0
    assert all("--dry-run" in command and "--use-cache" in command for command in commands)
    assert all(command[-2:] == ["--verbosity", "quiet"] for command in commands)


def test_all_omits_timeout_to_preserve_per_scenario_defaults() -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    assert run_all(_args(dry_run=True), scenarios=("rename",), run_child=fake_run) == 0
    assert "--timeout" not in commands[0]
    assert "--dry-run" in commands[0]
    assert commands[0][-2:] == ["--verbosity", "quiet"]


def test_real_all_stops_when_failed_child_does_not_prove_isolation(capsys) -> None:
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

    assert attempted == ["create", "rename"]
    output = capsys.readouterr().out
    assert "FAIL rename (exit 5" in output
    assert "stdout: invariant failed" in output
    assert "stderr: details" in output
    assert "hidden success" not in output
    assert "Stopped after 2/3 scenarios: 1 passed, 1 failed, 1 not started" in output
    assert "validated cache templates remain reusable" in output


def test_real_all_continues_after_proven_failure_isolation(capsys) -> None:
    attempted: list[str] = []

    def fake_run(command, **_kwargs):
        scenario = command[2]
        attempted.append(scenario)
        assert "--all-child" in command
        if scenario == "rename":
            marker = ALL_CHILD_ISOLATION_PREFIX + json.dumps(
                {"passed": True, "status": "closed"}
            )
            return SimpleNamespace(
                returncode=5,
                stdout="invariant failed",
                stderr=marker + "\n",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    assert run_all(
        _args(),
        scenarios=("create", "rename", "reparent-section"),
        run_child=fake_run,
    ) == 5

    assert attempted == ["create", "rename", "reparent-section"]
    output = capsys.readouterr().out
    assert "isolated rename: exact run Notebook bundle closed; continuing" in output
    assert "Completed 3 scenarios: 2 passed, 1 failed" in output
    assert ALL_CHILD_ISOLATION_PREFIX not in output


def test_real_all_stops_when_failure_isolation_reports_close_failure(capsys) -> None:
    attempted: list[str] = []

    def fake_run(command, **_kwargs):
        attempted.append(command[2])
        marker = ALL_CHILD_ISOLATION_PREFIX + json.dumps(
            {"passed": False, "status": "close_failed"}
        )
        return SimpleNamespace(returncode=4, stdout="", stderr=marker + "\n")

    assert run_all(
        _args(),
        scenarios=("rename", "reparent-section"),
        run_child=fake_run,
    ) == 4

    assert attempted == ["rename"]
    output = capsys.readouterr().out
    assert "exact failure isolation could not be proven" in output
    assert "isolated rename" not in output


def test_dry_run_all_still_checks_every_plan_after_a_failure(capsys) -> None:
    attempted: list[str] = []

    def fake_run(command, **_kwargs):
        scenario = command[2]
        attempted.append(scenario)
        return SimpleNamespace(
            returncode=5 if scenario == "rename" else 0,
            stdout="",
            stderr="",
        )

    assert run_all(
        _args(dry_run=True),
        scenarios=("create", "rename", "reparent-section"),
        run_child=fake_run,
    ) == 5

    assert attempted == ["create", "rename", "reparent-section"]
    assert "Completed 3 scenarios: 2 passed, 1 failed" in capsys.readouterr().out


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


def test_non_json_child_diagnostics_are_bounded(capsys) -> None:
    large = "\n".join(f"line-{index}-" + "x" * 200 for index in range(1_000))

    def fake_run(_command, **_kwargs):
        return SimpleNamespace(returncode=5, stdout=large, stderr=large)

    assert run_all(
        _args(verbosity="quiet"),
        scenarios=("rename",),
        run_child=fake_run,
    ) == 5
    output = capsys.readouterr().out
    assert output.count("output truncated") == 2
    assert len(output) < 10_000
