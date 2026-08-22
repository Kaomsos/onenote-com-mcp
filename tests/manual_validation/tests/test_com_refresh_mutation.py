from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.manual_validation.mcp_stdio_client import ClientFailure
from tests.manual_validation.onenote_exit_wait import NATIVE_DESKTOP_PROBE
from tests.manual_validation.runtime import (
    InvariantFailure,
    RestoreFailure,
    RunnerFailure,
    RuntimeOptions,
)
from tests.manual_validation.run_identity import new_run_identity
from tests.manual_validation.scenarios.common.dry_run import build_isolated_dry_run_plan
from tests.manual_validation.scenarios.common.orchestrator import finalize_notebook
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.common.specs import SCENARIO_SPECS
from tests.manual_validation.test_utils import read_json, write_json


def test_com_refresh_mutation_is_human_gated_and_least_privilege() -> None:
    scenario = SCENARIO_REGISTRY.get("com-refresh-mutation")
    spec = SCENARIO_SPECS["com-refresh-mutation"]

    assert scenario.included_in_all is False
    assert spec.execution_contract["included_in_all"] is False
    assert spec.execution_contract["human_gated_onenote_close"] is True
    assert spec.execution_contract["refresh_internal_validation_com"] is True
    assert spec.execution_contract["refresh_lifecycle_validation_com"] is True
    assert spec.execution_contract["stabilize_target_page_baseline"] is True
    assert spec.execution_contract["observe_forward_rename_durability"] is True
    assert spec.execution_contract["close_source_before_mcp_exit"] is True
    assert scenario.requires_lifecycle_wrappers is True
    assert scenario.close_source_before_mcp_exit is True
    assert spec.policy.ui_control_enabled is True
    assert spec.policy.writes_enabled is True
    assert spec.policy.deletes_enabled is False
    assert "launch_onenote_gui" in spec.tool_allowlist
    assert "rename_page" in spec.tool_allowlist
    assert "delete_page" not in spec.tool_allowlist
    assert scenario.fixture_recipe.supports_cache is True
    assert scenario.fixture_recipe.recipe_version == 1


def test_com_refresh_mutation_dry_run_declares_close_gate_without_stdin(tmp_path) -> None:
    scenario = SCENARIO_REGISTRY.get("com-refresh-mutation")
    args = argparse.Namespace(
        scenario="com-refresh-mutation",
        notebook_name="__com-refresh-mutation__",
        use_cache=False,
        keep_worksite=False,
        keep_notebook=False,
    )
    plan = build_isolated_dry_run_plan(
        args,
        RuntimeOptions(tmp_path / "run", 180, True, True),
        spec=scenario.spec,
        capability_assessment=None,
        copy_budget={"max_pages": 200},
        worksite_action=scenario.worksite_dry_run_action,
        recipe=scenario.fixture_recipe,
        production_close_handoff=False,
    )
    steps = [step["step"] for step in plan["ordered_steps"]]
    assert "human-onenote-close-and-native-stopped-health" in steps
    assert "refresh-internal-validation-com-and-page-xml-probe" in steps
    assert "refresh-lifecycle-validation-com-and-exact-notebook-probe" in steps
    assert "stabilize-target-page-baseline-before-mutation" in steps
    assert "observe-forward-rename-durability" in steps
    assert "close-source-notebook-before-mcp-exit" in steps
    assert steps.index("human-onenote-close-and-native-stopped-health") < steps.index(
        "refresh-internal-validation-com-and-page-xml-probe"
    )
    assert steps.index("refresh-internal-validation-com-and-page-xml-probe") < steps.index(
        "refresh-lifecycle-validation-com-and-exact-notebook-probe"
    )
    assert steps.index("refresh-lifecycle-validation-com-and-exact-notebook-probe") < steps.index(
        "stabilize-target-page-baseline-before-mutation"
    )
    assert steps.index("stabilize-target-page-baseline-before-mutation") < steps.index(
        "com-refresh-mutation"
    )
    assert steps.index("com-refresh-mutation") < steps.index(
        "observe-forward-rename-durability"
    )
    assert steps.index("observe-forward-rename-durability") < steps.index(
        "close-source-notebook-before-mcp-exit"
    )
    assert steps.index("close-source-notebook-before-mcp-exit") < steps.index(
        "close-source-notebook"
    )
    close_step = next(
        step
        for step in plan["ordered_steps"]
        if step["step"] == "human-onenote-close-and-native-stopped-health"
    )
    assert close_step["stdin_read_performed"] is False
    assert close_step["sleep_performed"] is False
    assert close_step["gui_state_read"] is False
    assert close_step["bounded_native_fully_stopped_wait"] is True
    assert close_step["allowed_operations"] == ["health_check"]
    assert "bounded native fully-stopped wait" in close_step["target"]
    internal_step = next(
        step
        for step in plan["ordered_steps"]
        if step["step"] == "refresh-internal-validation-com-and-page-xml-probe"
    )
    assert internal_step["stdin_read_performed"] is False
    assert internal_step["sleep_performed"] is False
    assert internal_step["gui_state_read"] is False
    assert internal_step["allowed_operations"] == ["get_page_xml"]
    assert internal_step["mcp_child_refresh_is_not_sufficient"] is True
    assert "refresh harness-owned internal COM then exact page XML probe" in internal_step[
        "target"
    ]
    lifecycle_step = next(
        step
        for step in plan["ordered_steps"]
        if step["step"] == "refresh-lifecycle-validation-com-and-exact-notebook-probe"
    )
    assert lifecycle_step["stdin_read_performed"] is False
    assert lifecycle_step["sleep_performed"] is False
    assert lifecycle_step["gui_state_read"] is False
    assert lifecycle_step["allowed_operations"] == ["get_exact_notebook"]
    assert lifecycle_step["mcp_child_refresh_is_not_sufficient"] is True
    assert lifecycle_step["internal_bridge_refresh_is_not_sufficient"] is True
    assert "refresh harness-owned lifecycle COM then exact Notebook probe" in lifecycle_step[
        "target"
    ]
    baseline_step = next(
        step
        for step in plan["ordered_steps"]
        if step["step"] == "stabilize-target-page-baseline-before-mutation"
    )
    assert baseline_step["stdin_read_performed"] is False
    assert baseline_step["sleep_performed"] is False
    assert baseline_step["gui_state_read"] is False
    assert baseline_step["allowed_operations"] == ["expand_page"]
    assert baseline_step["phase"] == "baseline"
    assert baseline_step["required_stable_observations"] == 3
    durability_step = next(
        step
        for step in plan["ordered_steps"]
        if step["step"] == "observe-forward-rename-durability"
    )
    assert durability_step["stdin_read_performed"] is False
    assert durability_step["sleep_performed"] is False
    assert durability_step["gui_state_read"] is False
    assert durability_step["allowed_operations"] == ["expand_page"]
    assert durability_step["phase"] == "forward_durability"
    assert durability_step["linger_observations"] == 1
    in_mcp_close = next(
        step
        for step in plan["ordered_steps"]
        if step["step"] == "close-source-notebook-before-mcp-exit"
    )
    assert in_mcp_close["stdin_read_performed"] is False
    assert in_mcp_close["sleep_performed"] is False
    assert in_mcp_close["gui_state_read"] is False
    assert in_mcp_close["allowed_operations"] == ["close_exact_notebook"]
    assert in_mcp_close["mcp_client_still_active"] is True
    final_close = next(
        step
        for step in plan["ordered_steps"]
        if step["step"] == "close-source-notebook"
    )
    assert final_close["allowed_operations"] == []
    assert final_close["preclosed_lease_only"] is True


