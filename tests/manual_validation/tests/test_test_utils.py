"""Pure snapshot and hierarchy invariant utility tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re

import pytest

from tests.manual_validation import test_utils
from tests.manual_validation.runtime import PathBudgetFailure
from tests.manual_validation.test_utils import (
    assert_valid_page_tree,
    capture_snapshot,
    comparable_snapshot,
    is_descendant_of,
    page_body_content_hash,
    page_content_hash,
    page_reparent_content_hash,
    page_revision_marker_projection,
    page_semantic_content_identity,
    write_json,
    write_sensitive_page_xml,
)


def test_json_and_sensitive_xml_use_unique_atomic_temporary_files(
    tmp_path,
    monkeypatch,
) -> None:
    original = test_utils.atomic_replace_with_retry
    temporary_names: list[str] = []

    def recording_replace(source, destination, **kwargs) -> None:
        temporary_names.append(source.name)
        original(source, destination, **kwargs)

    monkeypatch.setattr(test_utils, "atomic_replace_with_retry", recording_replace)
    json_path = tmp_path / "evidence.json"
    xml_path = tmp_path / "page.xml"

    write_json(json_path, {"version": 1})
    write_json(json_path, {"version": 2})
    report = write_sensitive_page_xml(
        xml_path,
        "<one:Page><one:Data>YQ==</one:Data></one:Page>",
    )

    assert json_path.read_text(encoding="utf-8").find('"version": 2') >= 0
    assert "YQ==" not in xml_path.read_text(encoding="utf-8")
    assert report["binary_payload_count"] == 1
    assert len(temporary_names) == len(set(temporary_names)) == 3
    assert all(name.startswith(".") and name.endswith(".tmp") for name in temporary_names)
    assert all(re.fullmatch(r"\..+\.[0-9a-f]{16}\.tmp", name) for name in temporary_names)
    assert not list(tmp_path.glob("*.tmp"))


def test_sensitive_xml_budget_failure_precedes_parent_creation(tmp_path, monkeypatch) -> None:
    target = tmp_path / "not-created" / "page.xml"

    def reject(_paths, *, phase):
        raise PathBudgetFailure(
            phase=phase,
            target_kind="run_xml_evidence",
            path=Path(target),
            actual_utf16=241,
            limit_utf16=240,
            relative_path=None,
            remediation={"code": "use_shorter_unique_run_dir", "message": "shorten"},
        )

    monkeypatch.setattr(test_utils, "preflight_paths", reject)
    with pytest.raises(PathBudgetFailure):
        write_sensitive_page_xml(target, "<one:Page/>")

    assert not target.parent.exists()


def test_snapshot_comparison_ignores_capture_time_and_item_order() -> None:
    first = {
        "captured_at": "before",
        "notebook_id": "n",
        "items": [{"id": "b"}, {"id": "a"}],
        "page_hashes": {"p": "hash"},
        "page_objects": {"p": []},
    }
    second = {**first, "captured_at": "after", "items": list(reversed(first["items"]))}
    assert comparable_snapshot(first) == comparable_snapshot(second)


def test_page_body_hash_ignores_title_but_not_body_content() -> None:
    before = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote"><one:Title><one:OE><one:T>Before</one:T></one:OE></one:Title><one:Outline><one:OEChildren><one:OE><one:T>Body</one:T></one:OE></one:OEChildren></one:Outline></one:Page>"""
    renamed = before.replace(">Before<", ">After<")
    changed_body = renamed.replace(">Body<", ">Changed<")

    assert page_body_content_hash(before) == page_body_content_hash(renamed)
    assert page_body_content_hash(before) != page_body_content_hash(changed_body)


def test_semantic_content_identity_ignores_empty_layout_outline_but_keeps_meaning() -> None:
    before = (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">'
        '<one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>'
        '<one:Outline><one:Position x="1" y="2"/><one:OEChildren>'
        '<one:OE><one:T><![CDATA[<span style="font-weight:bold">Body</span>]]>'
        '</one:T></one:OE></one:OEChildren></one:Outline></one:Page>'
    )
    stabilized = before.replace(
        "</one:Page>",
        '<one:Outline><one:Position x="99" y="100"/><one:OEChildren/>'
        "</one:Outline></one:Page>",
    ).replace('x="1" y="2"', 'x="3" y="4"')
    changed = stabilized.replace(">Body<", ">Changed<")

    before_identity = page_semantic_content_identity(before)
    stabilized_identity = page_semantic_content_identity(stabilized)
    changed_identity = page_semantic_content_identity(changed)

    assert before_identity["complete"] is True
    assert before_identity == stabilized_identity
    assert before_identity["sha256"] != changed_identity["sha256"]


