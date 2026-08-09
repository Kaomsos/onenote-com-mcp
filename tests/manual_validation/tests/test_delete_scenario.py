"""Delete scenario manifest allowlist tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tests.manual_validation.runtime import RuntimeOptions
from tests.manual_validation.scenarios import delete as delete_scenario
from tests.manual_validation.scenarios.delete import DeleteScenario


def test_delete_executes_with_its_minimal_group_only_manifest(monkeypatch, tmp_path) -> None:
    notebook = {"resource_type": "notebook", "id": "notebook-id", "name": "Notebook"}
    sandbox = {
        "resource_type": "section_group",
        "id": "sandbox-id",
        "name": "Delete-Sandbox",
        "parent_id": "notebook-id",
    }
    target = {
        "resource_type": "section_group",
        "id": "group-id",
        "name": "Disposable-Group",
        "parent_id": "sandbox-id",
    }
    manifest = {
        "schema_version": 1,
        "notebook": notebook,
        "structure": {"delete_sandbox": sandbox, "disposable_group": target},
    }
    before = {
        "captured_at": "before",
        "notebook_id": "notebook-id",
        "items": [notebook, sandbox, target],
        "page_hashes": {},
        "page_objects": {},
    }
    after = {**before, "captured_at": "after", "items": [notebook, sandbox]}

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
            if name == "delete_section_group":
                return {"permanently": False}
            if name == "get_tree":
                return {
                    "tree": {
                        "item": notebook,
                        "children": [
                            {
                                "item": sandbox,
                                "children": [{"item": {**target, "is_in_recycle_bin": True}}],
                            }
                        ],
                    }
                }
            raise AssertionError(f"Unexpected tool call: {name}")

    snapshots = iter([before, after])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    monkeypatch.setattr(delete_scenario, "MCPStdioClient", FakeClient)
    monkeypatch.setattr(delete_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(delete_scenario, "render_report", lambda _run_dir: None)
    args = SimpleNamespace(notebook_name=None)
    scenario = DeleteScenario()
    scenario.prepare_arguments(args, manifest)

    result = asyncio.run(
        scenario.execute(
            args,
            RuntimeOptions(tmp_path, 10, False, False),
            manifest,
            client=None,
            fixture_result={},
        )
    )

    assert result["target_key"] == "disposable_group"
    assert FakeClient.calls[0] == (
        "delete_section_group",
        {
            "section_group_id": "group-id",
            "expected_name": "Disposable-Group",
            "expected_parent_id": "sandbox-id",
            "expected_modified": None,
            "permanently": False,
        },
    )
