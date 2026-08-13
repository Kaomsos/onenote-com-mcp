"""Content-free audit evidence for run-scoped OneNote bridge calls."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from local_onenote_mcp import bridge as bridge_module
from local_onenote_mcp.bridge import OneNoteBridge
from local_onenote_mcp.onenote_errors import OneNoteModalUIBlockedError


def test_bridge_audit_records_operation_without_params_or_result(monkeypatch, tmp_path) -> None:
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    audit_path = tmp_path / "bridge-calls.jsonl"

    def fake_write(payload):
        request_path.write_text(json.dumps(payload), encoding="utf-8")
        return request_path

    def fake_run(*_args, **_kwargs):
        response_path.write_text(
            json.dumps({"ok": True, "data": {"secret_result": "hidden"}}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(OneNoteBridge, "_write_temp_json", staticmethod(fake_write))
    monkeypatch.setattr(OneNoteBridge, "_reserve_temp_path", staticmethod(lambda: response_path))
    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)

    result = OneNoteBridge(audit_path=audit_path).call(
        "get_hierarchy",
        object_id="secret-id",
    )

    assert result == {"secret_result": "hidden"}
    record = json.loads(audit_path.read_text(encoding="utf-8"))
    assert record["operation"] == "get_hierarchy"
    assert record["ok"] is True
    assert record["elapsed_seconds"] >= 0
    rendered = json.dumps(record)
    assert "secret-id" not in rendered
    assert "hidden" not in rendered


def test_bridge_timeout_override_is_internal_and_capped_by_global_timeout(monkeypatch, tmp_path) -> None:
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    observed = []
    requests = []

    def fake_write(payload):
        requests.append(payload)
        request_path.write_text(json.dumps(payload), encoding="utf-8")
        return request_path

    def fake_run(*_args, **kwargs):
        observed.append(kwargs["timeout"])
        response_path.write_text(json.dumps({"ok": True, "data": {}}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(OneNoteBridge, "_write_temp_json", staticmethod(fake_write))
    monkeypatch.setattr(OneNoteBridge, "_reserve_temp_path", staticmethod(lambda: response_path))
    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)

    bridge = OneNoteBridge(timeout_seconds=10)
    bridge.call("find_pages", _timeout_seconds=2.5, query="probe")
    bridge.call("find_pages", _timeout_seconds=30, query="probe")

    assert observed == [2.5, 10.0]
    assert all("_timeout_seconds" not in request["params"] for request in requests)


def test_bridge_audit_keeps_typed_hresult_without_payload_or_message(monkeypatch, tmp_path) -> None:
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    audit_path = tmp_path / "bridge-calls.jsonl"

    def fake_write(payload):
        request_path.write_text(json.dumps(payload), encoding="utf-8")
        return request_path

    def fake_run(*_args, **_kwargs):
        response_path.write_text(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "message": "secret page text must not enter audit",
                        "hresult": -2147213264,
                        "category": "OperationStopped",
                    },
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(OneNoteBridge, "_write_temp_json", staticmethod(fake_write))
    monkeypatch.setattr(OneNoteBridge, "_reserve_temp_path", staticmethod(lambda: response_path))
    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)

    with pytest.raises(OneNoteModalUIBlockedError):
        OneNoteBridge(audit_path=audit_path).call(
            "update_page_content", xml="secret raw XML"
        )

    record = json.loads(audit_path.read_text(encoding="utf-8"))
    assert record["error_code"] == "onenote_modal_ui_blocked"
    assert record["hresult"] == "0x80042030"
    assert record["hresult_signed"] == -2147213264
    rendered = json.dumps(record)
    assert "secret" not in rendered
    assert "raw XML" not in rendered