def test_semantic_persistence_identity_ignores_outline_merge_but_keeps_oe_order() -> None:
    separate = (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">'
        '<one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>'
        '<one:Outline><one:OEChildren><one:OE><one:T>First</one:T></one:OE>'
        '</one:OEChildren></one:Outline>'
        '<one:Outline><one:OEChildren><one:OE><one:T>Second</one:T></one:OE>'
        '</one:OEChildren></one:Outline></one:Page>'
    )
    merged = separate.replace(
        "</one:OEChildren></one:Outline><one:Outline><one:OEChildren>",
        "",
    )
    reordered = merged.replace(
        "<one:T>First</one:T>",
        "<one:T>Changed</one:T>",
    )

    separate_identity = page_semantic_content_identity(separate)
    merged_identity = page_semantic_content_identity(merged)
    reordered_identity = page_semantic_content_identity(reordered)

    assert separate_identity["sha256"] != merged_identity["sha256"]
    assert (
        separate_identity["persistence_sha256"]
        == merged_identity["persistence_sha256"]
    )
    assert (
        separate_identity["persistence_sha256"]
        != reordered_identity["persistence_sha256"]
    )


def test_materialization_identity_ignores_presentation_only_rich_text_span_rewrite() -> None:
    before = (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">'
        '<one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>'
        '<one:Outline><one:OEChildren><one:OE><one:T><![CDATA[Body]]>'
        '</one:T></one:OE></one:OEChildren></one:Outline></one:Page>'
    )
    materialized = before.replace(
        "<![CDATA[Body]]>",
        '<![CDATA[<span lang="zh-CN" style="font-size:11pt">Body</span>]]>',
    )

    before_identity = page_semantic_content_identity(before)
    materialized_identity = page_semantic_content_identity(materialized)

    assert (
        before_identity["persistence_sha256"]
        != materialized_identity["persistence_sha256"]
    )
    assert (
        before_identity["materialization_sha256"]
        == materialized_identity["materialization_sha256"]
    )
    changed_text_identity = page_semantic_content_identity(
        materialized.replace("Body</span>", "Changed</span>")
    )
    assert (
        before_identity["materialization_sha256"]
        != changed_text_identity["materialization_sha256"]
    )


def test_materialization_identity_ignores_table_presentation_rewrite_but_keeps_topology() -> None:
    before = (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">'
        '<one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>'
        '<one:Outline><one:OEChildren><one:OE>'
        '<one:Table bordersVisible="true"><one:Columns>'
        '<one:Column index="0" width="100"/></one:Columns>'
        '<one:Row><one:Cell shadingColor="#ffffff"><one:OEChildren>'
        '<one:OE><one:T>Cell</one:T></one:OE>'
        '</one:OEChildren></one:Cell></one:Row></one:Table>'
        '</one:OE></one:OEChildren></one:Outline></one:Page>'
    )
    materialized = (
        before.replace('bordersVisible="true"', 'bordersVisible="false"')
        .replace('width="100"', 'width="104.25"')
        .replace('shadingColor="#ffffff"', 'shadingColor="#fefefe"')
    )

    before_identity = page_semantic_content_identity(before)
    materialized_identity = page_semantic_content_identity(materialized)

    assert (
        before_identity["persistence_sha256"]
        != materialized_identity["persistence_sha256"]
    )
    assert (
        before_identity["materialization_sha256"]
        == materialized_identity["materialization_sha256"]
    )
    changed_text = page_semantic_content_identity(
        materialized.replace("<one:T>Cell</one:T>", "<one:T>Changed</one:T>")
    )
    assert (
        before_identity["materialization_sha256"]
        != changed_text["materialization_sha256"]
    )
    changed_topology = page_semantic_content_identity(
        materialized.replace(
            "</one:Columns>",
            '<one:Column index="1" width="50"/></one:Columns>',
        )
    )
    assert (
        before_identity["materialization_sha256"]
        != changed_topology["materialization_sha256"]
    )


