from __future__ import annotations

import pytest

from local_onenote_mcp.services.hierarchy import HierarchyService


CATALOG_XML = """<one:Notebooks xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
  <one:Notebook name="Alpha" ID="n1" lastModifiedTime="2026-08-10T00:00:00Z">
    <one:SectionGroup name="Group" ID="g1" lastModifiedTime="2026-08-10T01:00:00+00:00">
      <one:SectionGroup name="Nested" ID="g2" lastModifiedTime="2026-08-10T02:00:00Z">
        <one:Section name="Nested Section" ID="s2" lastModifiedTime="2026-08-10T03:00:00Z" />
      </one:SectionGroup>
      <one:Section name="Main" ID="s1" lastModifiedTime="2026-08-10T04:00:00Z" />
    </one:SectionGroup>
    <one:Section name="Root" ID="sr" lastModifiedTime="2026-08-10T05:00:00Z" />
    <one:SectionGroup name="OneNote_RecycleBin" ID="trash" isRecycleBin="true">
      <one:Section name="Deleted" ID="sd" />
    </one:SectionGroup>
  </one:Notebook>
  <one:Notebook name="Beta" ID="n2" lastModifiedTime="2026-08-11T00:00:00+01:00">
    <one:Section name="Beta Section" ID="sb" />
  </one:Notebook>
  <one:Notebook name="Closed" ID="nc" isClosed="true">
    <one:Section name="Closed Section" ID="sc" />
  </one:Notebook>
</one:Notebooks>"""


PAGES_XML = """<one:Notebooks xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
  <one:Notebook name="Alpha" ID="n1">
    <one:SectionGroup name="Group" ID="g1">
      <one:SectionGroup name="Nested" ID="g2">
        <one:Section name="Nested Section" ID="s2">
          <one:Page name="Nested Page" ID="p4" pageLevel="1" />
        </one:Section>
      </one:SectionGroup>
      <one:Section name="Main" ID="s1">
        <one:Page name="Parent" ID="p1" pageLevel="1" lastModifiedTime="2026-08-10T06:00:00Z" />
        <one:Page name="Child" ID="p2" pageLevel="2" lastModifiedTime="2026-08-10T07:00:00+00:00" />
        <one:Page name="Sibling" ID="p3" pageLevel="1" lastModifiedTime="2026-08-10T08:00:00Z" />
      </one:Section>
    </one:SectionGroup>
    <one:Section name="Root" ID="sr"><one:Page name="Root Page" ID="pr" pageLevel="1" /></one:Section>
    <one:SectionGroup name="OneNote_RecycleBin" ID="trash" isRecycleBin="true">
      <one:Section name="Deleted" ID="sd"><one:Page name="Deleted Page" ID="pd" pageLevel="1" /></one:Section>
    </one:SectionGroup>
  </one:Notebook>
  <one:Notebook name="Beta" ID="n2"><one:Section name="Beta Section" ID="sb">
    <one:Page name="Beta Page" ID="pb" pageLevel="1" />
  </one:Section></one:Notebook>
  <one:Notebook name="Closed" ID="nc" isClosed="true"><one:Section name="Closed Section" ID="sc">
    <one:Page name="Closed Page" ID="pc" pageLevel="1" />
  </one:Section></one:Notebook>
</one:Notebooks>"""


SECTION_FRAGMENT = """<one:Section xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" name="Main" ID="s1">
  <one:Page name="Parent" ID="p1" pageLevel="1" lastModifiedTime="2026-08-10T06:00:00Z" />
  <one:Page name="Child" ID="p2" pageLevel="2" lastModifiedTime="2026-08-10T07:00:00Z" />
  <one:Page name="Sibling" ID="p3" pageLevel="1" lastModifiedTime="2026-08-10T08:00:00Z" />
  <one:Section name="Unprovable" ID="unknown-section">
    <one:Page name="Outside" ID="outside" pageLevel="1" />
  </one:Section>
</one:Section>"""


GROUP_FRAGMENT = """<one:SectionGroup xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" name="Group" ID="g1">
  <one:SectionGroup name="Nested" ID="g2"><one:Section name="Nested Section" ID="s2" /></one:SectionGroup>
  <one:Section name="Main" ID="s1" />
  <one:Section name="Foreign" ID="sb" />
</one:SectionGroup>"""


class FakeBridge:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def call(self, operation, **params):
        self.calls.append((operation, params))
        key = (params.get("start_id", ""), params.get("scope"))
        xml = {
            ("", 2): CATALOG_XML,
            ("", 3): CATALOG_XML,
            ("", 4): PAGES_XML,
            ("g1", 3): GROUP_FRAGMENT,
            ("n1", 3): CATALOG_XML,
            ("n1", 4): PAGES_XML,
            ("g1", 4): PAGES_XML,
            ("s1", 4): SECTION_FRAGMENT,
        }.get(key)
        if xml is None:
            raise AssertionError(f"Unexpected GetHierarchy call: {key}")
        return {"xml": xml}


