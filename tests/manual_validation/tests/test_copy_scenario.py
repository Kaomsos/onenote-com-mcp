"""Copy scenario planning, evidence, cleanup, and invariant tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from local_onenote_mcp.page import image_dimensions
from tests.manual_validation import test_utils
from tests.manual_validation.mcp_stdio_client import ClientFailure
from tests.manual_validation.runtime import (
    InvariantFailure,
    RestoreFailure,
    RunnerFailure,
    RuntimeOptions,
)
from tests.manual_validation.scenarios.common import copy_runtime
from tests.manual_validation.scenarios.common.copy_runtime import (
    call_with_result_evidence,
    cleanup_copy,
    close_copied_notebook,
    copy_spec,
)
from tests.manual_validation.scenarios.common.fixture_builders import (
    ensure_copy_list_tag_fixture,
    ensure_copy_rich_fixture,
)
from tests.manual_validation.scenarios.common.config import COPY_FIXTURE_MARKER
from tests.manual_validation.scenarios.common.copy_invariants import (
    assert_copy_fixture_capabilities,
    assert_copy_mapping,
    assert_copy_page_restored,
    assert_pages_unchanged,
)


def test_protected_page_invariant_ignores_unrelated_page_but_keeps_strict_gates() -> None:
    protected = {
        "resource_type": "page",
        "id": "protected",
        "title": "Protected",
        "parent_id": "section",
        "section_id": "section",
        "parent_page_id": None,
        "page_level": 1,
        "order": 0,
    }
    unrelated = {**protected, "id": "unrelated", "title": "Description", "order": 1}
    before = {
        "items": [protected, unrelated],
        "page_hashes": {"protected": "stable", "unrelated": "old"},
        "page_objects": {"protected": [{"id": "object"}], "unrelated": []},
    }
    after = {
        **before,
        "page_hashes": {"protected": "stable", "unrelated": "normalized"},
    }

    assert_pages_unchanged(before, after, ["protected"])

    changed_content = {
        **after,
        "page_hashes": {**after["page_hashes"], "protected": "changed"},
    }
    with pytest.raises(InvariantFailure, match="changed stable content"):
        assert_pages_unchanged(before, changed_content, ["protected"])

    changed_objects = {
        **after,
        "page_objects": {**after["page_objects"], "protected": [{"id": "other"}]},
    }
    with pytest.raises(InvariantFailure, match="changed content-object identity"):
        assert_pages_unchanged(before, changed_objects, ["protected"])


def test_copy_page_restore_ignores_unrelated_text_normalization_but_keeps_bundle_gates() -> None:
    protected = {
        "resource_type": "page",
        "id": "protected",
        "title": "Protected",
        "parent_id": "section",
        "section_id": "section",
        "parent_page_id": None,
        "page_level": 1,
        "order": 0,
    }
    unrelated = {
        "resource_type": "page",
        "id": "description",
        "title": "Description",
        "parent_id": "description-section",
        "section_id": "description-section",
        "parent_page_id": None,
        "page_level": 1,
        "order": 0,
    }
    before = {
        "notebook_ids": {"source": "source-notebook", "destination": "destination-notebook"},
        "items": [protected, unrelated],
        "page_hashes": {"protected": "stable", "description": "old"},
        "page_objects": {"protected": [{"id": "object"}], "description": []},
        "page_capability_projections": {
            "protected": {"capabilities": ["Outline"]},
            "description": {"capabilities": ["Outline"]},
        },
    }
    restored = {
        **before,
        "page_hashes": {"protected": "stable", "description": "normalized"},
    }

    assert_copy_page_restored(before, restored, ["protected"])

    changed_topology = {
        **restored,
        "items": [
            {**protected, "order": 1},
            unrelated,
        ],
    }
    with pytest.raises(InvariantFailure, match="identity or topology"):
        assert_copy_page_restored(before, changed_topology, ["protected"])

    changed_objects = {
        **restored,
        "page_objects": {**restored["page_objects"], "description": [{"id": "new"}]},
    }
    with pytest.raises(InvariantFailure, match="page_objects"):
        assert_copy_page_restored(before, changed_objects, ["protected"])

def test_result_evidence_is_written_for_structured_partial_failure(tmp_path) -> None:
    partial = {
        "ok": False,
        "complete": False,
        "code": "partial_failure",
        "outcome": "copy_only",
        "created_ids": ["new-page"],
    }

    class FakeClient:
        async def call_tool(self, *_args, **_kwargs):
            raise ClientFailure("partial", envelope=partial)

    evidence = tmp_path / "copy-result.json"
    with pytest.raises(ClientFailure):
        asyncio.run(
            call_with_result_evidence(
                FakeClient(),
                "copy_page",
                {},
                evidence,
            )
        )

    assert test_utils.read_json(evidence) == partial


def test_copy_plan_fails_closed_when_read_only_snapshots_never_stabilize(
    tmp_path,
) -> None:
    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def call_tool(self, name, _arguments):
            assert name == "plan_copy"
            self.calls += 1
            return {
                "plan_digest": f"digest-{self.calls}",
                "source_snapshot_digest": f"source-{self.calls}",
                "source": {"id": "source", "modified": f"m{self.calls}"},
            }

    client = FakeClient()
    attempts_path = tmp_path / "plan-attempts.json"
    with pytest.raises(InvariantFailure, match="did not stabilize"):
        asyncio.run(
            copy_runtime.stable_copy_plan(
                client,
                {"source_id": "source"},
                attempts_path=attempts_path,
                plan_path=tmp_path / "plan.json",
            )
        )

    assert client.calls == 3
    evidence = test_utils.read_json(attempts_path)
    assert evidence["stabilized"] is False
    assert len(evidence["attempts"]) == 3


def test_plan_bound_before_snapshot_binds_source_and_destination_evidence() -> None:
    before = {
        "items": [
            {"id": "source", "resource_type": "page", "modified": "old-source"},
            {
                "id": "destination",
                "resource_type": "section",
                "parent_id": "notebook",
                "notebook_id": "destination-notebook",
                "name": "Destination",
                "modified": "old-destination",
            },
        ],
        "page_hashes": {"source": "stable"},
    }
    planned = {
        "source": {"id": "source"},
        "source_snapshot_digest": "source-digest",
        "include_descendants": False,
        "snapshots": {
            "source": {
                "resources": [
                    {"id": "source", "resource_type": "page", "modified": "plan-source"}
                ],
                "page_hashes": {"source": "raw-source"},
            },
            "destination": {
                "resource_type": "section",
                "parent": {
                    "id": "destination",
                    "resource_type": "section",
                    "parent_id": "notebook",
                    "notebook_id": "destination-notebook",
                    "name": "Destination",
                    "modified": "plan-destination",
                },
                "name": "01-Same-Section-Root-Only-Copy",
                "base_folder": "",
                "target_path": "",
                "existing_children": [],
            },
        },
    }

    bound = copy_runtime.plan_bound_before_snapshot(before, planned)

    by_id = {item["id"]: item for item in bound["items"]}
    assert by_id["source"]["modified"] == "plan-source"
    assert by_id["destination"]["modified"] == "plan-destination"
    assert bound["plan_binding"]["destination_id"] == "destination"
    assert bound["plan_binding"]["destination_parent_snapshot"]["notebook_id"] == (
        "destination-notebook"
    )
    assert bound["plan_binding"]["destination_snapshot"]["name"] == (
        "01-Same-Section-Root-Only-Copy"
    )


def test_plan_bound_before_snapshot_rejects_flat_destination_resource_shape() -> None:
    before = {
        "items": [
            {"id": "source", "resource_type": "page"},
            {"id": "destination", "resource_type": "section"},
        ],
        "page_hashes": {"source": "stable"},
    }
    planned = {
        "source": {"id": "source"},
        "snapshots": {
            "source": {
                "resources": [{"id": "source", "resource_type": "page"}],
                "page_hashes": {"source": "raw-source"},
            },
            "destination": {"id": "destination", "resource_type": "section"},
        },
    }

    with pytest.raises(InvariantFailure, match="typed parent snapshot"):
        copy_runtime.plan_bound_before_snapshot(before, planned)

def test_notebook_copy_requires_exact_manifest_allowlisted_root(tmp_path) -> None:
    manifest = {
        "schema_version": 1,
        "notebook": {"id": "notebook-id", "name": "Notebook"},
        "structure": {},
        "disposable_targets": {
            "notebook_copy_root": str((tmp_path / "different-root").resolve()),
        },
    }

    with pytest.raises(RunnerFailure, match="exact disposable Notebook Copy root"):
        copy_spec("copy-notebook", manifest, tmp_path / "run")


def test_notebook_copy_uses_a_short_run_unique_destination_name(tmp_path) -> None:
    run_dir = tmp_path / "run"
    source_name = "__copy-notebook-2026-08-11-11-05-49__"
    manifest = {
        "schema_version": 1,
        "notebook": {"id": "notebook-id", "name": source_name},
        "structure": {},
        "disposable_targets": {
            "notebook_copy_root": str((run_dir / "notebook-copies").resolve()),
        },
    }

    spec = copy_spec(
        "copy-notebook",
        manifest,
        run_dir,
        name_suffix="2026-08-11-11-05-49",
    )

    assert spec["destination_name"].startswith("Copy-Notebook-")
    assert source_name not in spec["destination_name"]
    assert len(spec["destination_name"]) < len(source_name)


@pytest.mark.parametrize(
    ("scenario_name", "source_key", "destination_keys"),
    [
        (
            "copy-section",
            "source_section",
            ("group_b", "cross_notebook_group"),
        ),
        (
            "copy-section-group",
            "group_a",
            ("source_notebook", "destination_notebook"),
        ),
    ],
)
def test_container_copy_specs_declare_same_and_cross_notebook_cases(
    tmp_path, scenario_name, source_key, destination_keys
) -> None:
    manifest = {
        "schema_version": 1,
        "notebook": {"id": "source-notebook", "name": "Source"},
        "notebooks": {
            "source": {"id": "source-notebook", "name": "Source"},
            "destination": {"id": "destination-notebook", "name": "Destination"},
        },
        "structure": {
            "source_section": {"id": "source-section"},
            "group_a": {"id": "source-group"},
            "group_b": {"id": "same-notebook-group"},
            "cross_notebook_group": {"id": "cross-notebook-group"},
        },
    }

    spec = copy_spec(
        scenario_name,
        manifest,
        tmp_path,
        name_suffix="2026-08-11-20-00-00",
    )

    assert spec["source"]["id"] == manifest["structure"][source_key]["id"]
    assert [case["destination_scope"] for case in spec["cases"]] == [
        "same-notebook",
        "cross-notebook",
    ]
    assert [case["destination_role"] for case in spec["cases"]] == [
        "source",
        "destination",
    ]
    if destination_keys[0] == "source_notebook":
        expected_ids = ["source-notebook", "destination-notebook"]
    else:
        expected_ids = ["same-notebook-group", "cross-notebook-group"]
    assert [case["destination"]["id"] for case in spec["cases"]] == expected_ids


def test_notebook_copy_close_refreshes_modified_confirmation(tmp_path) -> None:
    target = {
        "resource_type": "notebook",
        "id": "copied-notebook",
        "name": "Copy",
        "modified": "2026-08-11T13:12:01.000Z",
    }

    class FakeClient:
        def __init__(self):
            self.close_arguments = None

        async def call_tool(self, name, arguments):
            if name == "get_notebook":
                assert arguments == {"notebook_id": "copied-notebook"}
                return {
                    "item": {
                        **target,
                        "modified": "2026-08-11T13:12:02.000Z",
                    }
                }
            if name == "close_notebook":
                self.close_arguments = dict(arguments)
                return {"closed": True}
            raise AssertionError(name)

    client = FakeClient()
    result = asyncio.run(
        close_copied_notebook(
            client,
            target,
            tmp_path / "close-confirmation.json",
        )
    )

    assert result == {"closed": True}
    assert client.close_arguments == {
        "notebook_id": "copied-notebook",
        "expected_name": "Copy",
        "expected_modified": "2026-08-11T13:12:02.000Z",
    }
    assert test_utils.read_json(tmp_path / "close-confirmation.json")["modified"] == (
        "2026-08-11T13:12:02.000Z"
    )


def test_failed_notebook_copy_closes_exact_created_target(tmp_path) -> None:
    target_path = (tmp_path / "copies" / "Copy").resolve()
    target = {
        "resource_type": "notebook",
        "id": "copied-notebook",
        "name": "Copy",
        "modified": "2026-08-11T13:12:01.000Z",
    }

    class FakeClient:
        async def call_tool(self, name, arguments):
            if name == "get_notebook":
                assert arguments == {"notebook_id": "copied-notebook"}
                return {"item": target}
            if name == "close_notebook":
                assert arguments["notebook_id"] == "copied-notebook"
                return {"closed": True}
            raise AssertionError(name)

    evidence = asyncio.run(
        copy_runtime._finalize_failed_copied_notebook(
            FakeClient(),
            {
                "item": target,
                "created_ids": ["copied-notebook"],
                "destination_path": str(target_path),
            },
            {"destination": {"target_path": str(target_path)}},
            tmp_path,
            keep_open=False,
        )
    )

    assert evidence["status"] == "closed"
    assert evidence["isolation_passed"] is True
    assert test_utils.read_json(
        tmp_path / "copy-target-failure-finalization.json"
    )["closed"] is True


def test_failed_notebook_copy_without_exact_target_binding_blocks_isolation(tmp_path) -> None:
    with pytest.raises(RestoreFailure, match="without enough exact binding"):
        asyncio.run(
            copy_runtime._finalize_failed_copied_notebook(
                object(),
                {"created_ids": ["copied-notebook"]},
                {"destination": {"target_path": str(tmp_path / "Copy")}},
                tmp_path,
                keep_open=False,
            )
        )

    evidence = test_utils.read_json(
        tmp_path / "copy-target-failure-finalization.json"
    )
    assert evidence["status"] == "close_failed"
    assert evidence["isolation_passed"] is False


@pytest.mark.parametrize(
    ("scenario_name", "tool_name", "source_key", "source_type"),
    [
        ("copy-section", "copy_section", "source_section", "section"),
        (
            "copy-section-group",
            "copy_section_group",
            "group_a",
            "section_group",
        ),
    ],
)
def test_container_copy_executor_runs_same_then_cross_notebook_cases(
    monkeypatch,
    tmp_path,
    scenario_name,
    tool_name,
    source_key,
    source_type,
) -> None:
    source_notebook = {
        "resource_type": "notebook",
        "id": "source-notebook",
        "name": "Source",
    }
    destination_notebook = {
        "resource_type": "notebook",
        "id": "destination-notebook",
        "name": "Destination",
    }
    source = {
        "resource_type": source_type,
        "id": "source-container",
        "name": "Source-Container",
        "parent_id": "source-notebook",
        "modified": "source-modified",
    }
    same_destination = {
        "resource_type": "section_group",
        "id": "same-notebook-group",
        "name": "Same",
        "parent_id": "source-notebook",
    }
    cross_destination = {
        "resource_type": "section_group",
        "id": "cross-notebook-group",
        "name": "Cross",
        "parent_id": "destination-notebook",
    }
    structure = {
        "source_section": source,
        "group_a": source,
        "group_b": same_destination,
        "cross_notebook_group": cross_destination,
    }
    manifest = {
        "schema_version": 1,
        "notebook": source_notebook,
        "notebooks": {
            "source": source_notebook,
            "destination": destination_notebook,
        },
        "structure": structure,
    }
    created_targets: list[dict] = []

    async def capture_bundle(_client, _notebooks):
        return {
            "notebook_id": "source-notebook",
            "items": [
                source_notebook,
                destination_notebook,
                source,
                same_destination,
                cross_destination,
                *created_targets,
            ],
            "page_hashes": {},
            "page_objects": {},
        }

    plan_destinations = iter(
        [
            same_destination if scenario_name == "copy-section" else source_notebook,
            cross_destination if scenario_name == "copy-section" else destination_notebook,
        ]
    )

    async def stable_plan(_client, arguments, **_paths):
        destination = next(plan_destinations)
        return {
            "plan_digest": f"digest-{arguments['destination_parent_id']}",
            "source_snapshot_digest": "source-digest",
            "source": source,
            "snapshots": {
                "source": {"resources": [source], "page_hashes": {}},
                "destination": {"parent": destination},
            },
            "content_capabilities": ["Outline", "RichText"],
        }

    cleanup_order = []

    async def cleanup(_client, _snapshot, copied):
        cleanup_order.append(copied["item"]["id"])
        return [copied["item"]["id"]]

    monkeypatch.setattr(copy_runtime, "_capture_notebook_bundle", capture_bundle)
    monkeypatch.setattr(copy_runtime, "stable_copy_plan", stable_plan)
    monkeypatch.setattr(copy_runtime, "plan_bound_before_snapshot", lambda before, _plan: before)
    monkeypatch.setattr(copy_runtime, "assert_copy_fixture_capabilities", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(copy_runtime, "assert_copy_mapping", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(copy_runtime, "assert_pages_unchanged", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(copy_runtime, "assert_restored", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(copy_runtime, "cleanup_copy", cleanup)
    monkeypatch.setattr(copy_runtime, "render_report", lambda _run_dir: None)

    class FakeClient:
        policy = copy_runtime.COPY_POLICY
        allowed_tools = set(copy_runtime.COPY_TOOLS) | {"health_check"}
        timeout_seconds = 1_800

        def __init__(self):
            self.copy_arguments = []

        async def call_tool(self, name, arguments):
            assert name == tool_name
            self.copy_arguments.append(dict(arguments))
            number = len(self.copy_arguments)
            target_id = f"target-{number}"
            target = {
                "resource_type": source_type,
                "id": target_id,
                "name": arguments["destination_name"],
                "parent_id": arguments["destination_parent_id"],
            }
            created_targets.append(target)
            siblings = [
                item
                for item in [source, same_destination, cross_destination, *created_targets]
                if item.get("resource_type") == source_type
                and item.get("parent_id") == target["parent_id"]
            ]
            parent_type = (
                "notebook"
                if target["parent_id"] in {"source-notebook", "destination-notebook"}
                else "section_group"
            )
            return {
                "item": target,
                "destination_position": {
                    "status": "observed",
                    "resource_type": source_type,
                    "parent_id": target["parent_id"],
                    "parent_type": parent_type,
                    "sibling_scope": "same_type_direct_children",
                    "index": len(siblings) - 1,
                    "sibling_count": len(siblings),
                    "sequence_source": "hierarchy_child_order",
                },
                "copy_report": {
                    "verified": True,
                    "lossless": True,
                    "id_map": {"source-container": target_id},
                },
            }

    run_dir = tmp_path / "run"
    (run_dir / "scenarios" / scenario_name).mkdir(parents=True)
    client = FakeClient()
    result = asyncio.run(
        copy_runtime.execute_copy_container(
            SimpleNamespace(
                scenario=scenario_name,
                notebook_name="Source",
                keep_worksite=False,
            ),
            RuntimeOptions(run_dir, 1_800, False, False),
            manifest,
            client=client,
        )
    )

    expected_destination_ids = (
        ["same-notebook-group", "cross-notebook-group"]
        if scenario_name == "copy-section"
        else ["source-notebook", "destination-notebook"]
    )
    assert [value["destination_parent_id"] for value in client.copy_arguments] == (
        expected_destination_ids
    )
    assert [case["destination_scope"] for case in result["case_results"]] == [
        "same-notebook",
        "cross-notebook",
    ]
    assert cleanup_order == ["target-2", "target-1"]
    assert result["restored"] is True


def test_keep_worksite_copy_spec_removes_cleanup_permissions(tmp_path) -> None:
    manifest = {
        "schema_version": 1,
        "notebook": {"id": "notebook-id", "name": "Notebook"},
        "notebooks": {
            "source": {"id": "notebook-id", "name": "Notebook"},
            "destination": {"id": "destination-notebook-id", "name": "Destination"},
        },
        "structure": {
            "parent_page": {"id": "page-id"},
            "semantic_page": {"id": "source-child-id"},
            "source_section": {"id": "source-section-id"},
            "disposable_section": {"id": "section-id"},
            "cross_section_anchor": {"id": "cross-section-anchor-id"},
            "cross_notebook_section": {"id": "cross-notebook-section-id"},
            "cross_notebook_anchor": {"id": "cross-notebook-anchor-id"},
        },
    }

    spec = copy_spec("copy-page", manifest, tmp_path, keep_worksite=True)

    assert [case["include_descendants"] for case in spec["cases"]] == [
        None,
        True,
        None,
        True,
        None,
        True,
    ]
    assert [case["expected_page_count"] for case in spec["cases"]] == [1, 2, 1, 2, 1, 2]
    assert [case["destination_scope"] for case in spec["cases"]] == [
        "same-section",
        "same-section",
        "cross-section",
        "cross-section",
        "cross-notebook",
        "cross-notebook",
    ]
    assert [case["collision_anchor"]["id"] for case in spec["cases"]] == [
        "source-child-id",
        "source-child-id",
        "cross-section-anchor-id",
        "cross-section-anchor-id",
        "cross-notebook-anchor-id",
        "cross-notebook-anchor-id",
    ]
    assert spec["protected_page_ids"] == [
        "page-id",
        "source-child-id",
        "cross-section-anchor-id",
        "cross-notebook-anchor-id",
    ]
    assert spec["policy"].deletes_enabled is False
    assert not {"delete_page", "delete_section", "delete_section_group"} & spec["tools"]
    assert {"get_tree", "copy_page", "plan_copy"} <= spec["tools"]
    assert not {"copy_section", "copy_section_group", "copy_notebook"} & spec["tools"]


def _legacy_copy_page_two_case_fixture(
    monkeypatch,
    tmp_path,
    keep_worksite,
    expected_restored,
    expected_cleanup_calls,
) -> None:
    notebook = {"resource_type": "notebook", "id": "notebook", "name": "Notebook"}
    source_section = {
        "resource_type": "section",
        "id": "source-section",
        "name": "Source",
        "parent_id": "notebook",
        "notebook_id": "notebook",
    }
    destination = {
        "resource_type": "section",
        "id": "destination-section",
        "name": "Destination",
        "parent_id": "notebook",
        "notebook_id": "notebook",
    }
    source = {
        "resource_type": "page",
        "id": "source-page",
        "title": "01-Source-Parent",
        "parent_id": "source-section",
        "section_id": "source-section",
        "notebook_id": "notebook",
        "page_level": 1,
        "parent_page_id": None,
        "order": 0,
        "modified": "pre-plan-modified",
    }
    source_child = {
        **source,
        "id": "source-child",
        "title": "02-Source-Child",
        "parent_page_id": "source-page",
        "page_level": 2,
        "order": 1,
    }
    root_target = {
        **source,
        "id": "root-target",
        "title": "01-Root-Only-Copy",
        "parent_id": "destination-section",
        "section_id": "destination-section",
    }
    subtree_target = {
        **root_target,
        "id": "subtree-target",
        "title": "02-Full-Subtree-Copy",
        "order": 1,
    }
    subtree_child = {
        **source_child,
        "id": "subtree-child",
        "parent_id": "destination-section",
        "section_id": "destination-section",
        "parent_page_id": "subtree-target",
        "order": 2,
    }
    before = {
        "notebook_id": "notebook",
        "items": [notebook, source_section, source, source_child, destination],
        "page_hashes": {
            "source-page": "same-source-hash",
            "source-child": "same-child-hash",
        },
        "page_objects": {"source-page": [], "source-child": []},
    }
    after_root = {
        "notebook_id": "notebook",
        "items": [
            notebook,
            source_section,
            source,
            source_child,
            destination,
            root_target,
        ],
        "page_hashes": {
            "source-page": "same-source-hash",
            "source-child": "same-child-hash",
            "root-target": "root-target-hash",
        },
        "page_objects": {
            "source-page": [],
            "source-child": [],
            "root-target": [],
        },
    }
    after_both = {
        "notebook_id": "notebook",
        "items": [
            *after_root["items"],
            subtree_target,
            subtree_child,
        ],
        "page_hashes": {
            **after_root["page_hashes"],
            "subtree-target": "subtree-target-hash",
            "subtree-child": "subtree-child-hash",
        },
        "page_objects": {
            **after_root["page_objects"],
            "subtree-target": [],
            "subtree-child": [],
        },
    }
    snapshots = iter(
        [before, after_root, after_both]
        if keep_worksite
        else [before, after_root, after_both, before]
    )

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    cleanup_calls: list[str] = []

    async def fake_cleanup(_client, _snapshot, _copied):
        target_ids = list(_copied["copy_report"]["id_map"].values())
        cleanup_calls.append(str(_copied["item"]["id"]))
        return list(reversed(target_ids))

    monkeypatch.setattr(copy_runtime, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(copy_runtime, "cleanup_copy", fake_cleanup)
    monkeypatch.setattr(copy_runtime, "render_report", lambda _run_dir: None)
    monkeypatch.setattr(
        copy_runtime,
        "copy_spec",
        lambda *_args, **_kwargs: {
            "source": source,
            "protected_page_ids": [
                "source-page",
                "source-child",
                "cross-section-anchor",
                "cross-notebook-anchor",
            ],
            "destination": destination,
            "tool": "copy_page",
            "cases": [
                {
                    "name": "root-only-default",
                    "destination_name": "01-Root-Only-Copy",
                    "include_descendants": None,
                    "expected_page_count": 1,
                },
                {
                    "name": "full-subtree",
                    "destination_name": "02-Full-Subtree-Copy",
                    "include_descendants": True,
                    "expected_page_count": 2,
                },
            ],
            "policy": copy_runtime.COPY_POLICY,
            "tools": copy_runtime.COPY_TOOLS,
        },
    )

    root_copied = {
        "item": root_target,
        "created_ids": ["root-target"],
        "copy_report": {
            "verified": True,
            "lossless": False,
            "id_map": {"source-page": "root-target"},
        },
    }
    subtree_copied = {
        "item": subtree_target,
        "created_ids": ["subtree-target", "subtree-child"],
        "copy_report": {
            "verified": True,
            "lossless": True,
            "id_map": {
                "source-page": "subtree-target",
                "source-child": "subtree-child",
            },
        },
    }

    class FakeClient:
        policy = copy_runtime.COPY_POLICY
        allowed_tools = set(copy_runtime.COPY_TOOLS) | {"health_check"}
        timeout_seconds = 1_800

        def __init__(self):
            self.copy_arguments = []
            self.plan_arguments = []
            self.plan_calls = 0

        async def call_tool(self, name, arguments):
            if name == "plan_copy":
                self.plan_calls += 1
                self.plan_arguments.append(dict(arguments))
                include_descendants = arguments.get("include_descendants", False)
                case_name = "subtree" if include_descendants else "root"
                modified = f"plan-bound-{case_name}-modified"
                planned_source = {**source, "modified": modified}
                resources = [planned_source]
                raw_hashes = {"source-page": f"raw-{case_name}-hash"}
                if include_descendants:
                    resources.append(source_child)
                    raw_hashes["source-child"] = "raw-child-hash"
                return {
                    "plan_digest": f"{case_name}-digest",
                    "source_snapshot_digest": f"{case_name}-source-digest",
                    "source": planned_source,
                    "snapshots": {
                        "source": {
                            "resources": resources,
                            "page_hashes": raw_hashes,
                        }
                    },
                    "content_capabilities": [
                        "Image",
                        "List",
                        "Outline",
                        "RichText",
                        "Table",
                        "Tag",
                    ],
                    "include_descendants": include_descendants,
                }
            if name == "copy_page":
                self.copy_arguments.append(dict(arguments))
                return subtree_copied if arguments.get("include_descendants") else root_copied
            raise AssertionError(name)

    run_dir = tmp_path / "run"
    (run_dir / "scenarios" / "copy-page").mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "notebook": notebook,
        "structure": {
            "parent_page": source,
            "disposable_section": destination,
        },
    }
    args = SimpleNamespace(
        scenario="copy-page",
        notebook_name="Notebook",
        keep_worksite=keep_worksite,
    )

    client = FakeClient()
    result = asyncio.run(
        copy_runtime.execute_copy(
            args,
            RuntimeOptions(run_dir, 1_800, False, False),
            manifest,
            client=client,
        )
    )

    assert result["status"] == "passed"
    assert result["restored"] is expected_restored
    assert result["worksite_preserved"] is keep_worksite
    assert len(cleanup_calls) == expected_cleanup_calls
    assert client.plan_calls == 4
    assert "include_descendants" not in client.plan_arguments[0]
    assert client.plan_arguments[2]["include_descendants"] is True
    assert "include_descendants" not in client.copy_arguments[0]
    assert client.copy_arguments[0]["expected_modified"] == "plan-bound-root-modified"
    assert client.copy_arguments[1]["include_descendants"] is True
    assert client.copy_arguments[1]["expected_modified"] == "plan-bound-subtree-modified"
    root_before = test_utils.read_json(
        run_dir / "scenarios" / "copy-page" / "before-root-only-default.json"
    )
    assert root_before["plan_binding"] == {
        "raw_page_hashes": {"source-page": "raw-root-hash"},
        "source_id": "source-page",
        "source_snapshot_digest": "root-source-digest",
        "include_descendants": False,
    }
    subtree_before = test_utils.read_json(
        run_dir / "scenarios" / "copy-page" / "before-full-subtree.json"
    )
    assert subtree_before["plan_binding"]["include_descendants"] is True
    assert "root-target" in subtree_before["page_hashes"]
    for case_name, digest in (
        ("root-only-default", "root-digest"),
        ("full-subtree", "subtree-digest"),
    ):
        plan_attempts = test_utils.read_json(
            run_dir / "scenarios" / "copy-page" / f"plan-attempts-{case_name}.json"
        )
        assert plan_attempts["stabilized"] is True
        assert [attempt["plan_digest"] for attempt in plan_attempts["attempts"]] == [
            digest,
            digest,
        ]
    assert [case["mapped_page_count"] for case in result["case_results"]] == [1, 2]
    if keep_worksite:
        worksite = test_utils.read_json(run_dir / "scenarios" / "copy-page" / "worksite.json")
        assert worksite["target_ids"] == [
            "root-target",
            "subtree-target",
            "subtree-child",
        ]
        assert worksite["manual_cleanup_required"] is True
    else:
        assert result["cleanup_deleted_ids"] == [
            "subtree-child",
            "subtree-target",
            "root-target",
        ]


@pytest.mark.parametrize("keep_worksite", (False, True))
def test_copy_page_executes_three_destination_scopes_by_two_subtree_modes(
    monkeypatch,
    tmp_path,
    keep_worksite,
) -> None:
    source_notebook = {"id": "source-notebook", "name": "Source Notebook"}
    destination_notebook = {"id": "destination-notebook", "name": "Destination Notebook"}
    source_section = {"id": "source-section", "resource_type": "section"}
    cross_section = {"id": "cross-section", "resource_type": "section"}
    cross_notebook_section = {"id": "cross-notebook-section", "resource_type": "section"}
    source = {
        "id": "source-page",
        "resource_type": "page",
        "title": "01-Source-Parent",
        "section_id": "source-section",
        "parent_id": "source-section",
        "parent_page_id": None,
        "page_level": 1,
        "order": 0,
        "modified": "stable",
    }
    child = {
        **source,
        "id": "source-child",
        "title": "02-Source-Child",
        "parent_page_id": "source-page",
        "page_level": 2,
        "order": 1,
    }
    cross_section_anchor = {
        **child,
        "id": "cross-section-anchor",
        "section_id": "cross-section",
        "parent_id": "cross-section",
        "parent_page_id": None,
        "page_level": 1,
        "order": 0,
    }
    cross_notebook_anchor = {
        **cross_section_anchor,
        "id": "cross-notebook-anchor",
        "section_id": "cross-notebook-section",
        "parent_id": "cross-notebook-section",
    }
    cases = []
    expected_destinations = []
    for scope, role, destination in (
        ("same-section", "source", source_section),
        ("cross-section", "source", cross_section),
        ("cross-notebook", "destination", cross_notebook_section),
    ):
        for subtree in (False, True):
            case_index = len(cases) + 1
            cases.append(
                {
                    "name": f"case-{case_index}",
                    "destination": destination,
                    "destination_role": role,
                    "destination_scope": scope,
                    "collision_anchor": {
                        "same-section": child,
                        "cross-section": cross_section_anchor,
                        "cross-notebook": cross_notebook_anchor,
                    }[scope],
                    "destination_name": f"{case_index:02d}-Copy",
                    "include_descendants": True if subtree else None,
                    "expected_page_count": 2 if subtree else 1,
                }
            )
            expected_destinations.append(destination["id"])

    monkeypatch.setattr(
        copy_runtime,
        "copy_spec",
        lambda *_args, **_kwargs: {
            "source": source,
            "protected_page_ids": [
                "source-page",
                "source-child",
                "cross-section-anchor",
                "cross-notebook-anchor",
            ],
            "notebooks": {
                "source": source_notebook,
                "destination": destination_notebook,
            },
            "tool": "copy_page",
            "cases": cases,
            "policy": copy_runtime.COPY_POLICY,
            "tools": copy_runtime.COPY_TOOLS,
        },
    )

    created_pages: list[dict] = []

    async def fake_snapshot(_client, notebook_id):
        if notebook_id == "source-notebook":
            items = [
                source_notebook,
                source_section,
                cross_section,
                source,
                child,
                cross_section_anchor,
            ]
            hashes = {
                "source-page": "parent-hash",
                "source-child": "child-hash",
                "cross-section-anchor": "cross-section-anchor-hash",
            }
            items.extend(
                item
                for item in created_pages
                if item["section_id"] in {"source-section", "cross-section"}
            )
        else:
            items = [destination_notebook, cross_notebook_section, cross_notebook_anchor]
            hashes = {"cross-notebook-anchor": "cross-notebook-anchor-hash"}
            items.extend(
                item
                for item in created_pages
                if item["section_id"] == "cross-notebook-section"
            )
        return {
            "notebook_id": notebook_id,
            "items": items,
            "page_hashes": hashes,
            "page_objects": {page_id: [{"id": f"object-{page_id}"}] for page_id in hashes},
        }

    async def fake_plan(_client, arguments, **_kwargs):
        include_descendants = arguments.get("include_descendants", False)
        resources = [source, child] if include_descendants else [source]
        destination_by_id = {
            item["id"]: item
            for item in (source_section, cross_section, cross_notebook_section)
        }
        destination_parent = destination_by_id[arguments["destination_parent_id"]]
        return {
            "plan_digest": f"digest-{arguments['destination_parent_id']}-{include_descendants}",
            "source_snapshot_digest": "source-digest",
            "source": source,
            "snapshots": {
                "source": {
                    "resources": resources,
                    "page_hashes": {item["id"]: "raw" for item in resources},
                },
                "destination": {
                    "resource_type": destination_parent["resource_type"],
                    "parent": destination_parent,
                    "name": arguments["destination_name"],
                    "base_folder": "",
                    "target_path": "",
                    "existing_children": [],
                },
            },
            "content_capabilities": [
                "DisplayEquation",
                "Image",
                "List",
                "Outline",
                "RichText",
                "Table",
                "Tag",
            ],
            "include_descendants": include_descendants,
        }

    copy_arguments: list[dict] = []

    class FakeClient:
        policy = copy_runtime.COPY_POLICY
        allowed_tools = set(copy_runtime.COPY_TOOLS) | {"health_check"}
        timeout_seconds = 1_800

        async def call_tool(self, name, arguments):
            assert name == "copy_page"
            copy_arguments.append(dict(arguments))
            index = len(copy_arguments)
            id_map = {"source-page": f"target-{index}"}
            destination_section_id = arguments["destination_section_id"]
            target = {
                "id": f"target-{index}",
                "resource_type": "page",
                "title": arguments["destination_title"],
                "section_id": destination_section_id,
                "parent_id": destination_section_id,
                "parent_page_id": None,
                "page_level": 1,
                "order": 100 + index * 2,
            }
            created_pages.append(target)
            if arguments.get("include_descendants") is True:
                id_map["source-child"] = f"target-child-{index}"
                created_pages.append(
                    {
                        **target,
                        "id": f"target-child-{index}",
                        "parent_page_id": target["id"],
                        "page_level": 2,
                        "order": target["order"] + 1,
                    }
                )
            base_pages = [source, child, cross_section_anchor, cross_notebook_anchor]
            siblings = sorted(
                [
                    item
                    for item in [*base_pages, *created_pages]
                    if item.get("section_id") == destination_section_id
                ],
                key=lambda item: int(item.get("order", 0)),
            )
            return {
                "item": target,
                "destination_position": {
                    "status": "observed",
                    "resource_type": "page",
                    "parent_id": destination_section_id,
                    "parent_type": "section",
                    "sibling_scope": "section_page_sequence",
                    "index": [item["id"] for item in siblings].index(target["id"]),
                    "sibling_count": len(siblings),
                    "sequence_source": "page_order",
                },
                "copy_report": {"verified": True, "id_map": id_map},
            }

    async def fake_cleanup(_client, _snapshot, copied):
        deleted = list(copied["copy_report"]["id_map"].values())
        created_pages[:] = [item for item in created_pages if item["id"] not in deleted]
        return deleted

    monkeypatch.setattr(copy_runtime, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(copy_runtime, "stable_copy_plan", fake_plan)
    monkeypatch.setattr(copy_runtime, "assert_copy_fixture_capabilities", lambda *_a, **_k: None)
    monkeypatch.setattr(copy_runtime, "assert_copy_mapping", lambda *_a, **_k: None)
    monkeypatch.setattr(copy_runtime, "cleanup_copy", fake_cleanup)
    monkeypatch.setattr(copy_runtime, "assert_restored", lambda *_a, **_k: None)
    monkeypatch.setattr(copy_runtime, "render_report", lambda _path: None)

    run_dir = tmp_path / "run"
    (run_dir / "scenarios" / "copy-page").mkdir(parents=True)
    result = asyncio.run(
        copy_runtime.execute_copy_page(
            SimpleNamespace(
                scenario="copy-page",
                notebook_name="Source Notebook",
                keep_worksite=keep_worksite,
            ),
            RuntimeOptions(run_dir, 1_800, False, False),
            {
                "notebook": source_notebook,
                "notebooks": {
                    "source": source_notebook,
                    "destination": destination_notebook,
                },
                "structure": {"parent_page": source},
            },
            client=FakeClient(),
        )
    )

    assert [arguments["destination_section_id"] for arguments in copy_arguments] == expected_destinations
    assert [arguments.get("include_descendants") for arguments in copy_arguments] == [
        None,
        True,
        None,
        True,
        None,
        True,
    ]
    assert [item["destination_scope"] for item in result["case_results"]] == [
        "same-section",
        "same-section",
        "cross-section",
        "cross-section",
        "cross-notebook",
        "cross-notebook",
    ]
    assert result["restored"] is (not keep_worksite)

def test_copy_cleanup_uses_exact_ids_leaf_to_root_with_fresh_reads() -> None:
    notebook = {"resource_type": "notebook", "id": "n", "name": "Notebook"}
    targets = [
        {
            "resource_type": "section_group",
            "id": "g",
            "name": "Root",
            "parent_id": "n",
            "notebook_id": "n",
        },
        {
            "resource_type": "section_group",
            "id": "ig",
            "name": "Inner",
            "parent_id": "g",
            "notebook_id": "n",
        },
        {
            "resource_type": "section",
            "id": "s1",
            "name": "First",
            "parent_id": "g",
            "notebook_id": "n",
        },
        {
            "resource_type": "section",
            "id": "s2",
            "name": "Second",
            "parent_id": "ig",
            "notebook_id": "n",
        },
        {
            "resource_type": "page",
            "id": "p1",
            "title": "Parent",
            "parent_id": "s2",
            "section_id": "s2",
            "notebook_id": "n",
            "order": 0,
        },
        {
            "resource_type": "page",
            "id": "p2",
            "title": "Child",
            "parent_id": "s2",
            "section_id": "s2",
            "notebook_id": "n",
            "order": 1,
        },
    ]

    class FakeClient:
        def __init__(self):
            self.active = {item["id"]: dict(item) for item in targets}
            self.deleted = []
            self.reads = 0

        async def call_tool(self, name, arguments):
            if name == "get_tree":
                self.reads += 1
                return {
                    "tree": {
                        "item": notebook,
                        "children": [
                            {"item": item, "children": []} for item in self.active.values()
                        ],
                    }
                }
            target_id = next(
                arguments[key]
                for key in ("page_id", "section_id", "section_group_id")
                if key in arguments
            )
            assert arguments["permanently"] is False
            self.deleted.append(target_id)
            self.active.pop(target_id)
            return {"permanently": False}

    client = FakeClient()
    snapshot = {"items": targets}
    copied = {
        "item": targets[0],
        "copy_report": {"id_map": {f"source-{item['id']}": item["id"] for item in targets}},
    }

    deleted = asyncio.run(cleanup_copy(client, snapshot, copied))

    assert deleted == ["p2", "p1", "s2", "s1", "ig", "g"]
    assert client.deleted == deleted
    assert client.reads == len(deleted)

def test_copy_rich_fixture_is_idempotent_and_records_automated_types(tmp_path) -> None:
    state = {
        "xml": (
            '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
            'ID="page-id"><one:Title><one:OE><one:T>Parent</one:T></one:OE></one:Title></one:Page>'
        ),
        "objects": [],
    }
    page = {
        "resource_type": "page",
        "id": "page-id",
        "title": "Parent",
        "section_id": "section-id",
        "modified": "m1",
    }

    class FakeClient:
        def __init__(self):
            self.mutations = []

        async def call_tool(self, name, arguments):
            if name == "get_page_xml":
                return {"xml": state["xml"]}
            if name == "get_page_objects":
                return {"objects": state["objects"]}
            if name == "list_pages":
                return {"pages": [page]}
            if name == "append_to_page":
                self.mutations.append(name)
                state["xml"] = (
                    '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
                    'ID="page-id"><one:Title><one:OE><one:T>Parent</one:T></one:OE></one:Title>'
                    '<one:Outline><one:OEChildren><one:OE><one:T>'
                    f"{COPY_FIXTURE_MARKER}"
                    '</one:T></one:OE><one:Table/></one:OEChildren></one:Outline></one:Page>'
                )
                return {"appended": True}
            if name == "add_image_to_page":
                self.mutations.append(name)
                state["objects"] = [{"kind": "Image", "id": None, "media_type": "png"}]
                return {"image_path": arguments["image_path"]}
            raise AssertionError(name)

    client = FakeClient()
    _, first = asyncio.run(ensure_copy_rich_fixture(client, page, tmp_path))
    _, second = asyncio.run(ensure_copy_rich_fixture(client, page, tmp_path))

    assert client.mutations == ["append_to_page", "add_image_to_page"]
    assert first == second
    assert first["automated_content"] == ["rich_text", "table", "image"]
    assert first["manual_content"] == ["ink", "shape", "media"]
    assert first["observed_object_types"] == ["Image"]
    assert image_dimensions(tmp_path / "fixture-assets" / "copy-fixture-1x1.png") == (1, 1)


def test_list_tag_copy_fixture_is_programmatic_and_idempotent() -> None:
    state = {
        "xml": '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="page-id"/>',
    }
    page = {
        "id": "page-id",
        "title": "List-Tag-Page",
        "section_id": "section-id",
        "modified": "m1",
    }

    class FakeClient:
        def __init__(self):
            self.append_arguments = []

        async def call_tool(self, name, arguments):
            if name == "get_page_xml":
                return {"xml": state["xml"]}
            if name == "list_pages":
                return {"pages": [page]}
            if name == "append_to_page":
                self.append_arguments.append(arguments)
                state["xml"] = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="page-id">
                <one:TagDef index="0" type="0" symbol="3" name="To Do"/>
                <one:Outline><one:OEChildren>
                  <one:OE><one:Tag index="0" completed="true"/><one:List><one:Number/></one:List><one:T>&amp;#20026;</one:T></one:OE>
                  <one:OE><one:Tag index="0" completed="false"/><one:List><one:Number/></one:List><one:T>&amp;#31572;&amp;#22797;</one:T></one:OE>
                  <one:OE><one:Tag index="0" completed="true"/><one:List><one:Number/></one:List><one:T>3&amp;#21457;&amp;#36865;</one:T></one:OE>
                </one:OEChildren></one:Outline></one:Page>"""
                return {"appended": True}
            raise AssertionError(name)

    client = FakeClient()
    _, first = asyncio.run(ensure_copy_list_tag_fixture(client, page))
    _, second = asyncio.run(ensure_copy_list_tag_fixture(client, page))

    assert len(client.append_arguments) == 1
    assert client.append_arguments[0]["content_format"] == "html"
    assert client.append_arguments[0]["content"].count("data-tag=") == 3
    assert first == second
    assert first["verification_tier"] == "semantic_list_tag"
    assert first["observed_capabilities"] == ["List", "Tag"]
    assert first["observed_counts"] == {"List": 3, "Tag": 3, "TagDef": 1}


