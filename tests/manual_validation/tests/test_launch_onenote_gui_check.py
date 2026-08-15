"""Pure contracts for the standalone GUI launch acceptance entry."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.manual_validation.launch_onenote_gui_check import (
    COMMAND,
    LaunchCheckFailure,
    build_plan,
    run_real_check,
)
from tests.manual_validation.mcp_stdio_client import ClientFailure
from tests.manual_validation.progress import RunProgressReporter
from tests.manual_validation.run_identity import new_run_identity
from tests.manual_validation.runner import build_parser
from tests.manual_validation.test_utils import read_json


def _identity():
    return new_run_identity(datetime(2026, 8, 15, 16, 0, tzinfo=timezone.utc))


def _health(*, ready: bool, policy: dict) -> dict:
    return {
        "onenote_desktop": {
            "process_running": ready,
            "visible_window_present": ready,
            "ready": ready,
            "probe": "native_windows_process_and_visible_window",
        },
        "mutation_policy": policy,
    }


class _FakeClient:
    def __init__(self, owner, **kwargs) -> None:
        self.owner = owner
        self.policy = kwargs["policy"]
        self.require_desktop_ready = kwargs["require_desktop_ready"]
        self.persist_runtime_logs = kwargs["persist_runtime_logs"]
        self.enabled = self.policy.ui_control_enabled
        self.health_result = _health(ready=False, policy=self.policy.as_dict())

    async def __aenter__(self):
        self.owner.clients.append(self)
        return self

    async def __aexit__(self, *_args):
        return None

    async def call_tool(self, name, _arguments, *, retry_read=False):
        self.owner.calls.append((self.enabled, name, retry_read))
        if not self.enabled:
            if name == "launch_onenote_gui":
                raise ClientFailure(
                    "policy disabled",
                    envelope={
                        "ok": False,
                        "error": {"code": "policy_disabled", "message": "disabled"},
                        "execution": {
                            "operation": "launch_onenote_gui",
                            "stage": "authorization",
                            "backend_calls": 0,
                        },
                    },
                )
            if name == "health_check":
                return _health(ready=False, policy=self.policy.as_dict())
        if name == "launch_onenote_gui":
            self.owner.launch_calls += 1
            if self.owner.launch_calls == 1:
                return {
                    "status": "started",
                    "launch_attempted": True,
                    "launch_attempts": 1,
                    "ready": True,
                }
            return {
                "status": "already_running",
                "launch_attempted": False,
                "launch_attempts": 0,
                "ready": True,
            }
        if name == "health_check":
            return _health(ready=True, policy=self.policy.as_dict())
        if name == "list_notebooks":
            return {
                "items": [{"id": "notebook-id", "resource_type": "notebook"}],
                "count": 1,
                "execution": {"operation": "list_notebooks"},
            }
        raise AssertionError(name)

    async def call_health_preflight(self, *, allow_desktop_not_running):
        if self.enabled and self.owner.launch_calls:
            return _health(ready=True, policy=self.policy.as_dict())
        assert allow_desktop_not_running is True
        return _health(ready=False, policy=self.policy.as_dict())

    def validate_health_contract(self, health, *, require_desktop_ready):
        assert require_desktop_ready is True
        assert health["onenote_desktop"]["ready"] is True
        assert health["mutation_policy"] == self.policy.as_dict()


class _FakeClientFactory:
    def __init__(self) -> None:
        self.clients = []
        self.calls = []
        self.launch_calls = 0

    def __call__(self, **kwargs):
        return _FakeClient(self, **kwargs)


def _accept_exact_prompt(prompt: str) -> str:
    return prompt.rsplit("Type exactly: ", 1)[1]


def test_plan_is_explicitly_outside_scenario_and_all(tmp_path) -> None:
    plan = build_plan(_identity(), tmp_path / "run-2026-08-16-00-00-00", 30)
    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    from tests.manual_validation.scenarios.common.registry import get_all_scenario_names

    assert COMMAND not in subparsers.choices
    assert COMMAND not in get_all_scenario_names()
    assert plan["command"] == COMMAND
    assert plan["registered_scenario"] is False
    assert plan["included_in_all"] is False
    assert plan["human_only"] is True
    assert plan["agent_execution_prohibited"] is True
    assert plan["mcp_runtime_logging"] == {
        "bridge_audit_file": False,
        "calls_jsonl": False,
        "server_stderr_file": False,
        "structured_acceptance_evidence": True,
        "terminal_output": True,
    }
    assert plan["side_effects"] == {
        "starts_onenote": True,
        "closes_onenote": False,
        "creates_notebook": False,
        "mutates_notebook_content": False,
        "leaves_onenote_running": True,
    }
    assert [item["policy"]["ui_control_enabled"] for item in plan["mcp_processes"]] == [
        False,
        True,
    ]


def test_real_check_rejects_noninteractive_execution_before_writes(tmp_path) -> None:
    run_dir = tmp_path / "run-2026-08-16-00-00-00"

    with pytest.raises(LaunchCheckFailure, match="interactive foreground terminal"):
        run_real_check(
            identity=_identity(),
            run_dir=run_dir,
            timeout=30,
            terminal_check=lambda: False,
            confirmation_reader=lambda _prompt: pytest.fail("must not prompt"),
        )

    assert not run_dir.exists()


def test_real_check_runs_two_frozen_profiles_and_persists_pass(tmp_path) -> None:
    run_dir = tmp_path / "run-2026-08-16-00-00-00"
    factory = _FakeClientFactory()
    terminal_lines: list[str] = []

    result = run_real_check(
        identity=_identity(),
        run_dir=run_dir,
        timeout=30,
        client_factory=factory,
        confirmation_reader=_accept_exact_prompt,
        terminal_check=lambda: True,
        progress=RunProgressReporter("normal", writer=terminal_lines.append),
    )

    assert result["status"] == "passed"
    assert result["mcp_processes_started"] == 2
    assert result["checks"] == {
        "health_check_did_not_launch": True,
        "unauthorized_rejection_before_backend": True,
        "authorized_single_launch": True,
        "second_call_idempotent": True,
        "post_launch_health_ready": True,
        "post_launch_hierarchy_read": True,
        "human_single_gui_verdict": True,
    }
    assert [client.enabled for client in factory.clients] == [False, True]
    assert all(client.require_desktop_ready is False for client in factory.clients)
    assert all(client.persist_runtime_logs is False for client in factory.clients)
    assert factory.launch_calls == 2
    assert read_json(run_dir / "run-state.json")["status"] == "passed"
    persisted_result = read_json(run_dir / "run-result.json")
    assert persisted_result["onenote_left_running"] is True
    assert persisted_result["mcp_runtime_logs_persisted"] is False
    assert persisted_result["mcp_runtime_logs_streamed_to_terminal"] is True
    assert "runtime_logs_persisted" not in persisted_result
    assert "runtime_logs_streamed_to_terminal" not in persisted_result
    assert not (run_dir / "run-failure.json").exists()
    assert any("[1/4] UI Control disabled proof" in line for line in terminal_lines)
    assert any("[4/4] human single-GUI verdict" in line for line in terminal_lines)
    assert not list(run_dir.rglob("calls.jsonl"))
    assert not list(run_dir.rglob("bridge-calls.jsonl"))
    assert not list(run_dir.rglob("server.stderr.log"))


def test_missing_human_gui_verdict_preserves_failure_evidence(tmp_path) -> None:
    run_dir = tmp_path / "run-2026-08-16-00-00-00"
    factory = _FakeClientFactory()
    prompts = 0

    def reader(prompt: str) -> str:
        nonlocal prompts
        prompts += 1
        if prompts == 1:
            return _accept_exact_prompt(prompt)
        return "REJECT"

    with pytest.raises(LaunchCheckFailure, match="GUI verdict was not accepted"):
        run_real_check(
            identity=_identity(),
            run_dir=run_dir,
            timeout=30,
            client_factory=factory,
            confirmation_reader=reader,
            terminal_check=lambda: True,
        )

    failure = read_json(run_dir / "run-failure.json")
    assert failure["status"] == "failed_preserved"
    assert failure["onenote_may_be_running"] is True
    assert read_json(run_dir / "run-state.json")["status"] == "failed_preserved"
    assert (run_dir / "authorized-launch.json").is_file()
