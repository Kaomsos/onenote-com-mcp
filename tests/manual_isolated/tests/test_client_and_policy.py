"""Pure MCP client and static mutation-policy tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.manual_isolated.mcp_stdio_client import (
    ClientFailure,
    COPY_NO_DELETE_POLICY,
    COPY_POLICY,
    COPY_BUDGET_ENV,
    DELETE_POLICY,
    MOVE_POLICY,
    POLICY_ENV_NAMES,
    READ_ONLY_POLICY,
    RECONSTRUCTIVE_MOVE_PAGE_POLICY,
    WRITE_POLICY,
    MCPStdioClient,
    build_server_env,
    is_mutation_tool,
    parse_tool_result,
    summarize,
)

def test_static_policy_matrix_is_minimal() -> None:
    assert READ_ONLY_POLICY.as_dict() == {
        "writes_enabled": False,
        "deletes_enabled": False,
        "permanent_deletes_enabled": False,
        "experimental_move_section_enabled": False,
        "experimental_copy_enabled": False,
        "reconstructive_move_page_enabled": False,
        "raw_xml_enabled": False,
    }
    assert WRITE_POLICY.writes_enabled is True
    assert WRITE_POLICY.deletes_enabled is False
    assert MOVE_POLICY.writes_enabled is True
    assert MOVE_POLICY.experimental_move_section_enabled is True
    assert DELETE_POLICY.deletes_enabled is True
    assert DELETE_POLICY.writes_enabled is False
    assert COPY_POLICY.experimental_copy_enabled is True
    assert COPY_POLICY.deletes_enabled is True
    assert COPY_NO_DELETE_POLICY.deletes_enabled is False
    assert RECONSTRUCTIVE_MOVE_PAGE_POLICY.reconstructive_move_page_enabled is True
    for policy in (
        READ_ONLY_POLICY,
        WRITE_POLICY,
        MOVE_POLICY,
        DELETE_POLICY,
        COPY_POLICY,
        COPY_NO_DELETE_POLICY,
        RECONSTRUCTIVE_MOVE_PAGE_POLICY,
    ):
        assert policy.permanent_deletes_enabled is False
        assert policy.raw_xml_enabled is False

def test_child_env_overrides_hostile_parent_values(monkeypatch, tmp_path) -> None:
    for env_name in POLICY_ENV_NAMES.values():
        monkeypatch.setenv(env_name, "true")
    for env_name, _value in COPY_BUDGET_ENV.values():
        monkeypatch.setenv(env_name, "999999999")
    env = build_server_env(DELETE_POLICY, tmp_path / "temp", 1_800)
    assert env["LOCAL_ONENOTE_ENABLE_WRITES"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_DELETES"] == "true"
    assert env["LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_MOVE_SECTION"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_RAW_XML"] == "false"
    assert env["TEMP"] == env["TMP"]
    assert env["LOCAL_ONENOTE_MCP_TIMEOUT"] == "1800"
    for env_name, value in COPY_BUDGET_ENV.values():
        assert env[env_name] == str(value)

def test_non_read_only_tool_classification_never_retries_publish_or_copy() -> None:
    assert is_mutation_tool("publish_object") is True
    assert is_mutation_tool("copy_page") is True
    assert is_mutation_tool("reconstructive_move_page") is True
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
        allowed_tools={"reconstructive_move_page"},
        run_dir=tmp_path,
        timeout_seconds=10,
    )
    client._session = FakeSession()

    with pytest.raises(Exception) as caught:
        asyncio.run(client.call_tool("reconstructive_move_page", {}, retry_read=False))

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