def test_list_tag_copy_fixture_fails_closed_when_readback_is_incomplete() -> None:
    page = {
        "id": "page-id",
        "title": "List-Tag-Page",
        "section_id": "section-id",
        "modified": "m1",
    }

    class FakeClient:
        async def call_tool(self, name, _arguments):
            if name == "get_page_xml":
                return {"xml": (
                    '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
                    'ID="page-id"><one:Outline><one:OEChildren><one:OE>'
                    '<one:List><one:Number numberSequence="0"/></one:List><one:T>Item</one:T>'
                    '</one:OE></one:OEChildren></one:Outline></one:Page>'
                )}
            if name == "list_pages":
                return {"pages": [page]}
            if name == "append_to_page":
                return {"appended": True}
            raise AssertionError(name)

    with pytest.raises(InvariantFailure, match="partial pre-existing"):
        asyncio.run(ensure_copy_list_tag_fixture(FakeClient(), page))

def test_runner_independently_validates_page_copy_mapping_and_topology() -> None:
    before = {
        "items": [
            {"resource_type": "section", "id": "source-section", "name": "Source", "parent_id": "n"},
            {
                "resource_type": "page",
                "id": "parent",
                "title": "Parent",
                "section_id": "source-section",
                "parent_id": "source-section",
                "parent_page_id": None,
                "page_level": 2,
                "order": 0,
            },
            {
                "resource_type": "page",
                "id": "child",
                "title": "Child",
                "section_id": "source-section",
                "parent_id": "source-section",
                "parent_page_id": "parent",
                "page_level": 3,
                "order": 1,
            },
            {
                "resource_type": "page",
                "id": "sibling",
                "title": "Sibling",
                "section_id": "source-section",
                "parent_id": "source-section",
                "parent_page_id": None,
                "page_level": 2,
                "order": 2,
            },
            {"resource_type": "section", "id": "destination", "name": "Destination", "parent_id": "n"},
        ]
    }
    after = {
        "items": [
            *before["items"],
            {
                "resource_type": "page",
                "id": "new-parent",
                "title": "Copied Parent",
                "section_id": "destination",
                "parent_id": "destination",
                "parent_page_id": None,
                "page_level": 1,
                "order": 4,
            },
            {
                "resource_type": "page",
                "id": "new-child",
                "title": "Child",
                "section_id": "destination",
                "parent_id": "destination",
                "parent_page_id": "new-parent",
                "page_level": 2,
                "order": 5,
            },
        ]
    }
    copied = {
        "copy_report": {"id_map": {"parent": "new-parent", "child": "new-child"}}
    }

    assert_copy_mapping(
        before,
        after,
        "parent",
        "destination",
        "Copied Parent",
        copied,
    )

    broken = {"items": [dict(item) for item in after["items"]]}
    next(item for item in broken["items"] if item["id"] == "new-child")["parent_page_id"] = None
    with pytest.raises(InvariantFailure, match="parent relation"):
        assert_copy_mapping(
            before,
            broken,
            "parent",
            "destination",
            "Copied Parent",
            copied,
        )


