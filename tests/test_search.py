from __future__ import annotations

from copy import deepcopy

import pytest

from local_onenote_mcp.bridge import OneNoteBridgeError
from local_onenote_mcp.hierarchy import parse_hierarchy
from local_onenote_mcp.policy import SearchBudget
from local_onenote_mcp.services.search import SearchService


HIERARCHY_XML = """<one:Notebooks xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
  <one:Notebook name="Alpha" ID="n1">
    <one:SectionGroup name="Group" ID="g1">
      <one:Section name="Main" ID="s1">
        <one:Page name="First" ID="p1" pageLevel="1" />
        <one:Page name="Deleted" ID="pr" pageLevel="1" isInRecycleBin="true" />
      </one:Section>
      <one:Section name="Other" ID="s1b">
        <one:Page name="Grouped" ID="p1b" pageLevel="1" />
      </one:Section>
    </one:SectionGroup>
    <one:Section name="Root" ID="sroot">
      <one:Page name="At Root" ID="p1c" pageLevel="1" />
    </one:Section>
  </one:Notebook>
  <one:Notebook name="Beta" ID="n2"><one:Section name="Notes" ID="s2">
    <one:Page name="Second" ID="p2" pageLevel="1" />
  </one:Section></one:Notebook>
  <one:Notebook name="Closed" ID="n3" isClosed="true"><one:Section name="Old" ID="s3">
    <one:Page name="Third" ID="p3" pageLevel="1" />
  </one:Section></one:Notebook>
</one:Notebooks>"""


def result_xml(*page_ids: str) -> str:
    pages = "".join(f'<one:Page name="stale" ID="{page_id}" />' for page_id in page_ids)
    return (
        '<one:Pages xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">'
        f"{pages}</one:Pages>"
    )


def page_xml(text: str) -> str:
    return (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">'
        f"<one:Outline><one:OEChildren><one:OE><one:T><![CDATA[{text}]]></one:T>"
        "</one:OE></one:OEChildren></one:Outline></one:Page>"
    )


class FakeHierarchy:
    def __init__(self, items=None):
        self.items = deepcopy(parse_hierarchy(HIERARCHY_XML) if items is None else items)
        self.calls = 0

    def resources(self, include_recycle_bin=False):
        self.calls += 1
        return deepcopy(self.items)

    @staticmethod
    def without_recycle_bin(items):
        return [item for item in items if item.get("is_in_recycle_bin") is not True]


class FakePages:
    def __init__(self, texts=None):
        self.texts = texts or {
            "p1": "needle alpha",
            "p1b": "needle grouped",
            "p1c": "needle root",
            "p2": "beta needle",
            "p3": "needle closed",
            "pr": "needle recycle",
        }
        self.calls = []

    def xml(self, page_id, page_info, *, _timeout_seconds=None):
        self.calls.append((page_id, page_info, _timeout_seconds))
        return page_xml(self.texts[page_id])


class FakeBridge:
    def __init__(self, xml="", error=None):
        self.xml = xml
        self.error = error
        self.calls = []

    def call(self, operation, *, _timeout_seconds=None, **params):
        self.calls.append((operation, params, _timeout_seconds))
        if self.error:
            raise self.error
        return {"xml": self.xml}


def service(*, bridge=None, hierarchy=None, pages=None):
    return SearchService(bridge or FakeBridge(), hierarchy or FakeHierarchy(), pages or FakePages())


def budget(**overrides):
    values = {
        "max_pages": 20,
        "max_page_chars": 1_000,
        "max_total_chars": 10_000,
        "max_seconds": 30,
        "snippet_chars": 80,
    }
    values.update(overrides)
    return SearchBudget(**values)


@pytest.fixture(autouse=True)
def fixed_budget(monkeypatch):
    monkeypatch.setattr(SearchBudget, "current", classmethod(lambda cls: budget()))


