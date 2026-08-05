"""Rename scenario restoration behavior tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.manual_isolated.runner import InvariantFailure, RuntimeOptions
from tests.manual_isolated.scenarios import rename as rename_scenario
from tests.manual_isolated.scenarios.rename import run_rename

def test_rename_attempts_restore_before_reporting_invariant_failure(monkeypatch, tmp_path) -> None:
    target = {
        "resource_type": "section",
        "id": "section-id",
        "name": "Move-Source",
        "path": "Notebook/Group-A/Move-Source",
        "parent_id": "group-a",
    }
    notebook = {"resource_type": "notebook", "id": "notebook-id", "name": "Notebook"}
    manifest = {"schema_version": 1, "notebook": notebook, "structure": {"move_source": target}}
    before = {
        "captured_at": "before",
        "notebook_id": "notebook-id",
        "items": [target],
        "page_hashes": {"page": "before-hash"},
        "page_objects": {"page": []},
    }
    changed = {**target, "name": "Move-Source-Smoke-Renamed", "path": "renamed"}
    after = {**before, "captured_at": "after", "items": [changed], "page_hashes": {"page": "changed-hash"}}
    restored = {**before, "captured_at": "restored"}

    class FakeClient:
        calls: list[tuple[str, dict]] = []

        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def call_tool(self, name: str, arguments: dict, **_: object) -> dict:
            self.calls.append((name, arguments))
            item = changed if arguments["new_name"].endswith("Renamed") else target
            return {"ok": True, "complete": True, "item": item}

    snapshots = iter([before, after, restored])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    monkeypatch.setattr(rename_scenario, "MCPStdioClient", FakeClient)
    monkeypatch.setattr(rename_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(rename_scenario, "render_report", lambda _run_dir: None)
    args = SimpleNamespace(target="move_source", new_name=None, notebook_name=None)
    options = RuntimeOptions(tmp_path, 10, False, False)
    with pytest.raises(InvariantFailure):
        asyncio.run(run_rename(args, options, manifest))
    assert [call[1]["new_name"] for call in FakeClient.calls] == [
        "Move-Source-Smoke-Renamed",
        "Move-Source",
    ]