def test_runner_root_only_page_copy_requires_child_to_stay_at_source() -> None:
    parent = {
        "resource_type": "page",
        "id": "parent",
        "title": "Parent",
        "section_id": "source",
        "parent_id": "source",
        "parent_page_id": None,
        "page_level": 1,
        "order": 0,
    }
    child = {
        "resource_type": "page",
        "id": "child",
        "title": "Child",
        "section_id": "source",
        "parent_id": "source",
        "parent_page_id": "parent",
        "page_level": 2,
        "order": 1,
    }
    before = {
        "items": [
            {"resource_type": "section", "id": "source", "name": "Source"},
            parent,
            child,
            {"resource_type": "section", "id": "destination", "name": "Destination"},
        ]
    }
    target = {
        **parent,
        "id": "new-parent",
        "title": "Copied Parent",
        "section_id": "destination",
        "parent_id": "destination",
    }
    after = {"items": [*before["items"], target]}
    copied = {"copy_report": {"id_map": {"parent": "new-parent"}}}

    assert_copy_mapping(
        before,
        after,
        "parent",
        "destination",
        "Copied Parent",
        copied,
        include_descendants=False,
    )

    changed = {"items": [dict(item) for item in after["items"]]}
    next(item for item in changed["items"] if item["id"] == "child")["parent_page_id"] = None
    with pytest.raises(InvariantFailure, match="excluded source descendant"):
        assert_copy_mapping(
            before,
            changed,
            "parent",
            "destination",
            "Copied Parent",
            copied,
            include_descendants=False,
        )

def test_copy_fixture_capability_gate_runs_before_mutation() -> None:
    assert_copy_fixture_capabilities(
        {
            "content_capabilities": [
                "Image", "List", "Outline", "RichText", "Table", "Tag"
            ]
        }
    )
    with pytest.raises(InvariantFailure, match="missing required fixture capabilities"):
        assert_copy_fixture_capabilities(
            {"content_capabilities": ["Image", "Outline", "RichText", "Table"]}
        )