def service():
    bridge = FakeBridge()
    return HierarchyService(bridge), bridge


@pytest.mark.parametrize(
    ("resource_type", "scope", "expected_scope", "expected_ids"),
    [
        ("notebook", None, 2, ["n1", "n2"]),
        ("section_group", {"mode": "root"}, 3, ["g1", "g2"]),
        ("section", {"mode": "root"}, 3, ["s2", "s1", "sr", "sb"]),
        ("page", {"mode": "root"}, 4, ["p4", "p1", "p2", "p3", "pr", "pb"]),
    ],
)
def test_root_queries_use_one_shallow_native_call_and_exclude_closed_and_recycle_bin(
    resource_type, scope, expected_scope, expected_ids
):
    query, bridge = service()

    result = query.metadata_query(resource_type, scope)

    assert [item["id"] for item in result["items"]] == expected_ids
    assert bridge.calls == [
        (
            "get_hierarchy",
            {"start_id": "", "scope": expected_scope, "schema": 2},
        )
    ]
    assert result["scope"] == {"mode": "root", "notebook_count": 2}
    assert result["resource_type"] == resource_type
    assert result["query_kind"] == "hierarchy_metadata"
    assert result["pagination_consistency"] == "live_hierarchy"


def test_start_node_container_query_uses_catalog_then_native_fragment_and_excludes_self():
    query, bridge = service()

    result = query.metadata_query(
        "section_group", {"mode": "start_node", "start_node_id": "g1"}
    )

    assert [item["id"] for item in result["items"]] == ["g2"]
    assert [call[1]["scope"] for call in bridge.calls] == [3, 3]
    assert [call[1]["start_id"] for call in bridge.calls] == ["", "g1"]
    assert result["scope"] == {
        "mode": "start_node",
        "resource_type": "section_group",
        "id": "g1",
        "path": "Alpha/Group",
        "notebook_id": "n1",
    }


def test_start_section_page_query_rebases_fragment_and_derives_indentation_parent():
    query, bridge = service()

    result = query.metadata_query(
        "page",
        {"mode": "start_node", "start_node_id": "s1"},
        name_equals="Child",
        section_id="s1",
        parent_page_id="p1",
    )

    assert [item["id"] for item in result["items"]] == ["p2"]
    assert result["items"][0]["path"] == "Alpha/Group/Main/Child"
    assert result["items"][0]["notebook_id"] == "n1"
    assert result["items"][0]["section_id"] == "s1"
    assert [call[1]["scope"] for call in bridge.calls] == [3, 4]


def test_direct_parent_name_time_and_live_pagination_filters_are_strict_and_ordered():
    query, _ = service()

    first = query.metadata_query(
        "page",
        {"mode": "root"},
        name_contains="i",
        modified_after="2026-08-10T06:30:00+00:00",
        modified_before="2026-08-10T09:00:00Z",
        page_size=1,
    )
    second = query.metadata_query(
        "page",
        {"mode": "root"},
        name_contains="i",
        modified_after="2026-08-10T06:30:00Z",
        modified_before="2026-08-10T09:00:00+00:00",
        offset=1,
        page_size=1,
    )

    assert [item["id"] for item in first["items"]] == ["p2"]
    assert first["total_matches"] == 2
    assert first["has_more"] is True
    assert first["next_offset"] == 1
    assert [item["id"] for item in second["items"]] == ["p3"]
    assert second["has_more"] is False
    assert second["next_offset"] is None

    direct = query.metadata_query(
        "section", {"mode": "root"}, parent_id="g1"
    )
    assert [item["id"] for item in direct["items"]] == ["s1"]


