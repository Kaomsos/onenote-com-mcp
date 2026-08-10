from __future__ import annotations

from copy import deepcopy

import pytest

from local_onenote_mcp.bridge import OneNoteBridgeError
from local_onenote_mcp.hierarchy import parse_hierarchy
from local_onenote_mcp.policy import SearchBudget
from local_onenote_mcp.services.search import SearchService


HIERARCHY_XML = """<one:Notebooks xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
  <one:Notebook name="Alpha" ID="n1"><one:Section name="Main" ID="s1">
    <one:Page name="First" ID="p1" pageLevel="1" />
    <one:Page name="Deleted" ID="pr" pageLevel="1" isInRecycleBin="true" />
  </one:Section></one:Notebook>
  <one:Notebook name="Beta" ID="n2"><one:Section name="Notes" ID="s2">
    <one:Page name="Second" ID="p2" pageLevel="1" />
  </one:Section></one:Notebook>
  <one:Notebook name="Closed" ID="n3" isClosed="true"><one:Section name="Old" ID="s3">
    <one:Page name="Third" ID="p3" pageLevel="1" />
  </one:Section></one:Notebook>
</one:Notebooks>"""


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
        return self.items

    @staticmethod
    def without_recycle_bin(items):
        return [item for item in items if item.get("is_in_recycle_bin") is not True]


class FakePages:
    def __init__(self, texts=None):
        self.texts = texts or {"p1": "needle alpha", "p2": "beta needle", "p3": "needle closed", "pr": "needle recycle"}
        self.calls = []

    def xml(self, page_id, page_info):
        self.calls.append(page_id)
        return page_xml(self.texts[page_id])


class FakeBridge:
    def __init__(self, xml="", error=None):
        self.xml = xml
        self.error = error
        self.calls = []

    def call(self, operation, **params):
        self.calls.append((operation, params))
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


def test_global_local_scan_uses_one_snapshot_and_cross_notebook_budget(monkeypatch):
    hierarchy = FakeHierarchy()
    pages = FakePages()
    search = service(hierarchy=hierarchy, pages=pages)
    monkeypatch.setattr(SearchBudget, "current", classmethod(lambda cls: budget()))

    result = search.search("needle", "all_open_notebooks", max_results=20)

    assert hierarchy.calls == 1
    assert [page["id"] for page in result["pages"]] == ["p1", "p2"]
    assert [page["notebook_id"] for page in result["pages"]] == ["n1", "n2"]
    assert result["scope"] == {"resource_type": "all_open_notebooks", "notebook_count": 2}
    assert result["scan_budget"]["candidate_pages"] == 2
    assert result["scan_budget"]["scanned_pages"] == 2
    assert pages.calls == ["p1", "p2"]


def test_global_local_scan_applies_one_max_results_limit(monkeypatch):
    monkeypatch.setattr(SearchBudget, "current", classmethod(lambda cls: budget()))

    result = service().search("needle", "all_open_notebooks", max_results=1, include_snippets=False)

    assert [page["id"] for page in result["pages"]] == ["p1"]
    assert result["count"] == 1


def test_global_local_scan_rejects_combined_candidate_overflow_before_reads(monkeypatch):
    pages = FakePages()
    search = service(pages=pages)
    monkeypatch.setattr(SearchBudget, "current", classmethod(lambda cls: budget(max_pages=1)))

    with pytest.raises(ValueError, match="candidate pages"):
        search.search("needle", "all_open_notebooks")

    assert pages.calls == []


def test_global_scope_recycle_bin_is_optional_but_closed_notebooks_never_join(monkeypatch):
    monkeypatch.setattr(SearchBudget, "current", classmethod(lambda cls: budget()))
    without_recycle = service().search("needle", "all_open_notebooks", include_recycle_bin=False)
    with_recycle = service().search("needle", "all_open_notebooks", include_recycle_bin=True)

    assert [page["id"] for page in without_recycle["pages"]] == ["p1", "p2"]
    assert [page["id"] for page in with_recycle["pages"]] == ["p1", "pr", "p2"]


def test_global_scope_returns_empty_success_for_empty_hierarchy(monkeypatch):
    monkeypatch.setattr(SearchBudget, "current", classmethod(lambda cls: budget()))

    result = service(hierarchy=FakeHierarchy([])).search("needle", "all_open_notebooks")

    assert result["pages"] == []
    assert result["count"] == 0
    assert result["scope"]["notebook_count"] == 0
    assert result["scan_budget"]["candidate_pages"] == 0


