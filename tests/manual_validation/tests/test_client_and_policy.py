"""Pure MCP client and static mutation-policy tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.manual_validation.mcp_stdio_client import (
    ClientFailure,
    COPY_NO_DELETE_POLICY,
    COPY_POLICY,
    COPY_BUDGET_ENV,
    DELETE_POLICY,
    REPARENT_SECTION_POLICY,
    POLICY_ENV_NAMES,
    READ_ONLY_POLICY,
    REPARENT_PROBE_POLICY,
    REORDER_SECTION_GROUP_POLICY,
    REORDER_SECTION_POLICY,
    MOVE_PAGE_POLICY,
    WRITE_POLICY,
    MCPStdioClient,
    build_server_env,
    is_mutation_tool,
    parse_tool_result,
    scenario_client,
    summarize,
)


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
            policy=REPARENT_SECTION_POLICY,
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
        "experimental_reparent_section_enabled": False,
        "experimental_reorder_section_enabled": False,
        "experimental_reorder_section_group_enabled": False,
        "experimental_copy_enabled": False,
        "move_page_enabled": False,
        "raw_xml_enabled": False,
    }
    assert WRITE_POLICY.writes_enabled is True
    assert WRITE_POLICY.deletes_enabled is False
    assert REPARENT_SECTION_POLICY.writes_enabled is True
    assert REPARENT_SECTION_POLICY.experimental_reparent_section_enabled is True
    assert REORDER_SECTION_POLICY.experimental_reorder_section_enabled is True
    assert REORDER_SECTION_POLICY.experimental_reorder_section_group_enabled is False
    assert REORDER_SECTION_GROUP_POLICY.experimental_reorder_section_group_enabled is True
    assert REORDER_SECTION_GROUP_POLICY.experimental_reorder_section_enabled is False
    assert REPARENT_PROBE_POLICY.writes_enabled is True
    assert REPARENT_PROBE_POLICY.raw_xml_enabled is True
    assert REPARENT_PROBE_POLICY.deletes_enabled is False
    assert DELETE_POLICY.deletes_enabled is True
    assert DELETE_POLICY.writes_enabled is False
    assert COPY_POLICY.experimental_copy_enabled is True
    assert COPY_POLICY.deletes_enabled is True
    assert COPY_NO_DELETE_POLICY.deletes_enabled is False
    assert MOVE_PAGE_POLICY.move_page_enabled is True
    for policy in (
        READ_ONLY_POLICY,
        WRITE_POLICY,
        REPARENT_SECTION_POLICY,
        REORDER_SECTION_POLICY,
        REORDER_SECTION_GROUP_POLICY,
        REPARENT_PROBE_POLICY,
        DELETE_POLICY,
        COPY_POLICY,
        COPY_NO_DELETE_POLICY,
        MOVE_PAGE_POLICY,
    ):
        assert policy.permanent_deletes_enabled is False
        assert policy.raw_xml_enabled is (policy is REPARENT_PROBE_POLICY)

def test_child_env_overrides_hostile_parent_values(monkeypatch, tmp_path) -> None:
    for env_name in POLICY_ENV_NAMES.values():
        monkeypatch.setenv(env_name, "true")
    for env_name, _value in COPY_BUDGET_ENV.values():
        monkeypatch.setenv(env_name, "999999999")
    audit_path = tmp_path / "audit" / "bridge.jsonl"
    env = build_server_env(DELETE_POLICY, tmp_path / "temp", 1_800, audit_path)
    assert env["LOCAL_ONENOTE_ENABLE_WRITES"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_DELETES"] == "true"
    assert env["LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT_SECTION"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION_GROUP"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_MOVE_PAGE"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_RAW_XML"] == "false"
    assert env["TEMP"] == env["TMP"]
    assert env["LOCAL_ONENOTE_MCP_TIMEOUT"] == "1800"
    assert env["LOCAL_ONENOTE_BRIDGE_AUDIT_PATH"] == str(audit_path.resolve())
    for env_name, value in COPY_BUDGET_ENV.values():
        assert env[env_name] == str(value)


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