def test_page_tree_and_delete_sandbox_ancestry_checks() -> None:
    snapshot = {
        "items": [
            {"id": "sandbox", "resource_type": "section_group", "parent_id": "notebook"},
            {"id": "section", "resource_type": "section", "parent_id": "sandbox"},
            {
                "id": "parent",
                "resource_type": "page",
                "section_id": "section",
                "parent_id": "section",
                "parent_page_id": None,
                "page_level": 1,
                "order": 0,
            },
            {
                "id": "child",
                "resource_type": "page",
                "section_id": "section",
                "parent_id": "parent",
                "parent_page_id": "parent",
                "page_level": 2,
                "order": 1,
            },
        ]
    }
    assert_valid_page_tree(snapshot, "section")
    assert test_utils.page_topology(snapshot, "section") == [
        ("parent", "section", 0, 1, None),
        ("child", "section", 1, 2, "parent"),
    ]
    gapped = {
        "items": [
            {
                "id": "root",
                "resource_type": "page",
                "section_id": "section",
                "parent_page_id": None,
                "page_level": 1,
                "order": 0,
            },
            {
                "id": "skipped",
                "resource_type": "page",
                "section_id": "section",
                "parent_page_id": "root",
                "page_level": 3,
                "order": 1,
            },
            {
                "id": "skipped-sibling",
                "resource_type": "page",
                "section_id": "section",
                "parent_page_id": "root",
                "page_level": 3,
                "order": 2,
            },
        ]
    }
    assert_valid_page_tree(gapped, "section")
    assert is_descendant_of(snapshot, "section", "sandbox") is True
    assert is_descendant_of(snapshot, "section", "unrelated") is False


def test_page_content_hash_ignores_root_hierarchy_metadata_but_detects_content_changes() -> None:
    before = (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
        'ID="page" name="Sibling" dateTime="before" lastModifiedTime="before" '
        'pageLevel="1" isCurrentlyViewed="false"><one:Outline><one:OEChildren>'
        '<one:OE><one:T>same body</one:T></one:OE></one:OEChildren></one:Outline></one:Page>'
    )
    reordered = (
        before.replace('lastModifiedTime="before"', 'lastModifiedTime="after"')
        .replace('pageLevel="1"', 'pageLevel="2"')
        .replace(
            "<one:OE>",
            '<one:OE author="OneNote" creationTime="after" selected="all">',
        )
    )
    changed_content = reordered.replace("same body", "changed body")
    changed_object_id = reordered.replace(
        "<one:Outline>", '<one:Outline objectID="new-object">'
    )

    assert page_content_hash(before) == page_content_hash(reordered)
    assert page_content_hash(before) != page_content_hash(changed_content)
    assert page_content_hash(before) != page_content_hash(changed_object_id)


def test_revision_marker_projection_is_body_scoped_content_free_and_value_sensitive() -> None:
    first = (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
        'ID="page" lastModifiedBy="page-owner"><one:Title><one:OE author="title-owner">'
        '<one:T>secret title</one:T></one:OE></one:Title><one:Outline author="Alice" '
        'authorInitials="AA"><one:OEChildren><one:OE lastModifiedBy="Bob">'
        '<one:T>secret body</one:T></one:OE></one:OEChildren></one:Outline></one:Page>'
    )
    changed = first.replace('lastModifiedBy="Bob"', 'lastModifiedBy="Carol"')

    projection = page_revision_marker_projection(first)
    changed_projection = page_revision_marker_projection(changed)

    assert projection["marker_count"] == 3
    assert projection["attribute_counts"] == {
        "author": 1,
        "authorInitials": 1,
        "lastModifiedBy": 1,
    }
    assert projection["node_counts"] == {"OE": 1, "Outline": 1}
    assert projection["schema_version"] == 2
    assert [marker["attribute"] for marker in projection["markers"]] == [
        "author",
        "authorInitials",
        "lastModifiedBy",
    ]
    assert all("value" not in marker for marker in projection["markers"])
    assert projection["marker_values_exposed"] is False
    assert projection["author_metadata_exposed"] is False
    assert projection["sensitive_evidence"] is False
    assert projection["content_exposed"] is False
    assert projection["sha256"] != changed_projection["sha256"]
    assert "Alice" not in str(projection)
    assert "secret" not in str(projection)

    exposed = page_revision_marker_projection(first, expose_values=True)
    assert [marker["value"] for marker in exposed["markers"]] == [
        "Alice",
        "AA",
        "Bob",
    ]
    assert exposed["marker_values_exposed"] is True
    assert exposed["author_metadata_exposed"] is True
    assert exposed["sensitive_evidence"] is True
    assert exposed["content_exposed"] is False
    assert "secret title" not in str(exposed)
    assert "secret body" not in str(exposed)


