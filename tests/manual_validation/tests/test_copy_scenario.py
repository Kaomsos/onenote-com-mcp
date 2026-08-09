"""Copy scenario planning, evidence, cleanup, and invariant tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from local_onenote_mcp.page import image_dimensions
from tests.manual_validation import test_utils
from tests.manual_validation.mcp_stdio_client import ClientFailure
from tests.manual_validation.runtime import InvariantFailure, RunnerFailure, RuntimeOptions
from tests.manual_validation.scenarios.common import copy_runtime
from tests.manual_validation.scenarios.common.copy_runtime import (
    call_with_result_evidence,
    cleanup_copy,
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
)

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
    source_name = "__LOCAL_MCP_TEST_ISOLATED__20260809T055359Z"
    manifest = {
        "schema_version": 1,
        "notebook": {"id": "notebook-id", "name": source_name},
        "structure": {},
        "disposable_targets": {
            "notebook_copy_root": str((run_dir / "notebook-copies").resolve()),
        },
    }

    spec = copy_spec("copy-notebook", manifest, run_dir)

    assert spec["destination_name"].startswith("Copy-Notebook-")
    assert source_name not in spec["destination_name"]
    assert len(spec["destination_name"]) < len(source_name)


def test_keep_worksite_copy_spec_removes_cleanup_permissions(tmp_path) -> None:
    manifest = {
        "schema_version": 1,
        "notebook": {"id": "notebook-id", "name": "Notebook"},
        "structure": {
            "parent_page": {"id": "page-id"},
            "disposable_section": {"id": "section-id"},
        },
    }

    spec = copy_spec("copy-page", manifest, tmp_path, keep_worksite=True)

    assert spec["policy"].deletes_enabled is False
    assert not {"delete_page", "delete_section", "delete_section_group"} & spec["tools"]
    assert {"get_tree", "copy_page", "plan_copy"} <= spec["tools"]


@pytest.mark.parametrize(
    ("keep_worksite", "expected_restored", "expected_cleanup_calls"),
    ((False, True, 1), (True, False, 0)),
)
def test_copy_page_keep_worksite_preserves_verified_target_without_cleanup(
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
        "title": "Rich-Page",
        "parent_id": "source-section",
        "section_id": "source-section",
        "notebook_id": "notebook",
        "page_level": 1,
        "parent_page_id": None,
        "order": 0,
        "modified": "pre-plan-modified",
    }
    target = {
        **source,
        "id": "target-page",
        "title": "Copy-Page",
        "parent_id": "destination-section",
        "section_id": "destination-section",
    }
    before = {
        "notebook_id": "notebook",
        "items": [notebook, source_section, source, destination],
        "page_hashes": {"source-page": "same-source-hash"},
        "page_objects": {"source-page": []},
    }
    after = {
        "notebook_id": "notebook",
        "items": [notebook, source_section, source, destination, target],
        "page_hashes": {
            "source-page": "same-source-hash",
            "target-page": "target-hash",
        },
        "page_objects": {"source-page": [], "target-page": []},
    }
    snapshots = iter([before, after] if keep_worksite else [before, after, before])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    cleanup_calls: list[str] = []

    async def fake_cleanup(_client, _snapshot, _copied):
        cleanup_calls.append("cleanup")
        return ["target-page"]

    monkeypatch.setattr(copy_runtime, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(copy_runtime, "cleanup_copy", fake_cleanup)
    monkeypatch.setattr(copy_runtime, "render_report", lambda _run_dir: None)
    monkeypatch.setattr(
        copy_runtime,
        "copy_spec",
        lambda *_args, **_kwargs: {
            "source": source,
            "destination": destination,
            "destination_name": "Copy-Page",
            "tool": "copy_page",
            "policy": copy_runtime.COPY_POLICY,
            "tools": copy_runtime.COPY_TOOLS,
        },
    )

    copied = {
        "item": target,
        "created_ids": ["target-page"],
        "copy_report": {
            "verified": True,
            "lossless": False,
            "id_map": {"source-page": "target-page"},
        },
    }

    class FakeClient:
        policy = copy_runtime.COPY_POLICY
        allowed_tools = set(copy_runtime.COPY_TOOLS) | {"health_check"}
        timeout_seconds = 1_800

        def __init__(self):
            self.copy_arguments = None
            self.plan_calls = 0

        async def call_tool(self, name, arguments):
            if name == "plan_copy":
                self.plan_calls += 1
                settled = self.plan_calls > 1
                modified = "plan-bound-modified" if settled else "settling-modified"
                planned_source = {**source, "modified": modified}
                return {
                    "plan_digest": "digest" if settled else "settling-digest",
                    "source_snapshot_digest": (
                        "source-digest" if settled else "settling-source-digest"
                    ),
                    "source": planned_source,
                    "snapshots": {
                        "source": {
                            "resources": [planned_source],
                            "page_hashes": {
                                "source-page": (
                                    "raw-plan-hash" if settled else "settling-raw-hash"
                                )
                            },
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
                }
            if name == "copy_page":
                self.copy_arguments = arguments
                return copied
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
    assert client.plan_calls == 3
    assert client.copy_arguments["expected_modified"] == "plan-bound-modified"
    bound_before = test_utils.read_json(
        run_dir / "scenarios" / "copy-page" / "before.json"
    )
    assert bound_before["plan_binding"] == {
        "raw_page_hashes": {"source-page": "raw-plan-hash"},
        "source_id": "source-page",
        "source_snapshot_digest": "source-digest",
    }
    assert bound_before["page_hashes"] == {"source-page": "same-source-hash"}
    plan_attempts = test_utils.read_json(
        run_dir / "scenarios" / "copy-page" / "plan-attempts.json"
    )
    assert plan_attempts["stabilized"] is True
    assert [attempt["plan_digest"] for attempt in plan_attempts["attempts"]] == [
        "settling-digest",
        "digest",
        "digest",
    ]
    if keep_worksite:
        worksite = test_utils.read_json(run_dir / "scenarios" / "copy-page" / "worksite.json")
        assert worksite["target_ids"] == ["target-page"]
        assert worksite["manual_cleanup_required"] is True
    else:
        assert result["cleanup_deleted_ids"] == ["target-page"]

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
    assert first["manual_content"] == ["file_attachment", "ink", "media"]
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
