"""Copy scenario planning, evidence, cleanup, and invariant tests."""

from __future__ import annotations

import asyncio

import pytest

from local_onenote_mcp.page import image_dimensions
from tests.manual_validation import runner
from tests.manual_validation.mcp_stdio_client import ClientFailure
from tests.manual_validation.runner import InvariantFailure
from tests.manual_validation.scenarios.copy import (
    call_with_result_evidence,
    cleanup_copy,
    copy_spec,
)
from tests.manual_validation.scenarios.create import ensure_copy_rich_fixture
from tests.manual_validation.scenarios._config import COPY_FIXTURE_MARKER
from tests.manual_validation.scenarios.copy_invariants import (
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

    assert runner.read_json(evidence) == partial

def test_notebook_copy_requires_exact_manifest_allowlisted_root(tmp_path) -> None:
    manifest = {
        "schema_version": 1,
        "notebook": {"id": "notebook-id", "name": "Notebook"},
        "structure": {},
        "disposable_targets": {
            "notebook_copy_root": str((tmp_path / "different-root").resolve()),
        },
    }

    with pytest.raises(runner.RunnerFailure, match="exact disposable Notebook Copy root"):
        copy_spec("copy-notebook", manifest, tmp_path / "run")

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
        {"content_capabilities": ["Image", "Outline", "RichText", "Table"]}
    )
    with pytest.raises(InvariantFailure, match="missing automated fixture capabilities"):
        assert_copy_fixture_capabilities(
            {"content_capabilities": ["Image", "Outline", "Table"]}
        )
