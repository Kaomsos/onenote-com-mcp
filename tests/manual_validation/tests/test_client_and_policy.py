"""Pure MCP client and static mutation-policy tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from local_onenote_mcp.com_client import ComRefreshResult
from tests.manual_validation.mcp_stdio_client import (
    BATCH_MUTATION_BUDGET_ENV,
    ClientFailure,
    COPY_NO_DELETE_POLICY,
    COPY_POLICY,
    COPY_BUDGET_ENV,
    SEARCH_BUDGET_ENV,
    DELETE_POLICY,
    REPARENT_POLICY,
    RICH_COPY_NO_DELETE_POLICY,
    RICH_COPY_NOTEBOOK_POLICY,
    RICH_COPY_POLICY,
    RICH_REPARENT_POLICY,
    RICH_WRITE_POLICY,
    POLICY_ENV_NAMES,
    READ_ONLY_POLICY,
    REORDER_SECTION_GROUP_POLICY,
    REORDER_SECTION_POLICY,
    MOVE_PAGE_POLICY,
    MOVE_CONTAINERS_POLICY,
    TIMESTAMP_FIDELITY_POLICY,
    WRITE_POLICY,
    MCPStdioClient,
    build_server_env,
    is_mutation_tool,
    parse_tool_result,
    scenario_client,
    summarize,
)
from tests.manual_validation.test_utils import capture_snapshot, read_json


def test_scenario_client_reuses_existing_process_without_factory(tmp_path) -> None:
    existing = MCPStdioClient(
        policy=WRITE_POLICY,
        allowed_tools={"create_section", "rename_section"},
        run_dir=tmp_path,
        timeout_seconds=10,
    )

    class ForbiddenFactory:
        def __init__(self, **_kwargs):
            raise AssertionError("existing scenario client must be reused")

    async def exercise():
        async with scenario_client(
            existing,
            policy=WRITE_POLICY,
            allowed_tools={"rename_section"},
            run_dir=tmp_path,
            timeout_seconds=10,
            client_factory=ForbiddenFactory,
        ) as selected:
            assert selected is existing

    asyncio.run(exercise())


def test_scenario_client_rejects_runtime_permission_expansion(tmp_path) -> None:
    existing = MCPStdioClient(
        policy=WRITE_POLICY,
        allowed_tools={"rename_section"},
        run_dir=tmp_path,
        timeout_seconds=10,
    )

    async def exercise():
        async with scenario_client(
            existing,
            policy=REPARENT_POLICY,
            allowed_tools={"rename_section"},
            run_dir=tmp_path,
            timeout_seconds=10,
        ):
            pass

    with pytest.raises(ClientFailure, match="cannot satisfy required permissions"):
        asyncio.run(exercise())


def test_scenario_client_rejects_timestamp_probe_gate_expansion(tmp_path) -> None:
    existing = MCPStdioClient(
        policy=WRITE_POLICY,
        allowed_tools={"set_verified_page_datetime", "health_check"},
        run_dir=tmp_path,
        timeout_seconds=10,
    )

    async def exercise():
        async with scenario_client(
            existing,
            policy=TIMESTAMP_FIDELITY_POLICY,
            allowed_tools={"set_verified_page_datetime"},
            run_dir=tmp_path,
            timeout_seconds=10,
        ):
            pass

    with pytest.raises(ClientFailure, match="timestamp_fidelity_probe_enabled"):
        asyncio.run(exercise())


def test_static_policy_matrix_is_minimal() -> None:
    assert READ_ONLY_POLICY.as_dict() == {
        "writes_enabled": False,
        "deletes_enabled": False,
        "organize_enabled": False,
        "create_enabled": False,
        "local_file_io_enabled": False,
        "ui_control_enabled": False,
        "notebook_lifecycle_enabled": False,
    }
    assert WRITE_POLICY.writes_enabled is True
    assert WRITE_POLICY.deletes_enabled is False
    assert REPARENT_POLICY.writes_enabled is True
    assert REPARENT_POLICY.organize_enabled is True
    assert RICH_WRITE_POLICY.local_file_io_enabled is True
    assert RICH_REPARENT_POLICY.organize_enabled is True
    assert RICH_REPARENT_POLICY.local_file_io_enabled is True
    assert REORDER_SECTION_POLICY.as_dict() == WRITE_POLICY.as_dict()
    assert REORDER_SECTION_GROUP_POLICY.as_dict() == WRITE_POLICY.as_dict()
    assert DELETE_POLICY.deletes_enabled is True
    assert DELETE_POLICY.writes_enabled is False
    assert COPY_POLICY.create_enabled is True
    assert COPY_POLICY.deletes_enabled is True
    assert COPY_NO_DELETE_POLICY.deletes_enabled is False
    assert RICH_COPY_POLICY.local_file_io_enabled is True
    assert RICH_COPY_NO_DELETE_POLICY.local_file_io_enabled is True
    assert RICH_COPY_NO_DELETE_POLICY.deletes_enabled is False
    assert RICH_COPY_NOTEBOOK_POLICY.create_enabled is True
    assert RICH_COPY_NOTEBOOK_POLICY.local_file_io_enabled is True
    assert RICH_COPY_NOTEBOOK_POLICY.notebook_lifecycle_enabled is True
    assert RICH_COPY_NOTEBOOK_POLICY.deletes_enabled is False
    assert MOVE_PAGE_POLICY.create_enabled is True
    assert MOVE_CONTAINERS_POLICY.create_enabled is True
    for policy in (
        READ_ONLY_POLICY,
        WRITE_POLICY,
        REPARENT_POLICY,
        REORDER_SECTION_POLICY,
        REORDER_SECTION_GROUP_POLICY,
        DELETE_POLICY,
        COPY_POLICY,
        COPY_NO_DELETE_POLICY,
        RICH_WRITE_POLICY,
        RICH_REPARENT_POLICY,
        RICH_COPY_POLICY,
        RICH_COPY_NO_DELETE_POLICY,
        RICH_COPY_NOTEBOOK_POLICY,
        MOVE_PAGE_POLICY,
        MOVE_CONTAINERS_POLICY,
    ):
        assert set(policy.as_dict()) == set(POLICY_ENV_NAMES)


def test_client_failure_reads_nested_public_error_envelope() -> None:
    failure = ClientFailure(
        "tool failed",
        envelope={
            "ok": False,
            "error": {"code": "validation_error", "message": "bounded failure"},
        },
    )

    assert failure.error_code == "validation_error"
    assert failure.error_message == "bounded failure"

def test_child_env_overrides_hostile_parent_values(monkeypatch, tmp_path) -> None:
    for env_name in POLICY_ENV_NAMES.values():
        monkeypatch.setenv(env_name, "true")
    for env_name, _value in COPY_BUDGET_ENV.values():
        monkeypatch.setenv(env_name, "999999999")
    for env_name, _value in SEARCH_BUDGET_ENV.values():
        monkeypatch.setenv(env_name, "999999999")
    for env_name, _value in BATCH_MUTATION_BUDGET_ENV.values():
        monkeypatch.setenv(env_name, "999999999")
    audit_path = tmp_path / "audit" / "bridge.jsonl"
    env = build_server_env(DELETE_POLICY, tmp_path / "temp", 1_800, audit_path)
    assert env["LOCAL_ONENOTE_ENABLE_WRITES"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_DELETES"] == "true"
    assert env["LOCAL_ONENOTE_ENABLE_ORGANIZE"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_CREATE"] == "false"
    assert "LOCAL_ONENOTE_ENABLE_COPY" not in env
    assert env["LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_UI_CONTROL"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE"] == "false"
    assert env["TEMP"] == env["TMP"]
    assert env["LOCAL_ONENOTE_MCP_TIMEOUT"] == "1800"
    assert env["LOCAL_ONENOTE_BRIDGE_ADAPTER"] == "persistent_powershell"
    assert env["LOCAL_ONENOTE_BRIDGE_AUDIT_PATH"] == str(audit_path.resolve())
    for env_name, value in COPY_BUDGET_ENV.values():
        assert env[env_name] == str(value)
    for env_name, value in SEARCH_BUDGET_ENV.values():
        assert env[env_name] == str(value)
    for env_name, value in BATCH_MUTATION_BUDGET_ENV.values():
        assert env[env_name] == str(value)


def test_child_env_applies_static_search_budget_override(monkeypatch, tmp_path) -> None:
    overrides = {"max_pages": 4, "max_total_chars": 512}

    env = build_server_env(
        READ_ONLY_POLICY,
        tmp_path / "temp",
        search_budget=overrides,
    )

    assert env["LOCAL_ONENOTE_MAX_SEARCH_PAGES"] == "4"
    assert env["LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS"] == "512"
    assert env["LOCAL_ONENOTE_MAX_SEARCH_PAGE_CHARS"] == "100000"


def test_child_env_applies_static_batch_mutation_budget_override(tmp_path) -> None:
    env = build_server_env(
        DELETE_POLICY,
        tmp_path / "temp",
        batch_mutation_budget={"max_effective_pages": 3},
    )

    assert env["LOCAL_ONENOTE_MAX_BATCH_EFFECTIVE_PAGES"] == "3"
    assert env["LOCAL_ONENOTE_MAX_BATCH_EFFECTIVE_RESOURCES"] == "1000"


def test_bridge_audit_path_cannot_leak_from_parent_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_ONENOTE_BRIDGE_AUDIT_PATH", "untrusted-parent-path")
    env = build_server_env(READ_ONLY_POLICY, tmp_path / "temp")
    assert "LOCAL_ONENOTE_BRIDGE_AUDIT_PATH" not in env


def test_child_env_pins_bridge_adapter_over_parent(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_ONENOTE_BRIDGE_ADAPTER", "one_shot_powershell")
    env = build_server_env(READ_ONLY_POLICY, tmp_path / "temp")
    assert env["LOCAL_ONENOTE_BRIDGE_ADAPTER"] == "persistent_powershell"

def test_non_read_only_tool_classification_never_retries_publish_or_copy() -> None:
    assert is_mutation_tool("export_object_to_pdf") is True
    assert is_mutation_tool("copy_page") is True
    assert is_mutation_tool("move_page") is True
    assert is_mutation_tool("sort_children") is True
    assert is_mutation_tool("get_page_xml") is False

def test_audit_summary_redacts_page_payloads() -> None:
    result = summarize(
        {
            "xml": "<xml>secret</xml>",
            "content": "private",
            "html": "<p>short rich secret</p>",
            "id": "safe-id",
        }
    )
    assert result["xml"]["redacted"] is True
    assert result["content"]["redacted"] is True
    assert result["html"]["redacted"] is True
    assert result["id"] == "safe-id"
    assert "secret" not in str(result)
    assert "private" not in str(result)
    assert "short rich secret" not in str(result)

def test_tool_result_prefers_structured_envelope() -> None:
    result = SimpleNamespace(
        isError=False,
        structuredContent={"result": {"ok": True, "complete": True, "item": {"id": "x"}}},
        content=[],
    )
    assert parse_tool_result(result)["item"]["id"] == "x"

def test_client_failure_preserves_structured_partial_envelope(tmp_path) -> None:
    partial = {
        "ok": False,
        "complete": False,
        "code": "partial_failure",
        "outcome": "copy_only",
        "created_ids": ["new-page"],
        "copy_report": {"id_map": {"old-page": "new-page"}},
        "error": "source deletion was blocked",
    }

    class FakeSession:
        async def call_tool(self, *_args, **_kwargs):
            return SimpleNamespace(
                isError=False,
                structuredContent={"result": partial},
                content=[],
            )

    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"move_page"},
        run_dir=tmp_path,
        timeout_seconds=10,
    )
    client._session = FakeSession()

    with pytest.raises(Exception) as caught:
        asyncio.run(client.call_tool("move_page", {}, retry_read=False))

    assert isinstance(caught.value, ClientFailure)
    assert caught.value.envelope == partial


def test_client_accepts_success_when_domain_completion_is_unobservable(tmp_path) -> None:
    accepted = {
        "ok": True,
        "complete": False,
        "accepted": True,
        "completion_observable": False,
    }

    class FakeSession:
        async def call_tool(self, *_args, **_kwargs):
            return SimpleNamespace(
                isError=False,
                structuredContent={
                    "result": {
                        "ok": True,
                        "result": accepted,
                        "warnings": [],
                        "execution": {},
                    }
                },
                content=[],
            )

    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"health_check"},
        run_dir=tmp_path,
        timeout_seconds=10,
    )
    client._session = FakeSession()

    result = asyncio.run(client.call_tool("health_check", {}, retry_read=False))

    assert result == {
        **accepted,
        "warnings": [],
        "execution": {},
    }


def test_client_preserves_success_envelope_execution_when_flattening_payload(tmp_path) -> None:
    execution = {
        "operation": "request_notebook_sync",
        "kind": "lifecycle",
        "backend_category": "onenote_com",
        "observed_outcome": "accepted_completion_unobservable",
    }

    class FakeSession:
        async def call_tool(self, *_args, **_kwargs):
            return SimpleNamespace(
                isError=False,
                structuredContent={
                    "result": {
                        "ok": True,
                        "result": {
                            "accepted": True,
                            "complete": False,
                            "completion_observable": False,
                        },
                        "warnings": ["completion is not observable"],
                        "execution": execution,
                    }
                },
                content=[],
            )

    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"request_notebook_sync"},
        run_dir=tmp_path,
        timeout_seconds=10,
    )
    client._session = FakeSession()

    result = asyncio.run(
        client.call_tool("request_notebook_sync", {}, retry_read=False)
    )

    assert result == {
        "accepted": True,
        "complete": False,
        "completion_observable": False,
        "ok": True,
        "warnings": ["completion is not observable"],
        "execution": execution,
    }


def test_health_preflight_admits_only_exact_fully_stopped_typed_failure(tmp_path) -> None:
    envelope = {
        "ok": False,
        "error": {
            "code": "onenote_desktop_not_running",
            "message": "start OneNote and retry",
            "details": {
                "onenote_desktop": {
                    "process_running": False,
                    "visible_window_present": False,
                    "ready": False,
                    "probe": "native_windows_process_and_visible_window",
                }
            },
        },
        "execution": {
            "operation": "health_check",
            "stage": "execute",
            "backend_calls": 0,
        },
    }

    class FakeSession:
        async def call_tool(self, *_args, **_kwargs):
            return SimpleNamespace(
                isError=False,
                structuredContent={"result": envelope},
                content=[],
            )

    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"health_check"},
        run_dir=tmp_path,
        timeout_seconds=10,
        require_desktop_ready=False,
    )
    client._session = FakeSession()

    health = asyncio.run(
        client.call_health_preflight(allow_desktop_not_running=True)
    )

    assert health["onenote_desktop"]["ready"] is False
    assert health["expected_failure_envelope"] == envelope
    assert client.health_failure_envelope == envelope

    with pytest.raises(ClientFailure, match="health_check failed"):
        asyncio.run(
            client.call_health_preflight(allow_desktop_not_running=False)
        )


def test_health_preflight_rejects_process_only_state_even_in_cold_start_mode(tmp_path) -> None:
    class FakeSession:
        async def call_tool(self, *_args, **_kwargs):
            return SimpleNamespace(
                isError=False,
                structuredContent={
                    "result": {
                        "ok": False,
                        "error": {
                            "code": "onenote_desktop_not_running",
                            "message": "not ready",
                            "details": {
                                "onenote_desktop": {
                                    "process_running": True,
                                    "visible_window_present": False,
                                    "ready": False,
                                }
                            },
                        },
                        "execution": {
                            "operation": "health_check",
                            "backend_calls": 0,
                        },
                    }
                },
                content=[],
            )

    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"health_check"},
        run_dir=tmp_path,
        timeout_seconds=10,
        require_desktop_ready=False,
    )
    client._session = FakeSession()

    with pytest.raises(ClientFailure, match="health_check failed"):
        asyncio.run(
            client.call_health_preflight(allow_desktop_not_running=True)
        )


def test_call_audit_has_start_and_completion_timestamps(tmp_path) -> None:
    class FakeSession:
        async def call_tool(self, *_args, **_kwargs):
            return SimpleNamespace(
                isError=False,
                structuredContent={
                    "result": {
                        "ok": True,
                        "result": {"complete": True},
                        "warnings": [],
                        "execution": {},
                    }
                },
                content=[],
            )

    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"health_check"},
        run_dir=tmp_path,
        timeout_seconds=10,
    )
    client._session = FakeSession()
    asyncio.run(client.call_tool("health_check", {}, retry_read=False))
    audit = (tmp_path / "calls.jsonl").read_text(encoding="utf-8")
    assert '"started_at"' in audit
    assert '"completed_at"' in audit


def test_runtime_log_persistence_can_be_disabled_for_terminal_only_check(tmp_path) -> None:
    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"health_check"},
        run_dir=tmp_path,
        timeout_seconds=10,
        persist_runtime_logs=False,
    )
    client._append_audit({"tool": "health_check", "result": {"ok": True}})

    assert not (tmp_path / "calls.jsonl").exists()
    assert client._internal_bridge.audit_path is None


def test_materialized_scenario_before_snapshot_handoff_is_exact_and_single_use(
    tmp_path,
) -> None:
    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"expand_hierarchy", "get_page_xml"},
        run_dir=tmp_path / "mcp",
        timeout_seconds=10,
    )
    snapshot = {
        "captured_at": "2026-08-14T00:00:00+00:00",
        "notebook_id": "notebook-source",
        "items": [{"id": "section", "resource_type": "section"}],
        "page_hashes": {},
        "page_objects": {},
    }
    evidence_path = tmp_path / "scenario-before-snapshot-handoff.json"
    client.stage_scenario_before_snapshots(
        {"source": snapshot},
        {"source": "notebook-source"},
        evidence_path,
    )
    client.call_tool = AsyncMock(side_effect=AssertionError("handoff must avoid COM reads"))

    first = asyncio.run(capture_snapshot(client, "notebook-source"))

    assert first == snapshot
    assert client.call_tool.await_count == 0
    evidence = read_json(evidence_path)
    assert evidence["status"] == "consumed"
    assert evidence["roles"]["source"]["consumed"] is True
    assert evidence["roles"]["source"]["snapshot_sha256"]


def test_materialized_scenario_before_snapshot_rejects_wrong_notebook(tmp_path) -> None:
    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"expand_hierarchy", "get_page_xml"},
        run_dir=tmp_path / "mcp",
        timeout_seconds=10,
    )
    client.stage_scenario_before_snapshots(
        {"source": {"notebook_id": "notebook-source", "items": [], "page_hashes": {}}},
        {"source": "notebook-source"},
        tmp_path / "scenario-before-snapshot-handoff.json",
    )

    with pytest.raises(ClientFailure, match="unbound Notebook"):
        asyncio.run(capture_snapshot(client, "notebook-other"))


def test_materialized_multi_role_handoff_requires_each_exact_snapshot(tmp_path) -> None:
    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"expand_hierarchy", "get_page_xml"},
        run_dir=tmp_path / "mcp",
        timeout_seconds=10,
    )
    evidence_path = tmp_path / "scenario-before-snapshot-handoff.json"
    snapshots = {
        role: {
            "notebook_id": f"notebook-{role}",
            "items": [],
            "page_hashes": {},
        }
        for role in ("destination", "source")
    }
    client.stage_scenario_before_snapshots(
        snapshots,
        {role: str(snapshot["notebook_id"]) for role, snapshot in snapshots.items()},
        evidence_path,
    )

    assert asyncio.run(capture_snapshot(client, "notebook-source")) == snapshots["source"]
    partial = read_json(evidence_path)
    assert partial["status"] == "partially_consumed"
    assert partial["remaining_roles"] == 1

    assert asyncio.run(capture_snapshot(client, "notebook-destination")) == snapshots["destination"]
    complete = read_json(evidence_path)
    assert complete["status"] == "consumed"
    assert complete["remaining_roles"] == 0


def test_first_mutation_is_blocked_with_unconsumed_scenario_before_snapshot(tmp_path) -> None:
    class FakeSession:
        calls = 0

        async def call_tool(self, *_args, **_kwargs):
            self.calls += 1
            return SimpleNamespace(
                isError=False,
                structuredContent={"result": {"ok": True, "complete": True}},
                content=[],
            )

    client = MCPStdioClient(
        policy=WRITE_POLICY,
        allowed_tools={"create_page"},
        run_dir=tmp_path / "mcp",
        timeout_seconds=10,
    )
    client.run_dir.mkdir(parents=True)
    session = FakeSession()
    client._session = session
    evidence_path = tmp_path / "scenario-before-snapshot-handoff.json"
    client.stage_scenario_before_snapshots(
        {"source": {"notebook_id": "notebook-source", "items": [], "page_hashes": {}}},
        {"source": "notebook-source"},
        evidence_path,
    )

    with pytest.raises(ClientFailure, match="had not been consumed"):
        asyncio.run(client.call_tool("create_page", {}, retry_read=False))

    evidence = read_json(evidence_path)
    assert evidence["status"] == "discarded"
    assert evidence["discard_reason"] == (
        "mutation_blocked_before_snapshot_consumption:create_page"
    )
    assert session.calls == 0

def test_protocol_level_tool_error_is_audited_once(tmp_path) -> None:
    class FakeSession:
        async def call_tool(self, *_args, **_kwargs):
            return SimpleNamespace(isError=True, structuredContent=None, content=[])

    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"health_check"},
        run_dir=tmp_path,
        timeout_seconds=10,
    )
    client._session = FakeSession()

    with pytest.raises(ClientFailure):
        asyncio.run(client.call_tool("health_check", {}, retry_read=False))

    records = (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(records) == 1
    assert '"client_error"' in records[0]


class _StaleUntilRefreshInternalBridge:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self.page_calls = 0
        self.refreshed = False
        self.adapter_id = "persistent_powershell"
        self.audit_path = None
        self.generation = 1

    def refresh_com_client(self, **_: object) -> ComRefreshResult:
        self.refresh_calls += 1
        self.refreshed = True
        return ComRefreshResult(outcome="refreshed", generation=1, com_epoch=2)

    def call(self, operation: str, **_kwargs):
        self.page_calls += 1
        if operation == "get_page_content" and not self.refreshed:
            raise RuntimeError("RPC server unavailable (0x800706BA)")
        return {"xml": "<one:Page/>"}

    def close(self) -> None:
        return None


class _LaunchSession:
    def __init__(self) -> None:
        self.names: list[str] = []

    async def call_tool(self, name: str, _arguments, **_kwargs):
        self.names.append(name)
        return SimpleNamespace(
            isError=False,
            structuredContent={
                "result": {
                    "ok": True,
                    "result": {
                        "status": "started",
                        "launch_attempted": True,
                        "launch_attempts": 1,
                        "ready": True,
                        "com_client_refresh": {
                            "outcome": "refreshed",
                            "generation": 1,
                            "com_epoch": 2,
                        },
                    },
                    "warnings": [],
                    "execution": {},
                }
            },
            content=[],
        )


def test_mcp_launch_and_internal_bridge_lifecycles_are_independent(tmp_path) -> None:
    session = _LaunchSession()
    internal = _StaleUntilRefreshInternalBridge()
    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"launch_onenote_gui", "get_page_xml"},
        run_dir=tmp_path,
        timeout_seconds=10,
        persist_runtime_logs=False,
    )
    client._session = session
    client._internal_bridge = internal

    launched = asyncio.run(client.call_tool("launch_onenote_gui", {}, retry_read=False))

    assert launched["com_client_refresh"]["outcome"] == "refreshed"
    assert session.names == ["launch_onenote_gui"]
    assert internal.refresh_calls == 0
    assert internal.page_calls == 0
    with pytest.raises(ClientFailure, match="0x800706BA"):
        asyncio.run(
            client.call_tool("get_page_xml", {"page_id": "page-id"}, retry_read=False)
        )
    assert internal.refresh_calls == 0
    assert internal.page_calls == 1

    projection = client.refresh_internal_com_client()
    assert projection == {"outcome": "refreshed", "generation": 1, "com_epoch": 2}
    assert internal.refresh_calls == 1
    assert session.names == ["launch_onenote_gui"]

    xml = asyncio.run(
        client.call_tool("get_page_xml", {"page_id": "page-id"}, retry_read=False)
    )
    assert xml == {"xml": "<one:Page/>"}
    assert internal.page_calls == 2
    assert internal.refresh_calls == 1


def test_stale_internal_proxy_requires_explicit_refresh_before_xml_read(tmp_path) -> None:
    internal = _StaleUntilRefreshInternalBridge()
    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"get_page_xml"},
        run_dir=tmp_path,
        timeout_seconds=10,
        persist_runtime_logs=False,
    )
    client._session = SimpleNamespace()
    client._internal_bridge = internal

    with pytest.raises(ClientFailure, match="0x800706BA"):
        asyncio.run(
            client.call_tool("get_page_xml", {"page_id": "page-id"}, retry_read=False)
        )
    assert internal.refresh_calls == 0

    client.refresh_internal_com_client()
    xml = asyncio.run(
        client.call_tool("get_page_xml", {"page_id": "page-id"}, retry_read=False)
    )
    assert xml["xml"] == "<one:Page/>"
    assert internal.refresh_calls == 1
    assert internal.page_calls == 2
