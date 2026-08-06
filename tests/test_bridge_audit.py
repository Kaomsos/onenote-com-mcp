"""Content-free audit evidence for run-scoped OneNote bridge calls."""

from __future__ import annotations

import json
from types import SimpleNamespace

from local_onenote_mcp import bridge as bridge_module
from local_onenote_mcp.bridge import OneNoteBridge


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