class _FakeLifecycleWrapper:
    def __init__(
        self,
        *,
        refresh_outcome: str = "refreshed",
        refresh_error: str | None = None,
        probe_error: str | None = None,
        probe_id: str = "notebook-id",
    ) -> None:
        self.refresh_outcome = refresh_outcome
        self.refresh_error = refresh_error
        self.probe_error = probe_error
        self.probe_id = probe_id
        self.refresh_calls = 0
        self.probe_calls = 0
        self.close_transport_calls = 0
        self.close_notebook_calls = 0
        self.closed = False
        self.lease_path = Path()

    def refresh_com_client(self) -> dict[str, object]:
        self.refresh_calls += 1
        if self.refresh_error is not None:
            raise RestoreFailure(self.refresh_error)
        if self.refresh_outcome == "refreshed":
            return {"outcome": "refreshed", "generation": 1, "com_epoch": 2}
        if self.refresh_outcome == "host_discarded":
            return {"outcome": "host_discarded", "discarded_generation": 1}
        return {"outcome": self.refresh_outcome}

    def get_exact_notebook(self, lease=None):
        self.probe_calls += 1
        if self.probe_error is not None:
            raise RestoreFailure(self.probe_error)
        return {"id": self.probe_id, "name": "__com-refresh-mutation__"}

    def close_transport(self) -> None:
        self.close_transport_calls += 1

    def close_exact_notebook(self) -> dict[str, object]:
        self.close_notebook_calls += 1
        self.closed = True
        return {
            "closed": True,
            "source_notebook_id": self.probe_id,
            "close_before": {"id": self.probe_id},
        }


