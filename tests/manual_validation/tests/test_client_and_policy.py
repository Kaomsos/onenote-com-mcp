"""Pure MCP client and static mutation-policy tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.manual_validation.mcp_stdio_client import (
    ClientFailure,
    COPY_NO_DELETE_POLICY,
    COPY_POLICY,
    COPY_BUDGET_ENV,
    SEARCH_BUDGET_ENV,
    DELETE_POLICY,
    REPARENT_POLICY,
    POLICY_ENV_NAMES,
    READ_ONLY_POLICY,
    REORDER_SECTION_GROUP_POLICY,
    REORDER_SECTION_POLICY,
    MOVE_PAGE_POLICY,
    MOVE_CONTAINERS_POLICY,
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

def test_static_policy_matrix_is_minimal() -> None:
    assert READ_ONLY_POLICY.as_dict() == {
        "writes_enabled": False,
        "deletes_enabled": False,
        "permanent_deletes_enabled": False,
        "experimental_reparent_enabled": False,
        "experimental_reorder_section_enabled": False,
        "experimental_reorder_section_group_enabled": False,
        "experimental_copy_enabled": False,
        "move_page_enabled": False,
        "move_containers_enabled": False,
        "raw_xml_enabled": False,
    }
    assert WRITE_POLICY.writes_enabled is True
    assert WRITE_POLICY.deletes_enabled is False
    assert REPARENT_POLICY.writes_enabled is True
    assert REPARENT_POLICY.experimental_reparent_enabled is True
    assert REORDER_SECTION_POLICY.experimental_reorder_section_enabled is True
    assert REORDER_SECTION_POLICY.experimental_reorder_section_group_enabled is False
    assert REORDER_SECTION_GROUP_POLICY.experimental_reorder_section_group_enabled is True
    assert REORDER_SECTION_GROUP_POLICY.experimental_reorder_section_enabled is False
    assert DELETE_POLICY.deletes_enabled is True
    assert DELETE_POLICY.writes_enabled is False
    assert COPY_POLICY.experimental_copy_enabled is True
    assert COPY_POLICY.deletes_enabled is True
    assert COPY_NO_DELETE_POLICY.deletes_enabled is False
    assert MOVE_PAGE_POLICY.move_page_enabled is True
    assert MOVE_CONTAINERS_POLICY.move_containers_enabled is True
    for policy in (
        READ_ONLY_POLICY,
        WRITE_POLICY,
        REPARENT_POLICY,
        REORDER_SECTION_POLICY,
        REORDER_SECTION_GROUP_POLICY,
        DELETE_POLICY,
        COPY_POLICY,
        COPY_NO_DELETE_POLICY,
        MOVE_PAGE_POLICY,
        MOVE_CONTAINERS_POLICY,
    ):
        assert policy.permanent_deletes_enabled is False
        assert policy.raw_xml_enabled is False

def test_child_env_overrides_hostile_parent_values(monkeypatch, tmp_path) -> None:
    for env_name in POLICY_ENV_NAMES.values():
        monkeypatch.setenv(env_name, "true")
    for env_name, _value in COPY_BUDGET_ENV.values():
        monkeypatch.setenv(env_name, "999999999")
    for env_name, _value in SEARCH_BUDGET_ENV.values():
        monkeypatch.setenv(env_name, "999999999")
    audit_path = tmp_path / "audit" / "bridge.jsonl"
    env = build_server_env(DELETE_POLICY, tmp_path / "temp", 1_800, audit_path)
    assert env["LOCAL_ONENOTE_ENABLE_WRITES"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_DELETES"] == "true"
    assert env["LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION_GROUP"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_MOVE_PAGE"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_RAW_XML"] == "false"
    assert env["TEMP"] == env["TMP"]
    assert env["LOCAL_ONENOTE_MCP_TIMEOUT"] == "1800"
    assert env["LOCAL_ONENOTE_BRIDGE_AUDIT_PATH"] == str(audit_path.resolve())
    for env_name, value in COPY_BUDGET_ENV.values():
        assert env[env_name] == str(value)
    for env_name, value in SEARCH_BUDGET_ENV.values():
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


def test_bridge_audit_path_cannot_leak_from_parent_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCAL_ONENOTE_BRIDGE_AUDIT_PATH", "untrusted-parent-path")
    env = build_server_env(READ_ONLY_POLICY, tmp_path / "temp")
    assert "LOCAL_ONENOTE_BRIDGE_AUDIT_PATH" not in env

def test_non_read_only_tool_classification_never_retries_publish_or_copy() -> None:
    assert is_mutation_tool("publish_object") is True
    assert is_mutation_tool("copy_page") is True
    assert is_mutation_tool("move_page") is True
    assert is_mutation_tool("plan_copy") is False
    assert is_mutation_tool("get_page_xml") is False

def test_audit_summary_redacts_page_payloads() -> None:
    result = summarize({"xml": "<xml>secret</xml>", "content": "private", "id": "safe-id"})
    assert result["xml"]["redacted"] is True
    assert result["content"]["redacted"] is True
    assert result["id"] == "safe-id"
    assert "secret" not in str(result)
    assert "private" not in str(result)

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

def test_call_audit_has_start_and_completion_timestamps(tmp_path) -> None:
    class FakeSession:
        async def call_tool(self, *_args, **_kwargs):
            return SimpleNamespace(
                isError=False,
                structuredContent={"result": {"ok": True, "complete": True}},
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
