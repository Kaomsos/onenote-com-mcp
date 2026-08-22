"""Pure contracts for Move Page cross-second dateTime negative mode."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.manual_validation.mcp_stdio_client import (
    ClientFailure,
    MOVE_PAGE_DATETIME_DRIFT_NEGATIVE_POLICY,
    MOVE_PAGE_POLICY,
)
from tests.manual_validation.runtime import (
    EXIT_MCP,
    ExpectedNegativeOutcome,
    InvariantFailure,
    RunnerFailure,
    RuntimeOptions,
)
from tests.manual_validation.runner import build_parser
from tests.manual_validation.scenarios.common.config import (
    MOVE_PAGE_DATETIME_DRIFT_NEGATIVE_TOOLS,
    MOVE_PAGE_TOOLS,
)
from tests.manual_validation.scenarios.common.datetime_drift_negative import (
    apply_datetime_drift_once,
    build_negative_gate_evidence,
    confirm_next_utc_second,
    is_exact_trigger_event,
    next_utc_second,
    require_backend_counts,
    require_copy_only_envelope,
    require_source_and_target_layout,
    scan_trace_records,
    wait_for_datetime_drift_trigger,
    write_negative_gate_and_raise_expected_outcome,
)
from tests.manual_validation.scenarios.common.orchestrator import isolated_dry_run, run_validate
from tests.manual_validation.scenarios.common.registry import (
    SCENARIO_REGISTRY,
    get_all_scenario_names,
)
from tests.manual_validation.scenarios.common.specs import (
    get_scenario_spec,
)
from tests.manual_validation.scenarios.move_page import MovePageScenario
from tests.manual_validation.scenarios.negative_move_page_datetime_drift import (
    NegativeMovePageDatetimeDriftScenario,
    SCENARIO_NAME,
)
from tests.manual_validation.test_utils import read_json


FROZEN_SECOND = "2026-01-01T00:00:00Z"
NEXT_SECOND = "2026-01-01T00:00:01Z"


def _copy_only_envelope(*, rewritten: bool = False) -> dict:
    statuses = ["source_drifted" if rewritten else "verified"] * 2
    return {
        "ok": False,
        "error": {
            "code": "partial_failure",
            "message": "source deletion was blocked",
            "details": {
                "outcome": "copy_only",
                "source_deleted": False,
                "created_ids": ["target-subtree", "target-subtree-child"],
                "copy_report": {
                    "verified": True,
                    "lossless": True,
                    "copy_contract_satisfied": True,
                    "id_map": {
                        "subtree": "target-subtree",
                        "subtree-child": "target-subtree-child",
                    },
                    "page_results": [
                        {"date_time": {"status": status}} for status in statuses
                    ],
                },
            },
        },
    }


def _layout(*, missing_source: bool = False, extra_target: bool = False) -> tuple[dict, dict]:
    source_ids = ["subtree"] if missing_source else ["subtree", "subtree-child"]
    before = {
        "items": [
            {
                "id": "subtree",
                "resource_type": "page",
                "section_id": "source-section",
                "page_level": 1,
                "order": 0,
            },
            {
                "id": "subtree-child",
                "resource_type": "page",
                "section_id": "source-section",
                "page_level": 2,
                "order": 1,
            },
            {
                "id": "destination-anchor-a",
                "resource_type": "page",
                "section_id": "destination-section",
                "page_level": 1,
                "order": 0,
            },
        ],
        "page_hashes": {
            "subtree": "subtree-hash",
            "subtree-child": "child-hash",
        },
        "page_datetime_seconds": {
            "subtree": FROZEN_SECOND,
            "subtree-child": FROZEN_SECOND,
        },
    }
    after_items = [
        item
        for item in before["items"]
        if item["id"] in source_ids or item["id"] == "destination-anchor-a"
    ]
    after_items.extend(
        [
            {
                "id": "target-subtree",
                "resource_type": "page",
                "section_id": "destination-section",
                "page_level": 1,
                "order": 1,
            },
            {
                "id": "target-subtree-child",
                "resource_type": "page",
                "section_id": "destination-section",
                "page_level": 2,
                "order": 2,
            },
        ]
    )
    if extra_target:
        after_items.append(
            {
                "id": "duplicate-target",
                "resource_type": "page",
                "section_id": "destination-section",
                "page_level": 1,
                "order": 3,
            }
        )
    after = {
        "items": after_items,
        "page_hashes": dict(before["page_hashes"]),
        "page_datetime_seconds": {
            "subtree": FROZEN_SECOND,
            "subtree-child": NEXT_SECOND,
            "target-subtree": FROZEN_SECOND,
            "target-subtree-child": FROZEN_SECOND,
        },
    }
    return before, after


def test_default_move_page_contract_is_unchanged() -> None:
    scenario = MovePageScenario()
    spec = get_scenario_spec("move-page")
    assert scenario.included_in_all is True
    assert "move-page" in get_all_scenario_names()
    assert spec.policy == MOVE_PAGE_POLICY
    assert spec.policy.timestamp_fidelity_probe_enabled is False
    assert spec.tool_allowlist == (
        MOVE_PAGE_TOOLS | {"create_section", "create_page", "reorder_page"}
    )
    assert "read_verified_page_datetime" not in spec.tool_allowlist
    assert "set_verified_page_datetime" not in spec.tool_allowlist
    assert spec.execution_contract.get("datetime_drift_negative") is None
    assert [case["name"] for case in spec.execution_contract["cases"]] == [
        "cross-notebook-root-only",
        "cross-notebook-subtree",
    ]


def test_negative_scenario_owns_only_validation_datetime_capabilities() -> None:
    scenario = NegativeMovePageDatetimeDriftScenario()
    spec = get_scenario_spec(SCENARIO_NAME)
    default = get_scenario_spec("move-page")
    assert spec.name == SCENARIO_NAME
    assert spec.fixture != default.fixture
    assert spec.fixture.name == SCENARIO_NAME
    assert scenario.fixture_recipe.scenario_name == SCENARIO_NAME
    assert scenario.fixture_recipe is not MovePageScenario().fixture_recipe
    assert scenario.included_in_all is False
    assert SCENARIO_NAME not in get_all_scenario_names()
    assert spec.policy == MOVE_PAGE_DATETIME_DRIFT_NEGATIVE_POLICY
    assert spec.policy.writes_enabled is True
    assert spec.policy.create_enabled is True
    assert spec.policy.deletes_enabled is True
    assert spec.policy.timestamp_fidelity_probe_enabled is True
    extra = set(spec.tool_allowlist) - set(default.tool_allowlist)
    assert extra == MOVE_PAGE_DATETIME_DRIFT_NEGATIVE_TOOLS - MOVE_PAGE_TOOLS
    assert extra == {"read_verified_page_datetime", "set_verified_page_datetime"}
    assert spec.execution_contract["cases"][0]["name"] == "cross-notebook-subtree"
    assert spec.execution_contract["fresh_only"] is True
    assert spec.execution_contract["included_in_all"] is False
    assert spec.execution_contract["expected_outcome"] == "copy_only"


def test_negative_scenario_is_independently_registered() -> None:
    parser = build_parser()
    args = parser.parse_args([SCENARIO_NAME])
    assert args.command == SCENARIO_NAME
    assert NegativeMovePageDatetimeDriftScenario().runtime_spec(args) == get_scenario_spec(
        SCENARIO_NAME
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["move-page", "--datetime-drift-negative"])


def test_negative_scenario_rejects_use_cache_before_onenote(tmp_path) -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            SCENARIO_NAME,
            "--use-cache",
            "--run-dir",
            str(tmp_path / "run"),
        ]
    )
    args.scenario = args.command
    args.notebook_name = "__ISOLATED__"
    with pytest.raises(RunnerFailure, match="fresh-only"):
        asyncio.run(
            run_validate(
                args,
                RuntimeOptions(tmp_path / "run", 1_800, False, False, use_cache=True),
            )
        )


def test_negative_scenario_dry_run_rejects_cache_and_stays_side_effect_free(tmp_path) -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            SCENARIO_NAME,
            "--use-cache",
            "--dry-run",
            "--json",
            "--run-dir",
            str(tmp_path / "run"),
        ]
    )
    args.scenario = args.command
    run_dir = tmp_path / "run"
    payload = isolated_dry_run(
        args,
        RuntimeOptions(run_dir, 1_800, True, True, use_cache=True),
    )
    assert payload["cache"]["decision"] == "rejected_fresh_only"
    assert payload["ordered_steps"][0]["step"] == "preflight-fresh-only-rejects-cache"
    assert payload["expected_mcp_process_starts"] == 0
    assert "datetime_drift_negative" not in payload
    assert not run_dir.exists()


def test_trigger_matcher_accepts_only_exact_move_topology_event() -> None:
    exact = {
        "tool": "move_page",
        "read_reason": "topology_verification",
        "operation": "get_hierarchy",
    }
    assert is_exact_trigger_event(exact) is True
    assert is_exact_trigger_event({**exact, "tool": "copy_page"}) is False
    assert is_exact_trigger_event({**exact, "read_reason": "source_drift_revalidation"}) is False
    assert is_exact_trigger_event({**exact, "operation": "get_page_content"}) is False


def test_scan_ignores_topology_reads_before_datetime_verification() -> None:
    records = [
        {
            "tool": "move_page",
            "read_reason": "topology_verification",
            "operation": "get_hierarchy",
        },
        {
            "tool": "move_page",
            "read_reason": "final_target_readback",
            "operation": "get_page_content",
        },
        {
            "tool": "move_page",
            "read_reason": "topology_verification",
            "operation": "get_hierarchy",
        },
    ]
    scan = scan_trace_records(records)
    assert scan["datetime_verification_observed"] is True
    assert scan["trigger_index"] == 2
    assert scan["source_drift_observed"] is False


def test_wait_for_trigger_fail_closed_cases(tmp_path) -> None:
    async def _timeout() -> None:
        task = asyncio.create_task(asyncio.Event().wait())
        sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)

        try:
            with pytest.raises(InvariantFailure, match="Timed out"):
                await wait_for_datetime_drift_trigger(
                    tmp_path / "missing",
                    task,
                    timeout_seconds=1,
                    sleep=sleep,
                    clock=iter([0.0, 2.0]).__next__,
                )
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert sleeps == []

    async def _move_finished() -> None:
        async def finished() -> None:
            return None

        task = asyncio.create_task(finished())
        await asyncio.sleep(0)
        with pytest.raises(InvariantFailure, match="finished before"):
            await wait_for_datetime_drift_trigger(
                tmp_path / "empty",
                task,
                timeout_seconds=5,
                sleep=lambda _seconds: asyncio.sleep(0),
                clock=lambda: 0.0,
            )

    async def _multi_session() -> None:
        trace_dir = tmp_path / "multi"
        trace_dir.mkdir()
        (trace_dir / "session-a.jsonl").write_text("{}\n", encoding="utf-8")
        (trace_dir / "session-b.jsonl").write_text("{}\n", encoding="utf-8")
        task = asyncio.create_task(asyncio.Event().wait())
        try:
            with pytest.raises(InvariantFailure, match="more than one"):
                await wait_for_datetime_drift_trigger(
                    trace_dir,
                    task,
                    timeout_seconds=5,
                    sleep=lambda _seconds: asyncio.sleep(0),
                    clock=lambda: 0.0,
                )
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    async def _source_drift_first() -> None:
        trace_dir = tmp_path / "early-drift"
        trace_dir.mkdir()
        (trace_dir / "session-one.jsonl").write_text(
            json.dumps(
                {
                    "tool": "move_page",
                    "read_reason": "source_drift_revalidation",
                    "operation": "get_hierarchy",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        task = asyncio.create_task(asyncio.Event().wait())
        try:
            with pytest.raises(InvariantFailure, match="source_drift_revalidation"):
                await wait_for_datetime_drift_trigger(
                    trace_dir,
                    task,
                    timeout_seconds=5,
                    sleep=lambda _seconds: asyncio.sleep(0),
                    clock=lambda: 0.0,
                )
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(_timeout())
    asyncio.run(_move_finished())
    asyncio.run(_multi_session())
    asyncio.run(_source_drift_first())


def test_setter_and_readback_fail_closed_without_retry() -> None:
    class OnceClient:
        def __init__(self, payload: dict) -> None:
            self.payload = payload
            self.calls = 0

        async def call_tool(self, name: str, arguments: dict, **_kwargs) -> dict:
            self.calls += 1
            assert name == "set_verified_page_datetime"
            assert arguments["route"] == "update_hierarchy"
            return dict(self.payload)

    async def _run() -> None:
        drifted = OnceClient(
            {"status": "precondition_drifted", "mutation_dispatched": False}
        )
        with pytest.raises(InvariantFailure, match="precondition drifted"):
            await apply_datetime_drift_once(
                drifted,
                notebook_id="nb",
                page_id="page",
                expected_parent_id="parent",
                expected_hierarchy_modified="mod",
                expected_date_time=FROZEN_SECOND,
                next_second=NEXT_SECOND,
            )
        assert drifted.calls == 1

        failed = OnceClient(
            {"status": "write_failed", "mutation_dispatched": True}
        )
        with pytest.raises(InvariantFailure, match="write failed"):
            await apply_datetime_drift_once(
                failed,
                notebook_id="nb",
                page_id="page",
                expected_parent_id="parent",
                expected_hierarchy_modified="mod",
                expected_date_time=FROZEN_SECOND,
                next_second=NEXT_SECOND,
            )
        assert failed.calls == 1

        replayed = OnceClient(
            {
                "status": "dispatched",
                "mutation_dispatched": True,
                "mutation_attempts": 2,
                "mutation_replayed": True,
            }
        )
        with pytest.raises(InvariantFailure, match="retried or replayed"):
            await apply_datetime_drift_once(
                replayed,
                notebook_id="nb",
                page_id="page",
                expected_parent_id="parent",
                expected_hierarchy_modified="mod",
                expected_date_time=FROZEN_SECOND,
                next_second=NEXT_SECOND,
            )

        class ReadClient:
            def __init__(self) -> None:
                self.calls = 0

            async def call_tool(self, name: str, arguments: dict, **_kwargs) -> dict:
                self.calls += 1
                assert name == "read_verified_page_datetime"
                return {"status": "observed", "date_time": FROZEN_SECOND}

        reader = ReadClient()
        sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)

        with pytest.raises(InvariantFailure, match="next UTC second"):
            await confirm_next_utc_second(
                reader,
                notebook_id="nb",
                page_id="page",
                expected_second=NEXT_SECOND,
                sleep=sleep,
                attempts=2,
            )
        assert reader.calls == 2
        assert sleeps == [0.05]

    asyncio.run(_run())
    assert next_utc_second("2026-01-01T00:00:00.123+00:00") == NEXT_SECOND


def test_copy_only_envelope_and_layout_gates() -> None:
    inspected = require_copy_only_envelope(_copy_only_envelope())
    assert inspected["outcome"] == "copy_only"
    assert inspected["report_rewritten_source_drifted"] is False
    with pytest.raises(InvariantFailure, match="copy_only envelope"):
        require_copy_only_envelope(_copy_only_envelope(rewritten=True))

    before, after = _layout()
    layout = require_source_and_target_layout(
        before=before,
        after=after,
        source_ids=["subtree", "subtree-child"],
        id_map={"subtree": "target-subtree", "subtree-child": "target-subtree-child"},
        destination_section_id="destination-section",
        drift_source_id="subtree-child",
        expected_source_second=NEXT_SECOND,
        expected_target_second=FROZEN_SECOND,
    )
    assert layout["source_present"] is True
    missing_before, missing_after = _layout(missing_source=True)
    with pytest.raises(InvariantFailure, match="source Page is missing"):
        require_source_and_target_layout(
            before=missing_before,
            after=missing_after,
            source_ids=["subtree", "subtree-child"],
            id_map={"subtree": "target-subtree", "subtree-child": "target-subtree-child"},
            destination_section_id="destination-section",
            drift_source_id="subtree-child",
            expected_source_second=NEXT_SECOND,
            expected_target_second=FROZEN_SECOND,
        )
    extra_before, extra_after = _layout(extra_target=True)
    with pytest.raises(InvariantFailure, match="duplicate target"):
        require_source_and_target_layout(
            before=extra_before,
            after=extra_after,
            source_ids=["subtree", "subtree-child"],
            id_map={"subtree": "target-subtree", "subtree-child": "target-subtree-child"},
            destination_section_id="destination-section",
            drift_source_id="subtree-child",
            expected_source_second=NEXT_SECOND,
            expected_target_second=FROZEN_SECOND,
        )
    with pytest.raises(InvariantFailure, match="delete_hierarchy"):
        require_backend_counts(
            delete_count=1,
            create_count=2,
            expected_creates=2,
            move_submissions=1,
        )


def test_verified_evidence_raises_expected_negative_outcome() -> None:
    written: list[dict] = []
    original = ClientFailure("move_page failed (partial_failure): blocked")
    inspected = require_copy_only_envelope(_copy_only_envelope())
    evidence = build_negative_gate_evidence(
        source_ids=["subtree", "subtree-child"],
        drift_source_id="subtree-child",
        id_map={"subtree": "target-subtree", "subtree-child": "target-subtree-child"},
        original_utc_second=FROZEN_SECOND,
        drifted_utc_second=NEXT_SECOND,
        trigger_scan={
            "datetime_verification_observed": True,
            "trigger_observed": True,
            "trigger_before_source_drift": True,
        },
        setter={
            "status": "dispatched",
            "mutation_dispatched": True,
            "mutation_attempts": 1,
            "mutation_replayed": False,
            "route": "update_hierarchy",
        },
        envelope=inspected,
        layout={
            "source_present": True,
            "targets_present": True,
            "duplicate_targets": False,
            "source_hashes_unchanged": True,
            "source_utc_second": NEXT_SECOND,
            "target_utc_second": FROZEN_SECOND,
        },
        backend={
            "delete_hierarchy": 0,
            "create_new_page": 2,
            "move_page_submissions": 1,
        },
    )
    assert evidence["negative_gate_verified"] is True
    evidence_path = Path("datetime-drift-negative.json")
    with pytest.raises(ExpectedNegativeOutcome, match="partial_failure") as caught:
        write_negative_gate_and_raise_expected_outcome(
            written.append,
            evidence,
            original,
            evidence_path=evidence_path,
        )
    assert caught.value.original_error is original
    assert caught.value.evidence_path == evidence_path
    assert caught.value.exit_code == EXIT_MCP
    assert written[0]["negative_gate_verified"] is True
    assert written[0]["content_exposed"] is False


def test_execute_verifies_copy_only_then_raises_expected_negative(monkeypatch, tmp_path) -> None:
    source_notebook = {"resource_type": "notebook", "id": "source-notebook", "name": "Source"}
    destination_notebook = {
        "resource_type": "notebook",
        "id": "destination-notebook",
        "name": "Destination",
    }
    source_section = {
        "resource_type": "section",
        "id": "source-section",
        "name": "Source",
        "parent_id": "source-notebook",
    }
    destination = {
        "resource_type": "section",
        "id": "destination-section",
        "name": "Destination",
        "parent_id": "destination-notebook",
    }
    subtree = {
        "resource_type": "page",
        "id": "subtree",
        "title": "03-Subtree",
        "section_id": "source-section",
        "parent_id": "source-section",
        "parent_page_id": None,
        "page_level": 1,
        "order": 0,
        "modified": "before",
    }
    subtree_child = {
        **subtree,
        "id": "subtree-child",
        "title": "04-Subtree-Child",
        "parent_page_id": "subtree",
        "page_level": 2,
        "order": 1,
    }
    source_items = [source_notebook, source_section, subtree, subtree_child]
    dest_items = [destination_notebook, destination]
    hashes = {"subtree": "subtree-hash", "subtree-child": "child-hash"}
    after_move = False

    async def fake_snapshot(_client, notebook_id, **_kwargs):
        if notebook_id == "source-notebook":
            items = [dict(item) for item in source_items]
            seconds = {
                "subtree": FROZEN_SECOND,
                "subtree-child": NEXT_SECOND if after_move else FROZEN_SECOND,
            }
        else:
            items = [dict(item) for item in dest_items]
            if after_move:
                items.extend(
                    [
                        {
                            **subtree,
                            "id": "target-subtree",
                            "section_id": "destination-section",
                            "parent_id": "destination-section",
                        },
                        {
                            **subtree_child,
                            "id": "target-subtree-child",
                            "section_id": "destination-section",
                            "parent_id": "destination-section",
                        },
                    ]
                )
            seconds = {
                "target-subtree": FROZEN_SECOND,
                "target-subtree-child": FROZEN_SECOND,
            }
        page_ids = {item["id"] for item in items if item.get("resource_type") == "page"}
        return {
            "notebook_id": notebook_id,
            "items": items,
            "page_hashes": {key: value for key, value in hashes.items() if key in page_ids},
            "page_datetime_seconds": {
                key: value for key, value in seconds.items() if key in page_ids
            },
        }

    run_dir = tmp_path / "run"
    mcp_dir = run_dir / "scenario-mcp"
    mcp_dir.mkdir(parents=True)
    trace_dir = mcp_dir / "debug-trace"
    setter_done = asyncio.Event()
    original = ClientFailure(
        "move_page failed (partial_failure): source deletion was blocked",
        envelope=_copy_only_envelope(),
    )

    class FakeClient:
        policy = MOVE_PAGE_DATETIME_DRIFT_NEGATIVE_POLICY
        allowed_tools = MOVE_PAGE_DATETIME_DRIFT_NEGATIVE_TOOLS | {"health_check"}
        timeout_seconds = 1_800
        debug_trace_dir = trace_dir
        run_dir = mcp_dir
        setter_calls = 0

        async def call_tool(self, name: str, arguments: dict, **_kwargs):
            if name == "health_check":
                return {
                    "ok": True,
                    "debug_trace": {
                        "enabled": True,
                        "output_configured": True,
                        "writable": True,
                    },
                }
            if name == "read_verified_page_datetime":
                return {
                    "status": "observed",
                    "attribute_name": "dateTime",
                    "date_time": NEXT_SECOND if self.setter_calls else FROZEN_SECOND,
                }
            if name == "set_verified_page_datetime":
                self.setter_calls += 1
                assert arguments["date_time"] == NEXT_SECOND
                assert arguments["expected_date_time"] == FROZEN_SECOND
                setter_done.set()
                return {
                    "status": "dispatched",
                    "mutation_dispatched": True,
                    "mutation_attempts": 1,
                    "mutation_replayed": False,
                    "bridge_operation": "update_hierarchy",
                }
            if name == "move_page":
                with (mcp_dir / "calls.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"tool": "move_page"}) + "\n")
                await setter_done.wait()
                nonlocal after_move
                after_move = True
                with (mcp_dir / "bridge-calls.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"operation": "create_new_page"}) + "\n")
                    stream.write(json.dumps({"operation": "create_new_page"}) + "\n")
                raise original
            raise AssertionError(name)

    async def fake_wait(trace_dir_arg, move_task, **_kwargs):
        assert trace_dir_arg == trace_dir
        assert not move_task.done()
        trace_dir.mkdir(parents=True, exist_ok=True)
        session = trace_dir / "session-one.jsonl"
        records = [
            {
                "tool": "move_page",
                "read_reason": "final_source_revalidation",
                "operation": "get_page_content",
            },
            {
                "tool": "move_page",
                "read_reason": "topology_verification",
                "operation": "get_hierarchy",
            },
        ]
        session.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        return {
            "session_path": str(session),
            "scan": scan_trace_records(records),
            "records": records,
        }

    @asynccontextmanager
    async def fake_scenario_client(existing, **_kwargs):
        yield existing

    monkeypatch.setattr(
        "tests.manual_validation.scenarios.common.move_page_snapshot.capture_snapshot",
        fake_snapshot,
    )
    monkeypatch.setattr(
        "tests.manual_validation.scenarios.negative_move_page_datetime_drift.wait_for_datetime_drift_trigger",
        fake_wait,
    )
    monkeypatch.setattr(
        "tests.manual_validation.scenarios.negative_move_page_datetime_drift.scenario_client",
        fake_scenario_client,
    )

    manifest = {
        "schema_version": 1,
        "notebook": source_notebook,
        "notebooks": {"source": source_notebook, "destination": destination_notebook},
        "structure": {
            "subtree_page": subtree,
            "subtree_child": subtree_child,
            "destination_section": destination,
        },
    }
    client = FakeClient()
    with pytest.raises(ExpectedNegativeOutcome, match="partial_failure") as caught:
        asyncio.run(
            NegativeMovePageDatetimeDriftScenario().execute(
                SimpleNamespace(
                    notebook_name=None,
                    keep_worksite=False,
                ),
                RuntimeOptions(run_dir, 1_800, False, False),
                manifest,
                client=client,
                fixture_result={},
            )
        )
    assert caught.value.original_error is original
    evidence = read_json(
        run_dir / "scenarios" / SCENARIO_NAME / "datetime-drift-negative.json"
    )
    assert evidence["negative_gate_verified"] is True
    assert evidence["backend"]["delete_hierarchy"] == 0
    assert evidence["backend"]["create_new_page"] == 2
    assert evidence["backend"]["move_page_submissions"] == 1
    assert not (run_dir / "scenarios" / SCENARIO_NAME / "result.json").exists()
    assert client.setter_calls == 1


def test_parser_registers_independent_negative_scenario_only() -> None:
    args = build_parser().parse_args([SCENARIO_NAME])
    assert args.command == SCENARIO_NAME
    with pytest.raises(SystemExit):
        build_parser().parse_args(["move-page", "--datetime-drift-negative"])
    all_args = build_parser().parse_args(["all"])
    assert not hasattr(all_args, "datetime_drift_negative")
