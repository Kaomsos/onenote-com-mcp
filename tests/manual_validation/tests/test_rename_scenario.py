"""Rename scenario fixed dual-case and restoration behavior tests."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.manual_validation import test_utils
from tests.manual_validation.runtime import InvariantFailure, RuntimeOptions
from tests.manual_validation.scenarios import rename as rename_scenario
from tests.manual_validation.scenarios.rename import RenameScenario


def _attempt() -> dict:
    return {
        "state": "applied",
        "mutation_attempts": 1,
        "mutation_replayed": False,
        "observed_outcome": "applied",
    }


def _targets() -> tuple[dict, dict, dict]:
    notebook = {
        "resource_type": "notebook",
        "id": "notebook-id",
        "name": "Notebook",
        "parent_id": None,
    }
    group = {
        "resource_type": "section_group",
        "id": "group-id",
        "name": "Rename-Group",
        "path": "Notebook/Rename-Group",
        "parent_id": "notebook-id",
        "modified": "g1",
    }
    section = {
        "resource_type": "section",
        "id": "section-id",
        "name": "Rename-Section",
        "path": "Notebook/Rename-Group/Rename-Section",
        "parent_id": "group-id",
        "modified": "s1",
    }
    return notebook, group, section


def _snapshot(group: dict, section: dict, *, page_hash: str = "same-hash") -> dict:
    return {
        "captured_at": "snapshot",
        "notebook_id": "notebook-id",
        "items": [group, section],
        "page_hashes": {"page": page_hash},
        "page_objects": {"page": []},
    }


def _manifest(notebook: dict, group: dict, section: dict) -> dict:
    return {
        "schema_version": 1,
        "notebook": notebook,
        "structure": {
            "section_group_target": group,
            "section_target": section,
        },
    }


def _args(*, keep_worksite: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        new_name=None,
        notebook_name=None,
        keep_worksite=keep_worksite,
    )


def test_rename_runs_both_fixed_cases_and_restores_in_reverse_order(
    monkeypatch, tmp_path
) -> None:
    notebook, group, section = _targets()
    section_changed = {
        **section,
        "name": "Rename-Section-Smoke-Renamed",
        "modified": "s2",
    }
    group_changed = {
        **group,
        "name": "Rename-Group-Smoke-Renamed",
        "modified": "g2",
    }
    snapshots = iter(
        [
            _snapshot(group, section),
            _snapshot(group, section_changed),
            _snapshot(group_changed, section_changed),
            _snapshot(group, section_changed),
            _snapshot(group, section),
        ]
    )

    class FakeClient:
        calls: list[tuple[str, dict]] = []
        allowed_tools = set(rename_scenario.RENAME_TOOLS) | {"health_check"}
        policy = rename_scenario.WRITE_POLICY
        timeout_seconds = 10

        async def call_tool(self, name: str, arguments: dict, **_: object) -> dict:
            self.calls.append((name, arguments))
            item = section_changed if name == "rename_section" else group_changed
            return {
                "ok": True,
                "complete": True,
                "item": item,
                "reconciliation": _attempt(),
            }

    async def fake_snapshot(_client, _notebook_id):
        return deepcopy(next(snapshots))

    monkeypatch.setattr(rename_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(rename_scenario, "render_report", lambda _run_dir: None)
    result = asyncio.run(
        RenameScenario().execute(
            _args(),
            RuntimeOptions(tmp_path, 10, False, False),
            _manifest(notebook, group, section),
            client=FakeClient(),
            fixture_result={},
        )
    )

    assert [name for name, _arguments in FakeClient.calls] == [
        "rename_section",
        "rename_section_group",
        "rename_section_group",
        "rename_section",
    ]
    assert [arguments["new_name"] for _name, arguments in FakeClient.calls] == [
        "Rename-Section-Smoke-Renamed",
        "Rename-Group-Smoke-Renamed",
        "Rename-Group",
        "Rename-Section",
    ]
    assert result["target_ids"] == ["section-id", "group-id"]
    assert [case["resource_type"] for case in result["cases"]] == [
        "section",
        "section_group",
    ]
    assert result["restored"] is True


def test_rename_attempts_restore_before_reporting_invariant_failure(
    monkeypatch, tmp_path
) -> None:
    notebook, group, section = _targets()
    section_changed = {
        **section,
        "name": "Rename-Section-Smoke-Renamed",
        "modified": "s2",
    }
    snapshots = iter(
        [
            _snapshot(group, section),
            _snapshot(group, section_changed, page_hash="changed-hash"),
            _snapshot(group, section),
        ]
    )

    class FakeClient:
        calls: list[tuple[str, dict]] = []
        allowed_tools = set(rename_scenario.RENAME_TOOLS) | {"health_check"}
        policy = rename_scenario.WRITE_POLICY
        timeout_seconds = 10

        async def call_tool(self, name: str, arguments: dict, **_: object) -> dict:
            self.calls.append((name, arguments))
            return {
                "ok": True,
                "complete": True,
                "item": section_changed,
                "reconciliation": _attempt(),
            }

    async def fake_snapshot(_client, _notebook_id):
        return deepcopy(next(snapshots))

    monkeypatch.setattr(rename_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(rename_scenario, "render_report", lambda _run_dir: None)
    with pytest.raises(InvariantFailure):
        asyncio.run(
            RenameScenario().execute(
                _args(),
                RuntimeOptions(tmp_path, 10, False, False),
                _manifest(notebook, group, section),
                client=FakeClient(),
                fixture_result={},
            )
        )

    assert [name for name, _arguments in FakeClient.calls] == [
        "rename_section",
        "rename_section",
    ]
    assert [arguments["new_name"] for _name, arguments in FakeClient.calls] == [
        "Rename-Section-Smoke-Renamed",
        "Rename-Section",
    ]


def test_rename_keep_worksite_preserves_both_fixed_targets(
    monkeypatch, tmp_path
) -> None:
    notebook, group, section = _targets()
    section_changed = {
        **section,
        "name": "Rename-Section-Smoke-Renamed",
        "modified": "s2",
    }
    group_changed = {
        **group,
        "name": "Rename-Group-Smoke-Renamed",
        "modified": "g2",
    }
    snapshots = iter(
        [
            _snapshot(group, section),
            _snapshot(group, section_changed),
            _snapshot(group_changed, section_changed),
        ]
    )

    class FakeClient:
        calls: list[tuple[str, dict]] = []
        allowed_tools = set(rename_scenario.RENAME_TOOLS) | {"health_check"}
        policy = rename_scenario.WRITE_POLICY
        timeout_seconds = 10

        async def call_tool(self, name: str, arguments: dict, **_: object) -> dict:
            self.calls.append((name, arguments))
            item = section_changed if name == "rename_section" else group_changed
            return {
                "ok": True,
                "complete": True,
                "item": item,
                "reconciliation": _attempt(),
            }

    async def fake_snapshot(_client, _notebook_id):
        return deepcopy(next(snapshots))

    monkeypatch.setattr(rename_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(rename_scenario, "render_report", lambda _run_dir: None)
    result = asyncio.run(
        RenameScenario().execute(
            _args(keep_worksite=True),
            RuntimeOptions(tmp_path, 10, False, False),
            _manifest(notebook, group, section),
            client=FakeClient(),
            fixture_result={},
        )
    )

    assert len(FakeClient.calls) == 2
    assert result["restored"] is False
    assert result["worksite_preserved"] is True
    worksite = test_utils.read_json(
        tmp_path / "scenarios" / "rename" / "worksite.json"
    )
    assert worksite["target_ids"] == ["section-id", "group-id"]
    assert worksite["manual_cleanup_required"] is True
    assert len(worksite["cleanup"]) == 2