def test_page_reparent_content_hash_allows_ids_and_tag_indices_but_keeps_rich_semantics() -> None:
    before = (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
        'ID="old-page"><one:TagDef index="1" type="0" symbol="3"/>'
        '<one:Outline objectID="old-outline"><one:OEChildren><one:OE>'
        '<one:List><one:Number/></one:List><one:Tag index="1" completed="true"/>'
        '<one:T><![CDATA[<span style="font-weight:bold">item</span>]]></one:T>'
        '</one:OE></one:OEChildren></one:Outline>'
        '<one:Image objectID="old-image"><one:Data>YQ==</one:Data></one:Image></one:Page>'
    )
    remapped = (
        before.replace('ID="old-page"', 'ID="new-page"')
        .replace('objectID="old-outline"', 'objectID="new-outline"')
        .replace('objectID="old-image"', 'objectID="new-image"')
        .replace('index="1"', 'index="9"')
    )
    changed_tag = remapped.replace('completed="true"', 'completed="false"')
    changed_image = remapped.replace("YQ==", "Yg==")

    assert page_reparent_content_hash(before) == page_reparent_content_hash(remapped)
    assert page_reparent_content_hash(before) != page_reparent_content_hash(changed_tag)
    assert page_reparent_content_hash(before) != page_reparent_content_hash(changed_image)


class _SnapshotClient:
    def __init__(self, *, change_ids: bool = False) -> None:
        self.tree_calls = 0
        self.change_ids = change_ids
        self.calls: list[str] = []

    @staticmethod
    def _tree(section_modified: str, page_id: str = "page") -> dict:
        return {
            "item": {"id": "notebook", "resource_type": "notebook"},
            "children": [
                {
                    "item": {
                        "id": "section",
                        "resource_type": "section",
                        "parent_id": "notebook",
                        "modified": section_modified,
                    },
                    "children": [
                        {
                            "item": {
                                "id": page_id,
                                "resource_type": "page",
                                "section_id": "section",
                                "parent_id": "section",
                                "order": 0,
                            },
                            "children": [],
                        }
                    ],
                }
            ],
        }

    async def call_tool(self, name, _arguments):
        self.calls.append(name)
        if name == "expand_hierarchy":
            self.tree_calls += 1
            return {
                "tree": self._tree(
                    "fresh" if self.tree_calls == 2 else "stale",
                    "changed-page" if self.change_ids and self.tree_calls == 2 else "page",
                )
            }
        if name == "get_page_xml":
            return {
                "xml": (
                    '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
                    'ID="page"><one:Outline objectID="outline"><one:T>body</one:T>'
                    "</one:Outline></one:Page>"
                )
            }
        raise AssertionError(name)


def test_capture_snapshot_refreshes_hierarchy_after_page_evidence() -> None:
    client = _SnapshotClient()

    snapshot = asyncio.run(capture_snapshot(client, "notebook"))

    section = next(item for item in snapshot["items"] if item["id"] == "section")
    assert section["modified"] == "fresh"
    assert snapshot["page_hashes"]["page"]
    assert snapshot["page_canonical_hashes"]["page"]
    assert snapshot["page_reparent_hashes"]["page"]
    assert snapshot["page_semantic_content_identities"]["page"]["complete"] is True
    assert snapshot["page_semantic_content_identities"]["page"]["sha256"]
    assert snapshot["page_semantic_content_identities"]["page"]["persistence_sha256"]
    assert snapshot["page_semantic_content_identities"]["page"]["materialization_sha256"]
    assert snapshot["page_revision_marker_projections"]["page"] == {
        "schema_version": 2,
        "marker_count": 0,
        "attribute_counts": {},
        "node_counts": {},
        "sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
        "markers": [],
        "marker_values_exposed": False,
        "author_metadata_exposed": False,
        "sensitive_evidence": False,
        "content_exposed": False,
    }
    assert snapshot["page_xml_hashes"]["page"]
    assert snapshot["page_capability_projections"]["page"] == {
        "schema_version": 4,
        "capabilities": ["Outline"],
        "object_kind_counts": {"Outline": 1},
        "structural_marker_counts": {},
        "embedded_markup_tag_counts": {},
        "embedded_markup_attribute_name_counts": {},
        "unknown_nodes": [],
        "unsupported_page_roots": [],
        "complete": True,
    }
    assert snapshot["page_objects"]["page"] == [
        {
            "kind": "Outline",
            "id": "outline",
            "callback_id": None,
            "media_type": None,
            "can_delete": True,
            "delete_target_id": "outline",
            "container_object_id": None,
            "parent_object_id": "page",
            "page_id": "page",
        }
    ]
    assert client.tree_calls == 2
    assert client.calls == ["expand_hierarchy", "get_page_xml", "expand_hierarchy"]


def test_capture_snapshot_rejects_hierarchy_id_changes_during_evidence() -> None:
    with pytest.raises(test_utils.InvariantFailure, match="Hierarchy IDs changed"):
        asyncio.run(capture_snapshot(_SnapshotClient(change_ids=True), "notebook"))