def test_root_search_uses_one_find_pages_call_and_filters_unprovable_results():
    bridge = FakeBridge(result_xml("p2", "p1", "p3", "pr", "unknown", "p1"))
    hierarchy = FakeHierarchy()

    result = service(bridge=bridge, hierarchy=hierarchy).search(
        "left AND right",
        {"mode": "root"},
        include_snippets=False,
    )

    assert hierarchy.calls == 1
    assert len(bridge.calls) == 1
    operation, params, timeout = bridge.calls[0]
    assert operation == "find_pages"
    assert params == {
        "start_id": "",
        "query": "left AND right",
        "include_unindexed": False,
        "display": False,
        "schema": 2,
    }
    assert 0 < timeout <= 30
    assert [page["id"] for page in result["pages"]] == ["p2", "p1"]
    assert [page["path"] for page in result["pages"]] == [
        "Beta/Notes/Second",
        "Alpha/Group/Main/First",
    ]
    assert result["scope"] == {"resource_type": "root", "notebook_count": 2}
    assert result["search_backend"] == "onenote_index"
    assert result["pagination_consistency"] == "live_index"


@pytest.mark.parametrize(
    ("start_id", "resource_type", "expected"),
    [
        ("n1", "notebook", ["p1", "p1b", "p1c"]),
        ("g1", "section_group", ["p1", "p1b"]),
        ("s1", "section", ["p1"]),
    ],
)
def test_start_node_scopes_follow_exact_parent_chain(start_id, resource_type, expected):
    bridge = FakeBridge(result_xml("p2", "p1", "p1b", "p1c", "p3", "pr"))

    result = service(bridge=bridge).search(
        "needle",
        {"mode": "start_node", "start_node_id": start_id},
        include_snippets=False,
    )

    assert [page["id"] for page in result["pages"]] == expected
    assert bridge.calls[0][1]["start_id"] == start_id
    assert result["scope"]["id"] == start_id
    assert result["scope"]["resource_type"] == resource_type


def test_recycle_bin_result_and_start_scope_require_explicit_opt_in():
    search = service(bridge=FakeBridge(result_xml("pr", "p1")))

    without = search.search(
        "needle",
        {"mode": "start_node", "start_node_id": "s1"},
        include_snippets=False,
    )
    with_recycle = search.search(
        "needle",
        {"mode": "start_node", "start_node_id": "s1"},
        include_snippets=False,
        include_recycle_bin=True,
    )

    assert [page["id"] for page in without["pages"]] == ["p1"]
    assert [page["id"] for page in with_recycle["pages"]] == ["pr", "p1"]


@pytest.mark.parametrize(
    ("scope", "message"),
    [
        ({"mode": "start_node", "start_node_id": "p1"}, "notebook, section_group, or section"),
        ({"mode": "start_node", "start_node_id": "missing"}, "No hierarchy object"),
        ({"mode": "start_node", "start_node_id": "n3"}, "open Notebook"),
        ({"mode": "root", "start_node_id": "n1"}, "additional fields"),
        ({"mode": "start_node"}, "requires only"),
        ({"mode": "unknown"}, "scope.mode"),
    ],
)
def test_invalid_scope_rejected_before_find_pages(scope, message):
    bridge = FakeBridge()

    with pytest.raises(ValueError, match=message):
        service(bridge=bridge).search("needle", scope)

    assert bridge.calls == []


@pytest.mark.parametrize(
    ("query", "scope", "offset", "page_size", "message"),
    [
        (" ", {"mode": "root"}, 0, 200, "query is required"),
        ("x", {"mode": "root"}, -1, 200, "offset"),
        ("x", {"mode": "root"}, 0, 0, "page_size"),
        ("x", {"mode": "root"}, 0, 201, "page_size"),
    ],
)
def test_query_and_pagination_validation(query, scope, offset, page_size, message):
    with pytest.raises(ValueError, match=message):
        service().search(query, scope, offset=offset, page_size=page_size)


def test_stateless_pagination_preserves_index_order_and_handles_end_and_overflow():
    xml = result_xml("p2", "p1", "p1b", "p1c")
    search = service(bridge=FakeBridge(xml))

    first = search.search("needle", {"mode": "root"}, page_size=2, include_snippets=False)
    second = search.search(
        "needle", {"mode": "root"}, offset=2, page_size=2, include_snippets=False
    )
    beyond = search.search(
        "needle", {"mode": "root"}, offset=99, page_size=2, include_snippets=False
    )

    assert [page["id"] for page in first["pages"]] == ["p2", "p1"]
    assert first["total_matches"] == 4
    assert first["has_more"] is True
    assert first["next_offset"] == 2
    assert [page["id"] for page in second["pages"]] == ["p1b", "p1c"]
    assert second["has_more"] is False
    assert second["next_offset"] is None
    assert beyond["pages"] == []
    assert beyond["count"] == 0
    assert beyond["total_matches"] == 4
    assert beyond["has_more"] is False
    assert len(search.bridge.calls) == 3