@pytest.mark.parametrize(
    ("resource_type", "scope", "kwargs", "message"),
    [
        ("page", None, {}, "scope is required"),
        ("page", {"mode": "start_node", "start_node_id": "p1"}, {}, "No hierarchy object"),
        ("section", {"mode": "start_node", "start_node_id": "s1"}, {}, "must identify"),
        ("page", {"mode": "start_node", "start_node_id": "nc"}, {}, "open Notebook"),
        ("page", {"mode": "start_node", "start_node_id": "trash"}, {}, "recycle bin"),
        ("page", {"mode": "root", "extra": True}, {}, "additional fields"),
        ("page", {"mode": "root"}, {"section_id": "sc"}, "verified scope"),
        ("page", {"mode": "start_node", "start_node_id": "s1"}, {"section_id": "sb"}, "verified scope"),
        ("page", {"mode": "root"}, {"parent_page_id": "missing"}, "verified scope"),
        ("page", {"mode": "root"}, {"offset": -1}, "offset"),
        ("page", {"mode": "root"}, {"page_size": 201}, "page_size"),
        ("page", {"mode": "root"}, {"modified_after": "2026-08-10"}, "RFC 3339"),
        (
            "page",
            {"mode": "root"},
            {"modified_after": "2026-08-11T00:00:00Z", "modified_before": "2026-08-10T00:00:00Z"},
            "earlier",
        ),
    ],
)
def test_invalid_scope_relationship_time_and_pagination_fail_closed(
    resource_type, scope, kwargs, message
):
    query, _ = service()

    with pytest.raises(ValueError, match=message):
        query.metadata_query(resource_type, scope, **kwargs)


def test_include_recycle_bin_does_not_reintroduce_closed_notebook_or_escape_start_scope():
    query, _ = service()

    root = query.metadata_query(
        "page", {"mode": "root"}, include_recycle_bin=True
    )
    scoped = query.metadata_query(
        "page",
        {"mode": "start_node", "start_node_id": "s1"},
        include_recycle_bin=True,
    )

    assert "pd" in [item["id"] for item in root["items"]]
    assert "pc" not in [item["id"] for item in root["items"]]
    assert [item["id"] for item in scoped["items"]] == ["p1", "p2", "p3"]


def test_overflow_offset_is_an_empty_success_with_pre_page_total():
    query, _ = service()

    result = query.metadata_query("notebook", offset=99, page_size=1)

    assert result["items"] == []
    assert result["count"] == 0
    assert result["total_matches"] == 2
    assert result["has_more"] is False
    assert result["next_offset"] is None


@pytest.mark.parametrize(
    ("resource_type", "start_id", "expected_ids"),
    [
        ("section_group", "n1", ["g1", "g2"]),
        ("section_group", "g1", ["g2"]),
        ("section", "n1", ["s2", "s1", "sr"]),
        ("section", "g1", ["s2", "s1"]),
        ("page", "n1", ["p4", "p1", "p2", "p3", "pr"]),
        ("page", "g1", ["p4", "p1", "p2", "p3"]),
        ("page", "s1", ["p1", "p2", "p3"]),
    ],
)
def test_exact_native_start_type_matrix(resource_type, start_id, expected_ids):
    query, bridge = service()

    result = query.metadata_query(
        resource_type, {"mode": "start_node", "start_node_id": start_id}
    )

    assert [item["id"] for item in result["items"]] == expected_ids
    assert len(bridge.calls) == 2
    assert bridge.calls[0][1]["start_id"] == ""
    assert bridge.calls[0][1]["scope"] == 3
    assert bridge.calls[1][1]["start_id"] == start_id
    assert bridge.calls[1][1]["scope"] == (4 if resource_type == "page" else 3)


def test_empty_root_is_successful_and_still_uses_one_native_call():
    empty_xml = (
        '<one:Notebooks xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" />'
    )

    class EmptyBridge:
        def __init__(self):
            self.calls = []

        def call(self, operation, **params):
            self.calls.append((operation, params))
            return {"xml": empty_xml}

    bridge = EmptyBridge()
    query = HierarchyService(bridge)

    notebook = query.metadata_query("notebook")
    pages = query.metadata_query("page", {"mode": "root"})

    assert notebook["items"] == []
    assert notebook["scope"] == {"mode": "root", "notebook_count": 0}
    assert pages["items"] == []
    assert pages["scope"] == {"mode": "root", "notebook_count": 0}
    assert [call[1]["scope"] for call in bridge.calls] == [2, 4]


def test_malformed_native_xml_fails_without_root_or_findmeta_fallback():
    class BrokenBridge:
        def __init__(self):
            self.calls = []

        def call(self, operation, **params):
            self.calls.append((operation, params))
            return {"xml": "<broken"}

    bridge = BrokenBridge()

    with pytest.raises(Exception):
        HierarchyService(bridge).metadata_query("page", {"mode": "root"})

    assert len(bridge.calls) == 1
    assert bridge.calls[0][0] == "get_hierarchy"


def test_list_notebooks_equals_unfiltered_query_for_stable_open_hierarchy():
    query, _ = service()

    listed = query.list_notebooks()
    queried = query.metadata_query("notebook")

    assert [item["id"] for item in listed["items"]] == [
        item["id"] for item in queried["items"]
    ] == ["n1", "n2"]
    assert listed["count"] == queried["count"] == 2