@pytest.mark.parametrize(
    ("scope_type", "scope_id", "message"),
    [
        ("all_open_notebooks", "n1", "must be empty"),
        ("notebook", "", "scope_id is required"),
        ("unknown", "", "scope_type must be one of"),
    ],
)
def test_scope_argument_combinations_fail_closed(scope_type, scope_id, message):
    with pytest.raises(ValueError, match=message):
        service().search("needle", scope_type, scope_id)


def test_global_index_uses_empty_start_id_and_hydrates_from_same_catalog(monkeypatch):
    fragment = """<one:Pages xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
      <one:Page name="stale" ID="p2"/><one:Page name="stale" ID="p1"/>
      <one:Page name="closed" ID="p3"/><one:Page name="recycled" ID="pr"/>
    </one:Pages>"""
    bridge = FakeBridge(fragment)
    hierarchy = FakeHierarchy()
    pages = FakePages()
    search = service(bridge=bridge, hierarchy=hierarchy, pages=pages)
    monkeypatch.setattr(SearchBudget, "current", classmethod(lambda cls: budget()))

    result = search.search("needle", "all_open_notebooks", backend="onenote_index")

    assert hierarchy.calls == 1
    assert bridge.calls[0][0] == "find_pages"
    assert bridge.calls[0][1]["start_id"] == ""
    assert [page["id"] for page in result["pages"]] == ["p2", "p1"]
    assert [page["path"] for page in result["pages"]] == ["Beta/Notes/Second", "Alpha/Main/First"]
    assert result["scan_budget"]["hydrated_pages"] == 2
    assert result["scan_budget"]["hydrated_chars"] > 0


def test_global_index_applies_one_result_limit_without_snippet_reads(monkeypatch):
    fragment = """<one:Pages xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
      <one:Page ID="p1"/><one:Page ID="p2"/>
    </one:Pages>"""
    pages = FakePages()
    search = service(bridge=FakeBridge(fragment), pages=pages)
    monkeypatch.setattr(SearchBudget, "current", classmethod(lambda cls: budget()))

    result = search.search(
        "needle",
        "all_open_notebooks",
        backend="onenote_index",
        max_results=1,
        include_snippets=False,
    )

    assert [page["id"] for page in result["pages"]] == ["p1"]
    assert result["scan_budget"]["candidate_pages"] == 2
    assert result["scan_budget"]["hydrated_pages"] == 0
    assert pages.calls == []


def test_index_snippet_hydration_is_bounded_before_page_reads(monkeypatch):
    fragment = """<one:Pages xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
      <one:Page ID="p1"/><one:Page ID="p2"/>
    </one:Pages>"""
    pages = FakePages()
    search = service(bridge=FakeBridge(fragment), pages=pages)
    monkeypatch.setattr(SearchBudget, "current", classmethod(lambda cls: budget(max_pages=1)))

    with pytest.raises(ValueError, match="snippet hydration"):
        search.search("needle", "all_open_notebooks", backend="onenote_index")

    assert pages.calls == []


def test_index_snippet_hydration_enforces_total_character_budget(monkeypatch):
    fragment = """<one:Pages xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
      <one:Page ID="p1"/>
    </one:Pages>"""
    search = service(bridge=FakeBridge(fragment), pages=FakePages({"p1": "needle"}))
    monkeypatch.setattr(
        SearchBudget,
        "current",
        classmethod(lambda cls: budget(max_total_chars=5)),
    )

    with pytest.raises(RuntimeError, match="MAX_SEARCH_TOTAL_CHARS"):
        search.search("needle", "all_open_notebooks", backend="onenote_index")


def test_index_snippet_hydration_enforces_elapsed_time_budget(monkeypatch):
    fragment = """<one:Pages xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
      <one:Page ID="p1"/>
    </one:Pages>"""
    search = service(bridge=FakeBridge(fragment))
    clock = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(SearchBudget, "current", classmethod(lambda cls: budget(max_seconds=1)))
    monkeypatch.setattr("local_onenote_mcp.services.search.time.monotonic", lambda: next(clock))

    with pytest.raises(RuntimeError, match="snippet hydration exceeded"):
        search.search("needle", "all_open_notebooks", backend="onenote_index")


def test_index_failure_does_not_fall_back_to_local_scan(monkeypatch):
    bridge = FakeBridge(error=OneNoteBridgeError("index unavailable"))
    pages = FakePages()
    search = service(bridge=bridge, pages=pages)
    monkeypatch.setattr(SearchBudget, "current", classmethod(lambda cls: budget()))

    with pytest.raises(RuntimeError, match="index unavailable"):
        search.search("needle", "all_open_notebooks", backend="onenote_index")

    assert pages.calls == []
