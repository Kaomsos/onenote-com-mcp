"""Pure contracts for the standalone GUI launch acceptance entry."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.manual_validation.launch_onenote_gui_check import (
    COMMAND,
    REFRESH_REPEAT_COUNT,
    LaunchCheckFailure,
    UI_CONTROL_POLICY,
    build_plan,
    run_real_check,
)
from tests.manual_validation.onenote_exit_wait import NATIVE_DESKTOP_PROBE
from tests.manual_validation.mcp_stdio_client import ClientFailure
from tests.manual_validation.progress import RunProgressReporter
from tests.manual_validation.run_identity import new_run_identity
from tests.manual_validation.runner import build_parser
from tests.manual_validation.test_utils import read_json


def _identity():
    return new_run_identity(datetime(2026, 8, 15, 16, 0, tzinfo=timezone.utc))


def _health(
    *,
    ready: bool | None = None,
    policy: dict,
    process_running: bool | None = None,
    visible_window_present: bool | None = None,
) -> dict:
    if process_running is None:
        process_running = bool(ready)
    if visible_window_present is None:
        visible_window_present = bool(ready)
    desktop_ready = process_running and visible_window_present
    return {
        "onenote_desktop": {
            "process_running": process_running,
            "visible_window_present": visible_window_present,
            "ready": desktop_ready,
            "probe": NATIVE_DESKTOP_PROBE,
        },
        "mutation_policy": policy,
    }


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _exit_wait(clock: _Clock, timeout: float = 0.75) -> dict:
    return {
        "timeout_seconds": timeout,
        "poll_interval_seconds": 0.25,
        "sleep": clock.sleep,
        "monotonic": clock.monotonic,
    }


class _FakeClient:
    def __init__(self, owner, **kwargs) -> None:
        self.owner = owner
        self.policy = kwargs["policy"]
        self.require_desktop_ready = kwargs["require_desktop_ready"]
        self.persist_runtime_logs = kwargs["persist_runtime_logs"]
        self.enabled = self.policy.ui_control_enabled
        self.exited = False
        self.health_result = _health(ready=False, policy=self.policy.as_dict())

    async def __aenter__(self):
        self.owner.clients.append(self)
        if self.enabled:
            self.owner.enabled_context_open = True
        return self

    async def __aexit__(self, *_args):
        self.exited = True
        if self.enabled:
            self.owner.enabled_context_open = False
            self.owner.gui_released_by_teardown = True
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
            n = self.owner.launch_calls
            if n == 1:
                return {
                    "status": "started",
                    "launch_attempted": True,
                    "launch_attempts": 1,
                    "ready": True,
                    "com_client_refresh": {"outcome": "not_needed"},
                }
            if n == 3:
                if self.owner.recovery_outcome == "host_discarded":
                    return {
                        "status": "started",
                        "launch_attempted": True,
                        "launch_attempts": 1,
                        "ready": True,
                        "com_client_refresh": {
                            "outcome": "host_discarded",
                            "discarded_generation": 1,
                        },
                    }
                return {
                    "status": "started",
                    "launch_attempted": True,
                    "launch_attempts": 1,
                    "ready": True,
                    "com_client_refresh": {
                        "outcome": "refreshed",
                        "generation": 1,
                        "com_epoch": 3,
                    },
                }
            if self.owner.recovery_outcome == "host_discarded" and n >= 4:
                return {
                    "status": "already_running",
                    "launch_attempted": False,
                    "launch_attempts": 0,
                    "ready": True,
                    "com_client_refresh": {
                        "outcome": "refreshed",
                        "generation": self.owner.post_discard_generation,
                        "com_epoch": n - 2,
                    },
                }
            return {
                "status": "already_running",
                "launch_attempted": False,
                "launch_attempts": 0,
                "ready": True,
                "com_client_refresh": {
                    "outcome": "refreshed",
                    "generation": 1,
                    "com_epoch": n if n > 2 else 2,
                },
            }
        if name == "health_check":
            return _health(ready=True, policy=self.policy.as_dict())
        if name == "list_notebooks":
            self.owner.list_calls += 1
            return {
                "items": [{"id": "notebook-id", "resource_type": "notebook"}],
                "count": 1,
                "execution": {"operation": "list_notebooks"},
            }
        raise AssertionError(name)

    async def call_health_preflight(self, *, allow_desktop_not_running):
        if self.owner.gui_released_by_teardown:
            if allow_desktop_not_running:
                return _health(ready=False, policy=self.policy.as_dict())
            raise ClientFailure(
                "OneNote Desktop health preflight did not prove an existing visible GUI."
            )
        if (
            self.enabled
            and allow_desktop_not_running is False
            and self.owner.launch_calls >= 3 + REFRESH_REPEAT_COUNT
        ):
            self.owner.verdict_health_while_enabled.append(
                self.owner.enabled_context_open
            )
        if not self.enabled:
            assert allow_desktop_not_running is True
            return _health(ready=False, policy=self.policy.as_dict())
        if self.owner.launch_calls == 0:
            assert allow_desktop_not_running is True
            return _health(ready=False, policy=self.policy.as_dict())
        if self.owner.launch_calls == 2 and allow_desktop_not_running:
            if self.owner.unexpected_close_health:
                raise ClientFailure(
                    "probe failed",
                    envelope={
                        "ok": False,
                        "error": {
                            "code": "onenote_desktop_probe_failed",
                            "message": "probe failed",
                        },
                        "execution": {
                            "operation": "health_check",
                            "backend_calls": 0,
                        },
                    },
                )
            if self.owner.close_health_sequence:
                desktop = self.owner.close_health_sequence.pop(0)
                return {
                    "onenote_desktop": desktop,
                    "mutation_policy": self.policy.as_dict(),
                }
            if self.owner.remain_running_after_close:
                return _health(
                    policy=self.policy.as_dict(),
                    process_running=True,
                    visible_window_present=False,
                )
            return _health(ready=False, policy=self.policy.as_dict())
        return _health(ready=True, policy=self.policy.as_dict())

    def validate_health_contract(self, health, *, require_desktop_ready):
        assert require_desktop_ready is True
        assert health["onenote_desktop"]["ready"] is True
        assert health["mutation_policy"] == self.policy.as_dict()


class _FakeClientFactory:
    def __init__(
        self,
        *,
        recovery_outcome: str = "refreshed",
        remain_running_after_close: bool = False,
        post_discard_generation: int = 2,
        close_health_sequence: list[dict] | None = None,
        unexpected_close_health: bool = False,
    ) -> None:
        self.clients = []
        self.calls = []
        self.launch_calls = 0
        self.list_calls = 0
        self.recovery_outcome = recovery_outcome
        self.remain_running_after_close = remain_running_after_close
        self.post_discard_generation = post_discard_generation
        self.close_health_sequence = list(close_health_sequence or [])
        self.unexpected_close_health = unexpected_close_health
        self.enabled_context_open = False
        self.gui_released_by_teardown = False
        self.verdict_health_while_enabled: list[bool] = []
        self.verdict_prompt_while_enabled: list[bool] = []

    def __call__(self, **kwargs):
        return _FakeClient(self, **kwargs)


def _accept_exact_prompt(prompt: str) -> str:
    return prompt.rsplit("Type exactly: ", 1)[1]


def _accept_and_record_verdict(factory: _FakeClientFactory):
    def reader(prompt: str) -> str:
        if "ONE VISIBLE ONENOTE GUI" in prompt:
            factory.verdict_prompt_while_enabled.append(factory.enabled_context_open)
        return _accept_exact_prompt(prompt)

    return reader


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
        "onenote_visible_at_verdict": True,
        "post_mcp_teardown_onenote_state": "not_asserted",
    }
    assert "ready-health-at-human-verdict" in plan["ordered_phases"]
    assert plan["refresh_repeat_count"] == REFRESH_REPEAT_COUNT
    assert "establish-host-hierarchy-read" in plan["ordered_phases"]
    assert "warm-already-running-refresh" in plan["ordered_phases"]
    assert "bounded-native-fully-stopped-wait" in plan["ordered_phases"]
    assert plan["bounded_native_fully_stopped_wait"] is True
    assert plan["sleep_performed"] is False
    assert plan["gui_state_read"] is False
    assert plan["stdin_read_performed"] is False
    assert "recover-after-onenote-close" in plan["ordered_phases"]
    assert "repeated-already-running-refresh" in plan["ordered_phases"]
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
        confirmation_reader=_accept_and_record_verdict(factory),
        terminal_check=lambda: True,
        progress=RunProgressReporter("normal", writer=terminal_lines.append),
    )

    assert result["status"] == "passed"
    assert result["mcp_processes_started"] == 2
    assert result["checks"] == {
        "health_check_did_not_launch": True,
        "unauthorized_rejection_before_backend": True,
        "authorized_single_launch": True,
        "host_established_by_hierarchy_read": True,
        "warm_refresh_same_generation": True,
        "onenote_fully_stopped_after_user_close": True,
        "recovered_after_onenote_close": True,
        "post_recovery_health_ready": True,
        "post_recovery_hierarchy_read": True,
        "repeated_already_running_refresh": True,
        "onenote_ready_at_human_verdict": True,
        "human_single_gui_verdict": True,
    }
    assert result["final_host_generation"] == 1
    assert result["final_com_epoch"] == 3 + REFRESH_REPEAT_COUNT
    assert [client.enabled for client in factory.clients] == [False, True]
    assert all(client.require_desktop_ready is False for client in factory.clients)
    assert all(client.persist_runtime_logs is False for client in factory.clients)
    assert factory.launch_calls == 3 + REFRESH_REPEAT_COUNT
    assert factory.list_calls == 2 + REFRESH_REPEAT_COUNT
    assert read_json(run_dir / "warm-refresh-launch.json")["com_client_refresh"] == {
        "outcome": "refreshed",
        "generation": 1,
        "com_epoch": 2,
    }
    close_health = read_json(run_dir / "health-after-user-close.json")
    assert close_health["status"] == "fully_stopped"
    assert close_health["attempts"] == 1
    assert close_health["last_onenote_desktop"] == {
        "process_running": False,
        "visible_window_present": False,
        "ready": False,
        "probe": NATIVE_DESKTOP_PROBE,
    }
    assert read_json(run_dir / "recover-launch.json")["com_client_refresh"]["outcome"] == "refreshed"
    assert read_json(run_dir / "refresh-repeats.json")["count"] == REFRESH_REPEAT_COUNT
    assert read_json(run_dir / "run-state.json")["status"] == "passed"
    persisted_result = read_json(run_dir / "run-result.json")
    assert persisted_result["onenote_visible_at_verdict"] is True
    assert persisted_result["post_mcp_teardown_onenote_state"] == "not_asserted"
    assert "onenote_left_running" not in persisted_result
    assert factory.verdict_health_while_enabled == [True]
    assert factory.verdict_prompt_while_enabled == [True]
    assert all(client.exited is True for client in factory.clients)
    assert factory.enabled_context_open is False
    assert factory.gui_released_by_teardown is True
    assert read_json(run_dir / "health-at-human-verdict.json")["onenote_desktop"]["ready"] is True
    assert read_json(run_dir / "user-verdict.json")["enabled_mcp_still_running"] is True
    assert persisted_result["mcp_runtime_logs_persisted"] is False
    assert persisted_result["mcp_runtime_logs_streamed_to_terminal"] is True
    assert "runtime_logs_persisted" not in persisted_result
    assert "runtime_logs_streamed_to_terminal" not in persisted_result
    assert not (run_dir / "run-failure.json").exists()
    assert any("[1/6] UI Control disabled proof" in line for line in terminal_lines)
    assert any("[6/6] human single-GUI verdict" in line for line in terminal_lines)
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
        if prompts <= 2:
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
    assert "onenote_left_running" not in failure
    assert read_json(run_dir / "run-state.json")["status"] == "failed_preserved"
    assert (run_dir / "authorized-launch.json").is_file()
    assert (run_dir / "onenote-closed-confirmation.json").is_file()
    assert (run_dir / "refresh-repeats.json").is_file()
    assert (run_dir / "health-at-human-verdict.json").is_file()
    assert not (run_dir / "user-verdict.json").exists()
    assert all(client.exited is True for client in factory.clients)


def test_missing_onenote_closed_confirmation_preserves_failure_evidence(tmp_path) -> None:
    run_dir = tmp_path / "run-2026-08-16-00-00-00"
    factory = _FakeClientFactory()
    prompts = 0

    def reader(prompt: str) -> str:
        nonlocal prompts
        prompts += 1
        if prompts == 1:
            return _accept_exact_prompt(prompt)
        return "REJECT"

    with pytest.raises(LaunchCheckFailure, match="OneNote-closed confirmation"):
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
    assert (run_dir / "warm-refresh-launch.json").is_file()
    assert not (run_dir / "onenote-closed-confirmation.json").exists()


def test_real_check_accepts_host_discarded_recovery_then_new_generation(tmp_path) -> None:
    run_dir = tmp_path / "run-2026-08-16-00-00-01"
    factory = _FakeClientFactory(recovery_outcome="host_discarded")

    result = run_real_check(
        identity=_identity(),
        run_dir=run_dir,
        timeout=30,
        client_factory=factory,
        confirmation_reader=_accept_and_record_verdict(factory),
        terminal_check=lambda: True,
    )

    assert result["status"] == "passed"
    assert result["onenote_visible_at_verdict"] is True
    assert result["post_mcp_teardown_onenote_state"] == "not_asserted"
    assert factory.verdict_health_while_enabled == [True]
    assert factory.verdict_prompt_while_enabled == [True]
    assert result["final_host_generation"] == 2
    assert result["final_com_epoch"] == REFRESH_REPEAT_COUNT + 1
    assert read_json(run_dir / "recover-launch.json")["com_client_refresh"] == {
        "outcome": "host_discarded",
        "discarded_generation": 1,
    }
    repeats = read_json(run_dir / "refresh-repeats.json")
    assert repeats["final_generation"] == 2
    assert [item["launch"]["com_client_refresh"]["com_epoch"] for item in repeats["repeats"]] == [
        2,
        3,
        4,
    ]


def test_user_close_confirmation_fails_when_health_still_shows_running(tmp_path) -> None:
    run_dir = tmp_path / "run-2026-08-16-00-00-02"
    factory = _FakeClientFactory(remain_running_after_close=True)
    clock = _Clock()

    with pytest.raises(LaunchCheckFailure, match="process_running_without_window"):
        run_real_check(
            identity=_identity(),
            run_dir=run_dir,
            timeout=30,
            client_factory=factory,
            confirmation_reader=_accept_exact_prompt,
            terminal_check=lambda: True,
            onenote_exit_wait=_exit_wait(clock),
        )

    failure = read_json(run_dir / "run-failure.json")
    assert failure["status"] == "failed_preserved"
    assert (run_dir / "onenote-closed-confirmation.json").is_file()
    evidence = read_json(run_dir / "health-after-user-close.json")
    assert evidence["status"] == "timeout"
    assert evidence["classification"] == "process_running_without_window"
    assert evidence["last_onenote_desktop"]["process_running"] is True
    assert evidence["last_onenote_desktop"]["visible_window_present"] is False
    assert not (run_dir / "recover-launch.json").exists()
    assert factory.launch_calls == 2
    assert clock.sleeps


def test_host_discarded_recovery_fails_when_generation_does_not_advance(tmp_path) -> None:
    run_dir = tmp_path / "run-2026-08-16-00-00-03"
    factory = _FakeClientFactory(
        recovery_outcome="host_discarded",
        post_discard_generation=1,
    )

    with pytest.raises(LaunchCheckFailure, match="did not advance host generation"):
        run_real_check(
            identity=_identity(),
            run_dir=run_dir,
            timeout=30,
            client_factory=factory,
            confirmation_reader=_accept_exact_prompt,
            terminal_check=lambda: True,
        )

    assert read_json(run_dir / "recover-launch.json")["com_client_refresh"] == {
        "outcome": "host_discarded",
        "discarded_generation": 1,
    }
    assert not (run_dir / "refresh-repeats.json").exists()


def test_user_close_wait_polls_process_only_then_recovers_once(tmp_path) -> None:
    run_dir = tmp_path / "run-2026-08-16-00-00-04"
    factory = _FakeClientFactory(
        close_health_sequence=[
            {
                "process_running": True,
                "visible_window_present": False,
                "ready": False,
                "probe": NATIVE_DESKTOP_PROBE,
            },
            {
                "process_running": False,
                "visible_window_present": False,
                "ready": False,
                "probe": NATIVE_DESKTOP_PROBE,
            },
        ]
    )
    clock = _Clock()

    result = run_real_check(
        identity=_identity(),
        run_dir=run_dir,
        timeout=30,
        client_factory=factory,
        confirmation_reader=_accept_and_record_verdict(factory),
        terminal_check=lambda: True,
        onenote_exit_wait=_exit_wait(clock),
    )

    assert result["status"] == "passed"
    assert factory.verdict_health_while_enabled == [True]
    evidence = read_json(run_dir / "health-after-user-close.json")
    assert evidence["status"] == "fully_stopped"
    assert evidence["attempts"] == 2
    assert clock.sleeps == [0.25]
    assert factory.launch_calls == 3 + REFRESH_REPEAT_COUNT
    assert factory.calls.count((True, "launch_onenote_gui", False)) == factory.launch_calls


def test_user_close_wait_fails_closed_on_unexpected_probe(tmp_path) -> None:
    run_dir = tmp_path / "run-2026-08-16-00-00-05"
    factory = _FakeClientFactory(unexpected_close_health=True)
    clock = _Clock()

    with pytest.raises(LaunchCheckFailure, match="unexpected probe or envelope"):
        run_real_check(
            identity=_identity(),
            run_dir=run_dir,
            timeout=30,
            client_factory=factory,
            confirmation_reader=_accept_exact_prompt,
            terminal_check=lambda: True,
            onenote_exit_wait=_exit_wait(clock),
        )

    evidence = read_json(run_dir / "health-after-user-close.json")
    assert evidence["status"] == "unexpected_probe_or_envelope"
    assert evidence["attempts"] == 1
    assert not (run_dir / "recover-launch.json").exists()
    assert factory.launch_calls == 2
    assert clock.sleeps == []


def test_enabled_teardown_hides_gui_so_verdict_health_cannot_pass_afterwards() -> None:
    factory = _FakeClientFactory()
    factory.launch_calls = 3 + REFRESH_REPEAT_COUNT

    async def exercise() -> None:
        client = factory(
            policy=UI_CONTROL_POLICY,
            allowed_tools={"launch_onenote_gui", "list_notebooks"},
            run_dir=Path("unused"),
            timeout_seconds=30,
            require_desktop_ready=False,
            persist_runtime_logs=False,
        )
        async with client:
            health = await client.call_health_preflight(allow_desktop_not_running=False)
            assert health["onenote_desktop"]["ready"] is True
            assert factory.verdict_health_while_enabled == [True]
        with pytest.raises(ClientFailure, match="visible GUI"):
            await client.call_health_preflight(allow_desktop_not_running=False)

    asyncio.run(exercise())
    assert factory.gui_released_by_teardown is True
    assert factory.enabled_context_open is False
