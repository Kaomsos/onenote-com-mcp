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


def _page_snapshot(
    title: str,
    *,
    page_hash: str,
    body_hash: str = "body-stable",
    canonical_hash: str,
) -> dict:
    section = {
        "resource_type": "section",
        "id": "section-id",
        "name": "Rename-Section",
        "parent_id": "notebook-id",
    }
    page = {
        "resource_type": "page",
        "id": "page-id",
        "title": title,
        "path": f"Notebook/Rename-Section/{title}",
        "parent_id": "section-id",
        "section_id": "section-id",
        "page_level": 1,
        "order": 0,
        "parent_page_id": None,
        "modified": page_hash,
    }
    return {
        "captured_at": page_hash,
        "notebook_id": "notebook-id",
        "items": [section, page],
        "page_hashes": {"page-id": page_hash},
        "page_body_hashes": {"page-id": body_hash},
        "page_canonical_hashes": {"page-id": canonical_hash},
        "page_objects": {"page-id": [{"kind": "Outline", "id": "outline-id"}]},
    }


def test_page_rename_allows_title_hash_change_and_requires_canonical_restore(
    monkeypatch, tmp_path
) -> None:
    before = _page_snapshot(
        "Rename-Page", page_hash="raw-before", canonical_hash="canonical-before"
    )
    after = _page_snapshot(
        "Rename-Page-Smoke-Renamed",
        page_hash="raw-after",
        canonical_hash="canonical-after",
    )
    restored = _page_snapshot(
        "Rename-Page", page_hash="raw-restored", canonical_hash="canonical-before"
    )
    snapshots = iter([before, after, restored])
    notebook = {
        "resource_type": "notebook",
        "id": "notebook-id",
        "name": "Notebook",
        "parent_id": None,
    }
    manifest = {
        "schema_version": 1,
        "notebook": notebook,
        "structure": {"page_target": before["items"][1]},
    }

    class FakeClient:
        calls: list[tuple[str, dict]] = []
        allowed_tools = set(rename_scenario.RENAME_TOOLS) | {"health_check"}
        policy = rename_scenario.WRITE_POLICY
        timeout_seconds = 10

        async def call_tool(self, name: str, arguments: dict, **_: object) -> dict:
            self.calls.append((name, arguments))
            item = arguments["items"][0]
            title = item["new_title"]
            result_item = {
                **before["items"][1],
                "title": title,
                "path": f"Notebook/Rename-Section/{title}",
            }
            return {
                "operation": name,
                "mode": "batch",
                "items": [
                    {
                        "input_index": 0,
                        "object_id": "page-id",
                        "status": "applied",
                        "result": {
                            "item": result_item,
                            "reconciliation": _attempt(),
                        },
                    }
                ],
            }

    async def fake_snapshot(_client, _notebook_id):
        return deepcopy(next(snapshots))

    monkeypatch.setattr(rename_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(rename_scenario, "render_report", lambda _run_dir: None)
    result = asyncio.run(
        RenameScenario().execute(
            _args(),
            RuntimeOptions(tmp_path, 10, False, False),
            manifest,
            client=FakeClient(),
            fixture_result={},
        )
    )

    assert result["status"] == "passed"
    assert result["restored"] is True
    assert [call[1]["items"][0]["new_title"] for call in FakeClient.calls] == [
        "Rename-Page-Smoke-Renamed",
        "Rename-Page",
    ]


def test_page_rename_rejects_body_change_even_when_only_title_should_change() -> None:
    before = _page_snapshot(
        "Rename-Page", page_hash="raw-before", canonical_hash="canonical-before"
    )
    after = _page_snapshot(
        "Rename-Page-Smoke-Renamed",
        page_hash="raw-after",
        body_hash="body-changed",
        canonical_hash="canonical-after",
    )

    with pytest.raises(InvariantFailure, match="outside the title"):
        rename_scenario._validate_transition(
            before,
            after,
            target_id="page-id",
            new_name="Rename-Page-Smoke-Renamed",
        )