class _FakeMutationClient:
    def __init__(
        self,
        *,
        refresh_outcome: str = "refreshed",
        internal_refresh_outcome: str = "refreshed",
        internal_refresh_error: str | None = None,
        probe_xml: str = "<one:Page/>",
        probe_error: str | None = None,
        fail_post_snapshot: bool = False,
        remain_running_after_close: bool = False,
        mutation_attempts: int = 1,
        restore_attempts: int | None = None,
        close_health_sequence: list[dict] | None = None,
        unexpected_close_health: bool = False,
        expand_titles_after_rename: list[str] | tuple[str, ...] | None = None,
        baseline_modified_sequence: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.notebook_id = "notebook-id"
        self.page_id = "page-id"
        self.section_id = "section-id"
        self.title = "00-Owned-Page"
        self.modified = "2026-08-22T00:00:00Z"
        self.page_body_hash = "body-hash"
        self.refresh_outcome = refresh_outcome
        self.internal_refresh_outcome = internal_refresh_outcome
        self.internal_refresh_error = internal_refresh_error
        self.probe_xml = probe_xml
        self.probe_error = probe_error
        self.fail_post_snapshot = fail_post_snapshot
        self.remain_running_after_close = remain_running_after_close
        self.mutation_attempts = mutation_attempts
        self.restore_attempts = restore_attempts
        self.desktop_running = True
        self.close_health_sequence = list(close_health_sequence or [])
        self.unexpected_close_health = unexpected_close_health
        self.launch_calls: list[dict[str, object]] = []
        self.rename_calls: list[dict[str, object]] = []
        self.health_calls: list[bool] = []
        self.internal_refresh_calls = 0
        self.page_xml_calls: list[dict[str, object]] = []
        self.expand_page_calls: list[dict[str, object]] = []
        self.expand_titles_after_rename = list(expand_titles_after_rename or [])
        self.baseline_modified_sequence = list(baseline_modified_sequence or [])
        self._baseline_modified_index = 0

    def refresh_internal_com_client(self) -> dict[str, object]:
        self.internal_refresh_calls += 1
        if self.internal_refresh_error is not None:
            raise ClientFailure(self.internal_refresh_error)
        if self.internal_refresh_outcome == "refreshed":
            return {"outcome": "refreshed", "generation": 1, "com_epoch": 2}
        if self.internal_refresh_outcome == "host_discarded":
            return {"outcome": "host_discarded", "discarded_generation": 1}
        return {"outcome": self.internal_refresh_outcome}

    def consume_scenario_before_snapshot(self, notebook_id: str):
        assert notebook_id == self.notebook_id
        if self.rename_calls and self.fail_post_snapshot:
            raise ClientFailure(
                "Internal get_page_xml failed: RPC server unavailable (0x800706BA)"
            )
        return {
            "notebook_id": self.notebook_id,
            "items": [
                {
                    "id": self.notebook_id,
                    "resource_type": "notebook",
                    "name": "__com-refresh-mutation__",
                },
                {
                    "id": self.section_id,
                    "resource_type": "section",
                    "name": "00-COM-Refresh",
                    "parent_id": self.notebook_id,
                },
                {
                    "id": self.page_id,
                    "resource_type": "page",
                    "title": self.title,
                    "section_id": self.section_id,
                    "parent_id": self.section_id,
                    "modified": self.modified,
                },
            ],
            "page_body_hashes": {self.page_id: self.page_body_hash},
        }

    def _resolve_expand_title(self, token: str) -> str:
        if token == "original":
            return "00-Owned-Page"
        if token == "marker":
            return str(self.rename_calls[0]["title"])
        return token

    def _expand_page_tree(self) -> dict[str, object]:
        title = self.title
        modified = self.modified
        if self.rename_calls:
            if self.expand_titles_after_rename:
                title = self._resolve_expand_title(self.expand_titles_after_rename.pop(0))
        elif self.baseline_modified_sequence:
            modified = self.baseline_modified_sequence[
                self._baseline_modified_index % len(self.baseline_modified_sequence)
            ]
            self._baseline_modified_index += 1
        return {
            "tree": {
                "item": {
                    "id": self.page_id,
                    "resource_type": "page",
                    "title": title,
                    "section_id": self.section_id,
                    "parent_id": self.section_id,
                    "modified": modified,
                },
                "children": [],
            }
        }

    async def call_health_preflight(self, *, allow_desktop_not_running: bool):
        self.health_calls.append(allow_desktop_not_running)
        if self.unexpected_close_health:
            raise ClientFailure(
                "probe failed",
                envelope={
                    "ok": False,
                    "error": {
                        "code": "onenote_desktop_probe_failed",
                        "message": "probe failed",
                    },
                    "execution": {"operation": "health_check", "backend_calls": 0},
                },
            )
        if self.close_health_sequence:
            return {"onenote_desktop": self.close_health_sequence.pop(0)}
        if self.remain_running_after_close:
            return {
                "onenote_desktop": {
                    "process_running": True,
                    "visible_window_present": False,
                    "ready": False,
                    "probe": NATIVE_DESKTOP_PROBE,
                }
            }
        running = self.desktop_running
        return {
            "onenote_desktop": {
                "process_running": running,
                "visible_window_present": running,
                "ready": running,
                "probe": NATIVE_DESKTOP_PROBE,
            }
        }

    async def call_tool(self, name: str, arguments=None, *, retry_read: bool = True):
        arguments = arguments or {}
        if name == "launch_onenote_gui":
            self.launch_calls.append(
                {"arguments": dict(arguments), "retry_read": retry_read}
            )
            refresh = (
                {"outcome": "refreshed", "generation": 1, "com_epoch": 2}
                if self.refresh_outcome == "refreshed"
                else (
                    {"outcome": "host_discarded", "discarded_generation": 1}
                    if self.refresh_outcome == "host_discarded"
                    else {"outcome": self.refresh_outcome}
                )
            )
            return {
                "status": "started",
                "launch_attempted": True,
                "launch_attempts": 1,
                "ready": True,
                "com_client_refresh": refresh,
            }
        if name == "get_page_xml":
            self.page_xml_calls.append(
                {"arguments": dict(arguments), "retry_read": retry_read}
            )
            if self.probe_error is not None:
                raise ClientFailure(self.probe_error)
            return {"xml": self.probe_xml}
        if name == "expand_page":
            self.expand_page_calls.append(
                {"arguments": dict(arguments), "retry_read": retry_read}
            )
            return self._expand_page_tree()
        if name == "rename_page":
            self.rename_calls.append(dict(arguments))
            attempts = self.mutation_attempts
            if self.restore_attempts is not None and len(self.rename_calls) > 1:
                attempts = self.restore_attempts
            if attempts == 1:
                self.title = str(arguments["title"])
                self.modified = "2026-08-22T00:00:01Z"
            return {
                "reconciliation": {
                    "state": "applied",
                    "mutation_attempts": attempts,
                    "mutation_replayed": False,
                    "observed_outcome": "applied",
                }
            }
        raise AssertionError(name)


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


def _page_stability(clock: _Clock, **overrides) -> dict:
    values = {
        "baseline_timeout_seconds": 8.0,
        "forward_timeout_seconds": 12.0,
        "poll_interval_seconds": 1.0,
        "required_stable_observations": 3,
        "linger_observations": 1,
        "max_observations": 16,
        "sleep": clock.sleep,
        "monotonic": clock.monotonic,
    }
    values.update(overrides)
    return values


def _args(run_dir: Path, client: _FakeMutationClient, **overrides):
    identity = new_run_identity()

    def reader(_prompt: str) -> str:
        client.desktop_running = False
        return f"CLOSED {run_dir.name} ONENOTE CONTINUE"

    values = {
        "notebook_name": "__com-refresh-mutation__",
        "run_identity": identity,
        "keep_worksite": False,
        "confirmation_reader": reader,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _manifest() -> dict:
    return {
        "notebook": {"id": "notebook-id", "name": "__com-refresh-mutation__"},
        "structure": {"page_target": {"id": "page-id", "resource_type": "page"}},
    }


def _execute(
    run_dir: Path,
    client: _FakeMutationClient,
    wrapper: _FakeLifecycleWrapper | None = None,
    **overrides,
):
    (run_dir / "scenarios" / "com-refresh-mutation").mkdir(parents=True)
    wrapper = wrapper or _FakeLifecycleWrapper()
    if "page_stability" not in overrides:
        overrides["page_stability"] = _page_stability(_Clock())
    return asyncio.run(
        SCENARIO_REGISTRY.get("com-refresh-mutation").execute_with_lifecycle(
            _args(run_dir, client, **overrides),
            RuntimeOptions(run_dir, 180, True, False),
            _manifest(),
            client=client,
            fixture_result={"status": "prepared"},
            wrappers={"source": wrapper},
        )
    )


def _require_single_unretried_recovery_launch(client: _FakeMutationClient) -> None:
    assert len(client.launch_calls) == 1
    assert client.launch_calls[0]["arguments"] == {}
    assert client.launch_calls[0]["retry_read"] is False


def _require_internal_refresh_and_probe(client: _FakeMutationClient) -> None:
    assert client.internal_refresh_calls == 1
    assert len(client.page_xml_calls) == 1
    assert client.page_xml_calls[0]["arguments"] == {
        "page_id": client.page_id,
        "page_info": "all",
    }
    assert client.page_xml_calls[0]["retry_read"] is False


def _require_internal_gate_evidence(run_dir: Path, *, outcome: str) -> None:
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    refresh = read_json(evidence / "internal-bridge-refresh.json")
    probe = read_json(evidence / "internal-page-xml-probe.json")
    assert refresh["outcome"] == outcome
    assert probe == {
        "status": "ready",
        "page_id": "page-id",
        "xml_present": True,
        "xml_recorded": False,
    }
    assert "xml" not in probe


def _require_lifecycle_refresh_and_probe(
    wrapper: _FakeLifecycleWrapper,
) -> None:
    assert wrapper.refresh_calls == 1
    assert wrapper.probe_calls == 1
    assert wrapper.close_transport_calls == 0


def _require_lifecycle_gate_evidence(run_dir: Path, *, outcome: str) -> None:
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    refresh = read_json(evidence / "lifecycle-bridge-refresh.json")
    probe = read_json(evidence / "lifecycle-notebook-probe.json")
    assert refresh["outcome"] == outcome
    assert probe == {
        "status": "ready",
        "notebook_id": "notebook-id",
        "xml_recorded": False,
    }


def test_execute_recovers_from_full_stop_then_renames_once_and_restores(tmp_path) -> None:
    run_dir = tmp_path / "run-refresh"
    client = _FakeMutationClient()
    wrapper = _FakeLifecycleWrapper()
    result = _execute(run_dir, client, wrapper)

    assert result["status"] == "passed"
    assert result["refresh_outcome"] == "refreshed"
    assert result["internal_refresh_outcome"] == "refreshed"
    assert result["internal_page_xml_probe_ready"] is True
    assert result["lifecycle_refresh_outcome"] == "refreshed"
    assert result["lifecycle_notebook_probe_ready"] is True
    assert result["target_page_baseline_stable"] is True
    assert result["forward_rename_durable"] is True
    assert result["onenote_fully_stopped_after_user_close"] is True
    assert result["same_mcp_recovered_after_stop"] is True
    assert result["restored"] is True
    assert client.health_calls == [True]
    _require_single_unretried_recovery_launch(client)
    _require_internal_refresh_and_probe(client)
    _require_lifecycle_refresh_and_probe(wrapper)
    assert [call["title"] for call in client.rename_calls] == [
        result["marker"],
        "00-Owned-Page",
    ]
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    close_health = read_json(evidence / "health-after-user-close.json")
    assert close_health["status"] == "fully_stopped"
    assert close_health["attempts"] == 1
    assert close_health["last_onenote_desktop"]["ready"] is False
    assert read_json(evidence / "launch-refresh.json")["com_client_refresh"]["outcome"] == "refreshed"
    _require_internal_gate_evidence(run_dir, outcome="refreshed")
    _require_lifecycle_gate_evidence(run_dir, outcome="refreshed")
    baseline = read_json(evidence / "page-baseline-stability.json")
    durability = read_json(evidence / "forward-durability.json")
    assert baseline["status"] == "stable"
    assert baseline["xml_recorded"] is False
    assert durability["status"] == "durable"
    assert durability["xml_recorded"] is False
    assert durability["reverted_to_original"] is False
    assert all(call["retry_read"] is False for call in client.expand_page_calls)
    assert read_json(evidence / "after.json")["items"][-1]["title"] == result["marker"]
    assert read_json(evidence / "restored.json")["items"][-1]["title"] == "00-Owned-Page"
    assert len([item for item in read_json(evidence / "after.json")["items"] if item.get("title") == result["marker"]]) == 1


def test_execute_accepts_host_discarded_then_mutates_once(tmp_path) -> None:
    run_dir = tmp_path / "run-discarded"
    client = _FakeMutationClient(refresh_outcome="host_discarded")
    wrapper = _FakeLifecycleWrapper()
    result = _execute(run_dir, client, wrapper)

    assert result["status"] == "passed"
    assert result["refresh_outcome"] == "host_discarded"
    assert result["internal_refresh_outcome"] == "refreshed"
    assert result["lifecycle_refresh_outcome"] == "refreshed"
    assert result["restored"] is True
    _require_single_unretried_recovery_launch(client)
    _require_internal_refresh_and_probe(client)
    _require_lifecycle_refresh_and_probe(wrapper)
    assert len(client.rename_calls) == 2
    _require_internal_gate_evidence(run_dir, outcome="refreshed")
    _require_lifecycle_gate_evidence(run_dir, outcome="refreshed")


@pytest.mark.parametrize("outcome", ("not_needed", "rejected_closed", "not_attempted", "host_discard_unconfirmed"))
def test_execute_rejects_disallowed_refresh_before_mutation(tmp_path, outcome: str) -> None:
    run_dir = tmp_path / f"run-{outcome}"
    client = _FakeMutationClient(refresh_outcome=outcome)
    wrapper = _FakeLifecycleWrapper()
    with pytest.raises(InvariantFailure, match="refreshed or host_discarded"):
        _execute(run_dir, client, wrapper)
    _require_single_unretried_recovery_launch(client)
    assert client.internal_refresh_calls == 0
    assert client.page_xml_calls == []
    assert wrapper.refresh_calls == 0
    assert client.rename_calls == []
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    assert read_json(evidence / "launch-refresh.json")["com_client_refresh"]["outcome"] == outcome
    assert not (evidence / "internal-bridge-refresh.json").exists()
    assert not (evidence / "rename-forward.json").exists()


def test_execute_fails_when_health_still_shows_running_after_close(tmp_path) -> None:
    run_dir = tmp_path / "run-still-running"
    client = _FakeMutationClient(remain_running_after_close=True)
    clock = _Clock()
    with pytest.raises(InvariantFailure, match="process_running_without_window"):
        _execute(run_dir, client, onenote_exit_wait=_exit_wait(clock))
    assert client.launch_calls == []
    assert client.internal_refresh_calls == 0
    assert client.rename_calls == []
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    assert (evidence / "onenote-closed-confirmation.json").is_file()
    waited = read_json(evidence / "health-after-user-close.json")
    assert waited["status"] == "timeout"
    assert waited["last_onenote_desktop"]["process_running"] is True
    assert waited["last_onenote_desktop"]["visible_window_present"] is False
    assert not (evidence / "launch-refresh.json").exists()
    assert clock.sleeps


def test_execute_maps_unexpected_health_failure_before_launch(tmp_path) -> None:
    run_dir = tmp_path / "run-health-boom"
    client = _FakeMutationClient(unexpected_close_health=True)
    clock = _Clock()
    with pytest.raises(InvariantFailure, match="unexpected probe or envelope"):
        _execute(run_dir, client, onenote_exit_wait=_exit_wait(clock))
    assert client.launch_calls == []
    assert client.internal_refresh_calls == 0
    assert client.rename_calls == []
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    assert (evidence / "onenote-closed-confirmation.json").is_file()
    assert read_json(evidence / "health-after-user-close.json")["status"] == (
        "unexpected_probe_or_envelope"
    )
    assert not (evidence / "launch-refresh.json").exists()
    assert clock.sleeps == []


def test_execute_polls_process_only_then_launches_once_without_retry(tmp_path) -> None:
    run_dir = tmp_path / "run-async-exit"
    client = _FakeMutationClient(
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
    result = _execute(run_dir, client, onenote_exit_wait=_exit_wait(clock))
    assert result["status"] == "passed"
    assert result["restored"] is True
    _require_single_unretried_recovery_launch(client)
    _require_internal_refresh_and_probe(client)
    assert [call["title"] for call in client.rename_calls] == [
        result["marker"],
        "00-Owned-Page",
    ]
    evidence = read_json(
        run_dir / "scenarios" / "com-refresh-mutation" / "health-after-user-close.json"
    )
    assert evidence["status"] == "fully_stopped"
    assert evidence["attempts"] == 2
    assert clock.sleeps == [0.25]
    assert client.health_calls == [True, True]


def test_execute_rejects_missing_close_confirmation_before_health(tmp_path) -> None:
    run_dir = tmp_path / "run-no-confirm"
    client = _FakeMutationClient()
    with pytest.raises(InvariantFailure, match="OneNote-closed confirmation"):
        _execute(run_dir, client, confirmation_reader=lambda _prompt: "NO")
    assert client.health_calls == []
    assert client.launch_calls == []
    assert client.internal_refresh_calls == 0
    assert client.rename_calls == []


def test_execute_rejects_replayed_forward_mutation(tmp_path) -> None:
    run_dir = tmp_path / "run-replay"
    client = _FakeMutationClient(mutation_attempts=2)
    with pytest.raises(InvariantFailure, match="mutation attempt evidence"):
        _execute(run_dir, client)
    _require_single_unretried_recovery_launch(client)
    _require_internal_refresh_and_probe(client)
    assert len(client.rename_calls) == 1
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    assert (evidence / "rename-forward.json").is_file()
    assert not (evidence / "rename-restore.json").exists()


def test_execute_restore_failure_preserves_forward_evidence(tmp_path) -> None:
    run_dir = tmp_path / "run-restore-fail"
    client = _FakeMutationClient(restore_attempts=2)
    with pytest.raises(RestoreFailure, match="restoration failed"):
        _execute(run_dir, client)
    _require_single_unretried_recovery_launch(client)
    _require_internal_refresh_and_probe(client)
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    assert (evidence / "after.json").is_file()
    assert (evidence / "rename-restore.json").is_file()
    assert not (evidence / "restored.json").exists()
    assert not (evidence / "result.json").exists()
    assert client.rename_calls[0]["title"].startswith("COM-REFRESH-")


def test_execute_keep_worksite_skips_restore(tmp_path) -> None:
    run_dir = tmp_path / "run-keep"
    client = _FakeMutationClient()
    result = _execute(run_dir, client, keep_worksite=True)
    assert result["restored"] is False
    assert result["worksite_preserved"] is True
    _require_single_unretried_recovery_launch(client)
    _require_internal_refresh_and_probe(client)
    assert len(client.rename_calls) == 1
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    assert (evidence / "worksite.json").is_file()
    assert not (evidence / "restored.json").exists()


def test_execute_accepts_internal_not_needed_then_mutates_once(tmp_path) -> None:
    run_dir = tmp_path / "run-internal-not-needed"
    client = _FakeMutationClient(internal_refresh_outcome="not_needed")
    result = _execute(run_dir, client)

    assert result["status"] == "passed"
    assert result["internal_refresh_outcome"] == "not_needed"
    _require_single_unretried_recovery_launch(client)
    _require_internal_refresh_and_probe(client)
    assert [call["title"] for call in client.rename_calls] == [
        result["marker"],
        "00-Owned-Page",
    ]
    _require_internal_gate_evidence(run_dir, outcome="not_needed")


def test_execute_accepts_internal_host_discarded_then_mutates_once(tmp_path) -> None:
    run_dir = tmp_path / "run-internal-discarded"
    client = _FakeMutationClient(internal_refresh_outcome="host_discarded")
    result = _execute(run_dir, client)

    assert result["status"] == "passed"
    assert result["internal_refresh_outcome"] == "host_discarded"
    _require_single_unretried_recovery_launch(client)
    _require_internal_refresh_and_probe(client)
    assert len(client.rename_calls) == 2
    _require_internal_gate_evidence(run_dir, outcome="host_discarded")


@pytest.mark.parametrize(
    "outcome",
    ("not_attempted", "rejected_closed", "host_discard_unconfirmed"),
)
def test_execute_rejects_internal_refresh_before_mutation(
    tmp_path, outcome: str
) -> None:
    run_dir = tmp_path / f"run-internal-{outcome}"
    client = _FakeMutationClient(internal_refresh_outcome=outcome)
    wrapper = _FakeLifecycleWrapper()
    with pytest.raises(InvariantFailure, match="refreshed, host_discarded, or not_needed"):
        _execute(run_dir, client, wrapper)
    _require_single_unretried_recovery_launch(client)
    assert client.internal_refresh_calls == 1
    assert client.page_xml_calls == []
    assert wrapper.refresh_calls == 0
    assert client.rename_calls == []
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    assert read_json(evidence / "internal-bridge-refresh.json")["outcome"] == outcome
    assert not (evidence / "internal-page-xml-probe.json").exists()
    assert not (evidence / "rename-forward.json").exists()


def test_execute_internal_refresh_error_blocks_mutation(tmp_path) -> None:
    run_dir = tmp_path / "run-internal-refresh-error"
    client = _FakeMutationClient(internal_refresh_error="internal refresh exploded")
    wrapper = _FakeLifecycleWrapper()
    with pytest.raises(InvariantFailure, match="Internal validation COM refresh failed"):
        _execute(run_dir, client, wrapper)
    _require_single_unretried_recovery_launch(client)
    assert client.internal_refresh_calls == 1
    assert client.page_xml_calls == []
    assert wrapper.refresh_calls == 0
    assert client.rename_calls == []
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    assert read_json(evidence / "internal-bridge-refresh.json") == {
        "status": "failed",
        "xml_recorded": False,
        "error_type": "ClientFailure",
    }
    assert not (evidence / "rename-forward.json").exists()


def test_execute_stale_internal_proxy_probe_blocks_mutation(tmp_path) -> None:
    run_dir = tmp_path / "run-internal-rpc"
    client = _FakeMutationClient(
        probe_error="Internal get_page_xml failed: RPC server unavailable (0x800706BA)"
    )
    wrapper = _FakeLifecycleWrapper()
    with pytest.raises(InvariantFailure, match="Internal validation COM probe failed"):
        _execute(run_dir, client, wrapper)
    _require_single_unretried_recovery_launch(client)
    assert client.internal_refresh_calls == 1
    assert len(client.page_xml_calls) == 1
    assert wrapper.refresh_calls == 0
    assert client.rename_calls == []
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    probe = read_json(evidence / "internal-page-xml-probe.json")
    assert probe == {
        "status": "failed",
        "page_id": "page-id",
        "xml_present": False,
        "xml_recorded": False,
        "error_type": "ClientFailure",
        "hresult": "0x800706BA",
    }
    assert not (evidence / "rename-forward.json").exists()


def test_execute_empty_internal_probe_xml_blocks_mutation(tmp_path) -> None:
    run_dir = tmp_path / "run-empty-probe"
    client = _FakeMutationClient(probe_xml="   ")
    with pytest.raises(InvariantFailure, match="did not return Page XML"):
        _execute(run_dir, client)
    _require_single_unretried_recovery_launch(client)
    assert client.internal_refresh_calls == 1
    assert len(client.page_xml_calls) == 1
    assert client.rename_calls == []
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    assert read_json(evidence / "internal-page-xml-probe.json") == {
        "status": "failed",
        "page_id": "page-id",
        "xml_present": False,
        "xml_recorded": False,
    }
    assert not (evidence / "rename-forward.json").exists()


def test_execute_post_snapshot_rpc_unavailable_does_not_replay_rename(tmp_path) -> None:
    run_dir = tmp_path / "run-post-rpc"
    client = _FakeMutationClient(fail_post_snapshot=True)
    with pytest.raises(ClientFailure, match="0x800706BA"):
        _execute(run_dir, client)
    _require_single_unretried_recovery_launch(client)
    _require_internal_refresh_and_probe(client)
    assert len(client.rename_calls) == 1
    assert client.rename_calls[0]["title"].startswith("COM-REFRESH-")
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    assert (evidence / "rename-forward.json").is_file()
    assert not (evidence / "after.json").exists()
    assert not (evidence / "rename-restore.json").exists()
    assert not (evidence / "result.json").exists()


def test_execute_without_lifecycle_wrapper_is_rejected(tmp_path) -> None:
    run_dir = tmp_path / "run-no-wrapper"
    (run_dir / "scenarios" / "com-refresh-mutation").mkdir(parents=True)
    client = _FakeMutationClient()
    with pytest.raises(RunnerFailure, match="execute_with_lifecycle"):
        asyncio.run(
            SCENARIO_REGISTRY.get("com-refresh-mutation").execute(
                _args(run_dir, client),
                RuntimeOptions(run_dir, 180, True, False),
                _manifest(),
                client=client,
                fixture_result={"status": "prepared"},
            )
        )
    assert client.rename_calls == []


def test_execute_accepts_lifecycle_not_needed_then_mutates_once(tmp_path) -> None:
    run_dir = tmp_path / "run-lifecycle-not-needed"
    client = _FakeMutationClient()
    wrapper = _FakeLifecycleWrapper(refresh_outcome="not_needed")
    result = _execute(run_dir, client, wrapper)

    assert result["status"] == "passed"
    assert result["lifecycle_refresh_outcome"] == "not_needed"
    _require_internal_refresh_and_probe(client)
    _require_lifecycle_refresh_and_probe(wrapper)
    assert len(client.rename_calls) == 2
    _require_lifecycle_gate_evidence(run_dir, outcome="not_needed")


@pytest.mark.parametrize(
    "outcome",
    ("not_attempted", "rejected_closed", "host_discard_unconfirmed"),
)
def test_execute_rejects_lifecycle_refresh_before_mutation(
    tmp_path, outcome: str
) -> None:
    run_dir = tmp_path / f"run-lifecycle-{outcome}"
    client = _FakeMutationClient()
    wrapper = _FakeLifecycleWrapper(refresh_outcome=outcome)
    with pytest.raises(InvariantFailure, match="Lifecycle validation COM refresh"):
        _execute(run_dir, client, wrapper)
    _require_internal_refresh_and_probe(client)
    assert wrapper.refresh_calls == 1
    assert wrapper.probe_calls == 0
    assert client.rename_calls == []
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    assert read_json(evidence / "lifecycle-bridge-refresh.json")["outcome"] == outcome
    assert not (evidence / "lifecycle-notebook-probe.json").exists()
    assert not (evidence / "page-baseline-stability.json").exists()
    assert not (evidence / "rename-forward.json").exists()


def test_execute_lifecycle_refresh_error_blocks_mutation(tmp_path) -> None:
    run_dir = tmp_path / "run-lifecycle-refresh-error"
    client = _FakeMutationClient()
    wrapper = _FakeLifecycleWrapper(refresh_error="lifecycle refresh exploded")
    with pytest.raises(InvariantFailure, match="Lifecycle validation COM refresh failed"):
        _execute(run_dir, client, wrapper)
    _require_internal_refresh_and_probe(client)
    assert wrapper.refresh_calls == 1
    assert wrapper.probe_calls == 0
    assert client.rename_calls == []
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    assert read_json(evidence / "lifecycle-bridge-refresh.json") == {
        "status": "failed",
        "xml_recorded": False,
        "error_type": "RestoreFailure",
    }
    assert not (evidence / "page-baseline-stability.json").exists()
    assert not (evidence / "rename-forward.json").exists()


def test_execute_stale_lifecycle_proxy_probe_blocks_mutation(tmp_path) -> None:
    run_dir = tmp_path / "run-lifecycle-rpc"
    client = _FakeMutationClient()
    wrapper = _FakeLifecycleWrapper(
        probe_error="Lifecycle could not read the leased Notebook: RPC server unavailable (0x800706BA)"
    )
    with pytest.raises(InvariantFailure, match="Lifecycle validation COM probe failed"):
        _execute(run_dir, client, wrapper)
    _require_internal_refresh_and_probe(client)
    assert wrapper.refresh_calls == 1
    assert wrapper.probe_calls == 1
    assert wrapper.close_transport_calls == 0
    assert client.rename_calls == []
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    assert read_json(evidence / "lifecycle-notebook-probe.json") == {
        "status": "failed",
        "notebook_id": "notebook-id",
        "xml_recorded": False,
        "error_type": "RestoreFailure",
        "hresult": "0x800706BA",
    }
    assert not (evidence / "page-baseline-stability.json").exists()
    assert not (evidence / "rename-forward.json").exists()


def test_execute_unstable_baseline_blocks_rename(tmp_path) -> None:
    run_dir = tmp_path / "run-unstable-baseline"
    client = _FakeMutationClient(
        baseline_modified_sequence=(
            "2026-08-22T00:00:00Z",
            "2026-08-22T00:00:02Z",
        )
    )
    wrapper = _FakeLifecycleWrapper()
    clock = _Clock()
    with pytest.raises(InvariantFailure, match="did not stay stable"):
        _execute(
            run_dir,
            client,
            wrapper,
            page_stability=_page_stability(
                clock,
                baseline_timeout_seconds=2.0,
                max_observations=4,
            ),
        )
    _require_single_unretried_recovery_launch(client)
    _require_internal_refresh_and_probe(client)
    _require_lifecycle_refresh_and_probe(wrapper)
    assert client.rename_calls == []
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    baseline = read_json(evidence / "page-baseline-stability.json")
    assert baseline["status"] == "not_stable"
    assert baseline["xml_recorded"] is False
    assert not (evidence / "rename-forward.json").exists()
    assert not (evidence / "forward-durability.json").exists()
    assert clock.sleeps


def test_execute_forward_not_durable_skips_restore_and_closes_lifecycle(
    tmp_path,
) -> None:
    run_dir = tmp_path / "run-forward-not-durable"
    client = _FakeMutationClient(
        expand_titles_after_rename=("original", "marker", "marker", "original")
    )
    wrapper = _FakeLifecycleWrapper()
    wrapper.lease_path = run_dir / "lifecycle-lease.json"
    write_json(
        wrapper.lease_path,
        {
            "notebook_id": "notebook-id",
            "expected_name": "__com-refresh-mutation__",
            "expected_local_path": str(run_dir / "notebooks" / "owned"),
            "state": "active",
        },
    )
    with pytest.raises(InvariantFailure, match="forward_not_durable"):
        _execute(run_dir, client, wrapper)
    _require_single_unretried_recovery_launch(client)
    _require_internal_refresh_and_probe(client)
    _require_lifecycle_refresh_and_probe(wrapper)
    assert len(client.rename_calls) == 1
    assert client.rename_calls[0]["title"].startswith("COM-REFRESH-")
    evidence = run_dir / "scenarios" / "com-refresh-mutation"
    durability = read_json(evidence / "forward-durability.json")
    assert durability["status"] == "forward_not_durable"
    assert durability["seen_marker"] is True
    assert durability["reverted_to_original"] is True
    assert durability["xml_recorded"] is False
    assert [item["title_matches_original"] for item in durability["observations"]] == [
        True,
        False,
        False,
        True,
    ]
    assert [item["title_matches_marker"] for item in durability["observations"]] == [
        False,
        True,
        True,
        False,
    ]
    assert (evidence / "rename-forward.json").is_file()
    assert read_json(evidence / "after.json")["items"][-1]["title"].startswith(
        "COM-REFRESH-"
    )
    assert not (evidence / "rename-restore.json").exists()
    assert not (evidence / "restored.json").exists()
    assert not (evidence / "result.json").exists()

    finalized = asyncio.run(
        finalize_notebook(
            SimpleNamespace(
                scenario="com-refresh-mutation",
                keep_notebook=False,
                keep_worksite=False,
            ),
            RuntimeOptions(run_dir, 180, True, False),
            _manifest(),
            wrapper=wrapper,
        )
    )
    assert wrapper.closed is True
    assert wrapper.close_notebook_calls == 1
    assert finalized["closed"] is True
    assert finalized["status"] == "closed_preserved"