def test_candidate_budget_is_checked_before_offset_slice(monkeypatch):
    pages = FakePages()
    monkeypatch.setattr(SearchBudget, "current", classmethod(lambda cls: budget(max_pages=1)))

    with pytest.raises(ValueError, match="returned 2 candidate pages"):
        service(bridge=FakeBridge(result_xml("p1", "p2")), pages=pages).search(
            "needle", {"mode": "root"}, offset=99, page_size=1
        )

    assert pages.calls == []


def test_snippets_hydrate_only_current_page_with_remaining_timeout():
    pages = FakePages()

    result = service(bridge=FakeBridge(result_xml("p1", "p2")), pages=pages).search(
        "needle", {"mode": "root"}, offset=1, page_size=1
    )

    assert [call[0] for call in pages.calls] == ["p2"]
    assert 0 < pages.calls[0][2] <= 30
    assert "needle" in result["pages"][0]["snippet"]
    assert result["scan_budget"]["hydrated_pages"] == 1
    assert result["scan_budget"]["hydrated_chars"] > 0


def test_snippet_hydration_enforces_total_character_budget(monkeypatch):
    monkeypatch.setattr(
        SearchBudget,
        "current",
        classmethod(lambda cls: budget(max_total_chars=5)),
    )

    with pytest.raises(RuntimeError, match="MAX_SEARCH_TOTAL_CHARS"):
        service(bridge=FakeBridge(result_xml("p1"))).search("needle", {"mode": "root"})


def test_snippet_hydration_caps_per_page_processing_and_returned_snippet(monkeypatch):
    pages = FakePages({"p1": "prefix needle " + ("x" * 200)})
    monkeypatch.setattr(
        SearchBudget,
        "current",
        classmethod(
            lambda cls: budget(
                max_page_chars=40,
                max_total_chars=100,
                snippet_chars=20,
            )
        ),
    )

    result = service(bridge=FakeBridge(result_xml("p1")), pages=pages).search(
        "needle", {"mode": "root"}
    )

    assert result["scan_budget"]["hydrated_chars"] == 40
    assert len(result["pages"][0]["snippet"]) <= 20


def test_find_pages_and_processing_share_elapsed_time_budget(monkeypatch):
    clock = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(SearchBudget, "current", classmethod(lambda cls: budget(max_seconds=1)))
    monkeypatch.setattr("local_onenote_mcp.services.search.time.monotonic", lambda: next(clock))

    with pytest.raises(RuntimeError, match="FindPages result processing"):
        service(bridge=FakeBridge(result_xml("p1"))).search(
            "needle", {"mode": "root"}, include_snippets=False
        )


def test_find_pages_failure_has_no_local_scan_fallback():
    pages = FakePages()
    bridge = FakeBridge(error=OneNoteBridgeError("index unavailable"))

    with pytest.raises(RuntimeError, match="index unavailable"):
        service(bridge=bridge, pages=pages).search("needle", {"mode": "root"})

    assert len(bridge.calls) == 1
    assert pages.calls == []


def test_empty_root_is_an_empty_success_without_com_call():
    bridge = FakeBridge()

    result = service(bridge=bridge, hierarchy=FakeHierarchy([])).search(
        "needle", {"mode": "root"}
    )

    assert result["pages"] == []
    assert result["total_matches"] == 0
    assert result["scope"] == {"resource_type": "root", "notebook_count": 0}
    assert bridge.calls == []


def test_internal_local_text_search_remains_available_but_is_not_public_fallback():
    pages = FakePages()
    matches, stats = service(pages=pages).local_text_search(
        "s1", "needle", 10, False, budget=budget()
    )

    assert [page["id"] for page in matches] == ["p1"]
    assert stats["scanned_pages"] == 1
