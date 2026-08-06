"""Pure contracts for the human-gated, scenario-scoped validation runner."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re

import pytest

from tests.manual_validation import runtime, test_utils
from tests.manual_validation.mcp_stdio_client import ClientFailure
from tests.manual_validation.runner import build_parser, main
from tests.manual_validation.runtime import RuntimeOptions
from tests.manual_validation.scenarios.common import orchestrator as validation
from tests.manual_validation.scenarios.common import fixtures as fixture_module
from tests.manual_validation.scenarios.common.fixtures import _validate_fixture_snapshot
from tests.manual_validation.scenarios.base import Scenario
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.common.specs import SCENARIO_SPECS


SCENARIOS = validation.PUBLIC_SCENARIOS


def test_public_scenarios_are_class_managed_and_spec_backed() -> None:
    assert SCENARIO_REGISTRY.public_names == SCENARIOS
    assert all(isinstance(scenario, Scenario) for scenario in SCENARIO_REGISTRY.values())
    assert [scenario.spec.name for scenario in SCENARIO_REGISTRY.values()] == list(SCENARIOS)


def _args(run_dir: Path, scenario: str, *, keep: bool = False) -> argparse.Namespace:
    values = {
        "command": scenario,
        "scenario": scenario,
        "notebook_name": "__ISOLATED__",
        "run_dir": run_dir,
        "timeout": 1_800,
        "dry_run": False,
        "json_output": False,
        "keep_notebook": keep,
    }
    if scenario == "rename":
        values.update(target="move_source", new_name=None)
    if scenario == "reorder":
        values["page_level"] = 2
    return argparse.Namespace(**values)


def _manifest(run_dir: Path, name: str = "__ISOLATED__") -> dict:
    source = (run_dir / "notebooks" / name).resolve()
    return {
        "schema_version": 1,
        "notebook": {"id": "notebook-id", "name": name},
        "structure": {
            "group_a": {"id": "group-a"},
            "group_b": {"id": "group-b"},
            "move_source": {"id": "move-source"},
            "parent_page": {"id": "parent-page"},
            "sibling_page": {"id": "sibling-page"},
            "disposable_group": {"id": "disposable-group"},
            "disposable_page": {"id": "disposable-page"},
        },
        "disposable_targets": {"source_notebook_path": str(source)},
    }


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_each_dry_run_declares_one_process_and_scenario_fixture(
    scenario, capsys, tmp_path
) -> None:
    run_dir = tmp_path / scenario
    assert main([scenario, "--run-dir", str(run_dir), "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == scenario
    assert payload["server_started"] is False
    assert payload["agent_execution_prohibited"] is True
    assert payload["expected_mcp_process_starts"] == 1
    assert payload["fixture_profile"]["name"] == SCENARIO_SPECS[scenario].fixture.name
    assert payload["scenario_spec"]["tool_allowlist"] == sorted(
        SCENARIO_SPECS[scenario].tool_allowlist
    )
    assert [step["step"] for step in payload["ordered_steps"]] == [
        "create-source-notebook",
        scenario,
        "report",
        "close-source-notebook",
    ]
    assert payload["ordered_steps"][0]["allowed_operations"] == [
        "create_fresh_notebook"
    ]
    assert payload["ordered_steps"][-1]["allowed_operations"] == [
        "get_exact_notebook",
        "close_exact_notebook",
    ]
    assert payload["filesystem_cleanup"]["enabled"] is False
    assert not run_dir.exists()


def test_fixture_profiles_are_scenario_specific() -> None:
    names = {name: spec.fixture.name for name, spec in SCENARIO_SPECS.items()}
    assert names["create"] == "full-preset"
    assert names["rename"] == "rename-target"
    assert names["move"] == "section-move"
    assert names["copy-page"] == "rich-page-copy"
    assert len(set(names.values())) == len(names)
    assert "create_notebook" not in SCENARIO_SPECS["create"].tool_allowlist
    assert "move_section" not in SCENARIO_SPECS["rename"].tool_allowlist
    assert "delete_section_group" not in SCENARIO_SPECS["move"].tool_allowlist


def test_call_metrics_count_only_run_scoped_audit_lines(tmp_path) -> None:
    scenario = tmp_path / "scenario-mcp"
    scenario.mkdir()
    (scenario / "bridge-calls.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    (scenario / "calls.jsonl").write_text("{}\n{}\n{}\n", encoding="utf-8")
    (tmp_path / "lifecycle-bridge-calls.jsonl").write_text("{}\n", encoding="utf-8")
    metrics: dict = {}

    validation._refresh_call_metrics(metrics, tmp_path)

    assert metrics["observed_bridge_calls"] == {
        "scenario_mcp": 2,
        "lifecycle_wrapper": 1,
        "total": 3,
    }
    assert metrics["observed_mcp_tool_calls"] == 3


def test_fixture_validator_proves_page_tree_topology() -> None:
    structure = {
        "move_source": {"id": "section"},
        "parent_page": {"id": "parent"},
        "child_page": {"id": "child"},
        "sibling_page": {"id": "sibling"},
    }
    items = [
        {"id": "section", "resource_type": "section"},
        {
            "id": "parent",
            "resource_type": "page",
            "section_id": "section",
            "page_level": 1,
            "parent_page_id": None,
        },
        {
            "id": "child",
            "resource_type": "page",
            "section_id": "section",
            "page_level": 2,
            "parent_page_id": "parent",
        },
        {
            "id": "sibling",
            "resource_type": "page",
            "section_id": "section",
            "page_level": 1,
            "parent_page_id": None,
        },
    ]

    checks = _validate_fixture_snapshot("reorder", {"items": items}, structure, None)
    assert "Page levels and derived parent relationships match the profile" in checks

    items[2]["parent_page_id"] = "wrong"
    with pytest.raises(runtime.InvariantFailure, match="topology"):
        _validate_fixture_snapshot("reorder", {"items": items}, structure, None)


def test_fixture_validator_rejects_delete_target_outside_sandbox() -> None:
    structure = {
        "delete_sandbox": {"id": "sandbox"},
        "disposable_group": {"id": "target"},
    }
    snapshot = {
        "items": [
            {"id": "sandbox", "resource_type": "section_group"},
            {"id": "target", "resource_type": "section_group", "parent_id": "other"},
        ]
    }
    with pytest.raises(runtime.InvariantFailure, match="Delete-Sandbox"):
        _validate_fixture_snapshot("delete", snapshot, structure, None)


def test_fixture_validation_failure_persists_manifest_and_snapshot(monkeypatch, tmp_path) -> None:
    created = iter(
        [
            {"id": "sandbox", "name": "Delete-Sandbox"},
            {"id": "target", "name": "Disposable-Group"},
        ]
    )

    async def fake_group(*_args, **_kwargs):
        return next(created)

    async def fake_snapshot(*_args, **_kwargs):
        return {
            "items": [
                {"id": "sandbox", "resource_type": "section_group"},
                {
                    "id": "target",
                    "resource_type": "section_group",
                    "parent_id": "wrong",
                },
            ],
            "page_hashes": {},
        }

    monkeypatch.setattr(fixture_module, "ensure_group", fake_group)
    monkeypatch.setattr(fixture_module, "capture_snapshot", fake_snapshot)
    args = argparse.Namespace(scenario="delete")
    options = RuntimeOptions(tmp_path, 180, False, False)

    with pytest.raises(runtime.InvariantFailure, match="Delete-Sandbox"):
        asyncio.run(
            fixture_module.prepare_scenario_fixture(
                args,
                options,
                object(),
                {"id": "notebook", "name": "Notebook"},
                str(tmp_path / "notebooks" / "Notebook"),
                SCENARIO_SPECS["delete"],
            )
        )

    assert test_utils.read_json(tmp_path / "manifest.json")["fixture_validation"]["status"] == "failed"
    assert test_utils.read_json(tmp_path / "fixture-result.json")["validation"]["passed"] is False
    assert (tmp_path / "prepared.json").exists()


def test_default_identity_uses_one_timestamp(capsys) -> None:
    assert main(["rename", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    match = re.fullmatch(
        r"__LOCAL_MCP_TEST_ISOLATED__(\d{8}T\d{6}Z)", payload["notebook_name"]
    )
    assert match is not None
    assert Path(payload["run_dir"]).name == f"run-{match.group(1)}"


def test_keep_dry_run_omits_close(capsys, tmp_path) -> None:
    run_dir = tmp_path / "run"
    assert main(
        [
            "move",
            "--notebook-name",
            "__CUSTOM__",
            "--run-dir",
            str(run_dir),
            "--keep-notebook",
            "--dry-run",
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lifecycle"] == "keep"
    assert [step["step"] for step in payload["ordered_steps"]] == [
        "create-source-notebook",
        "move",
        "report",
    ]
    assert not run_dir.exists()


def test_cli_exposes_flat_scenarios_and_special_all_entry() -> None:
    parser = build_parser()
    choices = next(
        action.choices
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert set(choices) == {*SCENARIOS, "all"}
    for removed in ("validate", "inspect", "read", "baseline", "report", "suite"):
        assert removed not in choices


class FakeLifecycle:
    instances: list["FakeLifecycle"] = []

    def __init__(self, run_dir: Path, *, timeout_seconds: int) -> None:
        self.run_dir = run_dir
        self.timeout_seconds = timeout_seconds
        self.lease_path = run_dir / "lifecycle-lease.json"
        self.closed = False
        self.preserved = False
        self.__class__.instances.append(self)

    def create_fresh_notebook(self, name: str):
        path = (self.run_dir / "notebooks" / name).resolve()
        path.mkdir(parents=True)
        lease = {
            "notebook_id": "notebook-id",
            "expected_name": name,
            "expected_local_path": str(path),
        }
        test_utils.write_json(self.lease_path, {"schema_version": 1, **lease})
        return {"id": "notebook-id", "name": name}, lease

    def get_exact_notebook(self, lease=None):
        lease = lease or test_utils.read_json(self.lease_path)
        return {"id": lease["notebook_id"], "name": lease["expected_name"]}

    def close_exact_notebook(self):
        self.closed = True
        return {"closed": True, "close_before": {"id": "notebook-id"}}

class FakeMCP:
    starts = 0
    active: "FakeMCP | None" = None

    def __init__(self, **kwargs) -> None:
        self.policy = kwargs["policy"]
        self.allowed_tools = set(kwargs["allowed_tools"]) | {"health_check"}
        self.timeout_seconds = kwargs["timeout_seconds"]

    async def __aenter__(self):
        self.__class__.starts += 1
        self.__class__.active = self
        return self

    async def __aexit__(self, *_args):
        self.__class__.active = None


def _install_orchestration_fakes(monkeypatch, calls: list[str]) -> None:
    FakeLifecycle.instances.clear()
    FakeMCP.starts = 0
    monkeypatch.setattr(validation, "NotebookLifecycleWrapper", FakeLifecycle)
    monkeypatch.setattr(validation, "MCPStdioClient", FakeMCP)

    async def fake_fixture(args, options, client, _notebook, _path, spec):
        assert client is FakeMCP.active
        assert spec is SCENARIO_SPECS[args.scenario]
        calls.append("fixture")
        manifest = _manifest(options.run_dir, args.notebook_name)
        test_utils.write_json(options.run_dir / "manifest.json", manifest)
        return manifest, {"profile": spec.fixture.name}

    def fake_report(run_dir):
        calls.append("report")
        return run_dir / "report.md"

    monkeypatch.setattr(validation, "prepare_scenario_fixture", fake_fixture)
    monkeypatch.setattr(validation, "render_report", fake_report)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_each_scenario_uses_exactly_one_mcp_process(monkeypatch, tmp_path, scenario) -> None:
    calls: list[str] = []
    _install_orchestration_fakes(monkeypatch, calls)

    if scenario != "create":
        async def fake_scenario(
            args,
            _options,
            _manifest_value,
            *,
            client=None,
            fixture_result=None,
        ):
            assert client is FakeMCP.active
            calls.append(args.scenario)
            if args.scenario == "delete":
                assert args.delete_target_id == "disposable-group"
            return {"scenario": args.scenario, "status": "passed"}

        monkeypatch.setattr(SCENARIO_REGISTRY.get(scenario), "execute", fake_scenario)

    result = asyncio.run(
        validation.run_validate(
            _args(tmp_path / scenario, scenario),
            RuntimeOptions(tmp_path / scenario, 1_800, False, False),
        )
    )

    assert FakeMCP.starts == 1
    assert calls[0] == "fixture"
    if scenario != "create":
        assert calls[1] == scenario
    assert FakeLifecycle.instances[0].closed is True
    assert result["metrics"]["observed_mcp_process_starts"] == 1
    assert result["ordered_steps"] == [
        "create-source-notebook",
        scenario,
        "report",
        "close-source-notebook",
    ]


def test_failure_preserves_open_and_stops_before_report(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _install_orchestration_fakes(monkeypatch, calls)

    async def failing(*_args, **_kwargs):
        calls.append("rename")
        raise runtime.InvariantFailure("scenario mismatch")

    monkeypatch.setattr(SCENARIO_REGISTRY.get("rename"), "execute", failing)
    args = _args(tmp_path / "run", "rename")
    with pytest.raises(runtime.InvariantFailure):
        asyncio.run(validation.run_validate(args, RuntimeOptions(args.run_dir, 180, False, False)))
    assert calls == ["fixture", "rename"]
    assert FakeLifecycle.instances[0].closed is False
    state = test_utils.read_json(args.run_dir / "run-state.json")
    assert state["current_step"] == "rename"
    assert state["finalization_started"] is False


def test_restore_failure_never_enters_source_finalization(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _install_orchestration_fakes(monkeypatch, calls)

    async def restore_failed(*_args, **_kwargs):
        raise runtime.RestoreFailure("restored snapshot mismatch")

    monkeypatch.setattr(SCENARIO_REGISTRY.get("rename"), "execute", restore_failed)
    args = _args(tmp_path / "run", "rename")
    with pytest.raises(runtime.RestoreFailure, match="snapshot mismatch"):
        asyncio.run(validation.run_validate(args, RuntimeOptions(args.run_dir, 180, False, False)))

    assert FakeLifecycle.instances[0].closed is False
    state = test_utils.read_json(args.run_dir / "run-state.json")
    assert state["finalization_started"] is False


def test_copy_only_records_cleanup_and_never_closes(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _install_orchestration_fakes(monkeypatch, calls)

    async def copy_only(
        args,
        options,
        _manifest_value,
        *,
        client=None,
        fixture_result=None,
    ):
        assert client is FakeMCP.active
        partial = {
            "outcome": "copy_only",
            "created_ids": ["copied-page"],
            "id_map": {"disposable-page": "copied-page"},
        }
        test_utils.write_json(
            test_utils.scenario_dir(options.run_dir, args.scenario) / "copy-result.json",
            partial,
        )
        raise ClientFailure("copy_only", envelope=partial)

    monkeypatch.setattr(
        SCENARIO_REGISTRY.get("reconstructive-move-page"), "execute", copy_only
    )
    args = _args(tmp_path / "run", "reconstructive-move-page")
    with pytest.raises(ClientFailure, match="copy_only"):
        asyncio.run(validation.run_validate(args, RuntimeOptions(args.run_dir, 1_800, False, False)))
    validation.record_failure(args, "copy_only", runtime.EXIT_MCP)

    assert FakeLifecycle.instances[0].closed is False
    failure = test_utils.read_json(
        args.run_dir / "scenarios" / args.scenario / "failure.json"
    )
    assert failure["status"] == "needs_manual_cleanup"
    assert failure["created_ids"] == ["copied-page"]
    state = test_utils.read_json(args.run_dir / "run-state.json")
    assert state["status"] == "failed_preserved_open"
    assert state["failed_step"] == "reconstructive-move-page"


def test_finalize_uses_lifecycle_lease_and_never_starts_mcp(tmp_path) -> None:
    run_dir = tmp_path / "run"
    wrapper = FakeLifecycle(run_dir, timeout_seconds=180)
    _notebook, _lease = wrapper.create_fresh_notebook("__ISOLATED__")
    manifest = _manifest(run_dir)
    result = asyncio.run(
        validation.finalize_notebook(
            _args(run_dir, "move"),
            RuntimeOptions(run_dir, 180, False, False),
            manifest,
            wrapper=wrapper,
        )
    )
    assert wrapper.closed is True
    assert result["status"] == "closed_preserved"
    assert Path(manifest["disposable_targets"]["source_notebook_path"]).exists()


def test_copy_notebook_finalization_closes_source_lease_and_preserves_both_paths(tmp_path) -> None:
    run_dir = tmp_path / "run"
    wrapper = FakeLifecycle(run_dir, timeout_seconds=180)
    wrapper.create_fresh_notebook("__ISOLATED__")
    manifest = _manifest(run_dir)
    copy_path = (run_dir / "notebook-copies" / "Copy").resolve()
    copy_path.mkdir(parents=True)
    test_utils.write_json(
        test_utils.scenario_dir(run_dir, "copy-notebook") / "restored.json",
        {"target_path": str(copy_path)},
    )

    result = asyncio.run(
        validation.finalize_notebook(
            _args(run_dir, "copy-notebook"),
            RuntimeOptions(run_dir, 180, False, False),
            manifest,
            wrapper=wrapper,
        )
    )

    assert wrapper.closed is True
    assert result["source_notebook_id"] == "notebook-id"
    assert set(result["preserved_paths"]) == {
        manifest["disposable_targets"]["source_notebook_path"],
        str(copy_path),
    }
    assert copy_path.exists()
    assert "close_notebook" in SCENARIO_SPECS["copy-notebook"].tool_allowlist
    assert "delete_section" not in SCENARIO_SPECS["copy-notebook"].tool_allowlist


def test_keep_validates_lease_but_does_not_close(tmp_path) -> None:
    run_dir = tmp_path / "run"
    wrapper = FakeLifecycle(run_dir, timeout_seconds=180)
    wrapper.create_fresh_notebook("__ISOLATED__")
    result = asyncio.run(
        validation.finalize_notebook(
            _args(run_dir, "move", keep=True),
            RuntimeOptions(run_dir, 180, False, False),
            _manifest(run_dir),
            wrapper=wrapper,
        )
    )
    assert wrapper.closed is False
    assert result["status"] == "preserved_open"


def test_nonempty_run_dir_and_unsafe_name_fail_without_mutation(tmp_path, capsys) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "existing.txt").write_text("preserve", encoding="utf-8")
    assert main(["rename", "--notebook-name", "__ISOLATED__", "--run-dir", str(run_dir), "--json"]) == 2
    assert "absent or empty" in capsys.readouterr().out
    assert sorted(path.name for path in run_dir.iterdir()) == ["existing.txt"]

    unsafe = tmp_path / "unsafe"
    assert main(["rename", "--notebook-name", "unsafe/name", "--run-dir", str(unsafe), "--dry-run", "--json"]) == 2
    assert "Windows-safe leaf name" in capsys.readouterr().out
    assert not unsafe.exists()


def test_existing_empty_run_dir_is_accepted(tmp_path) -> None:
    path = tmp_path / "empty"
    path.mkdir()
    validation._assert_fresh_run_dir(path)
