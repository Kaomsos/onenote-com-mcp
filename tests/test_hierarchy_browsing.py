from __future__ import annotations

import pytest

from local_onenote_mcp.services import hierarchy as hierarchy_module
from local_onenote_mcp.services.hierarchy import HierarchyService
from tests.test_metadata_query import CATALOG_XML, PAGES_XML


class BrowsingBridge:
    def __init__(self, *, sections_xml: str = CATALOG_XML, pages_xml: str = PAGES_XML):
        self.sections_xml = sections_xml
        self.pages_xml = pages_xml
        self.calls: list[tuple[str, dict]] = []

    def call(self, operation, **params):
        self.calls.append((operation, params))
        assert operation == "get_hierarchy"
        return {
            "xml": {
                2: self.sections_xml,
                3: self.sections_xml,
                4: self.pages_xml,
            }[params["scope"]]
        }


def ids(node: dict) -> list[str]:
    return [child["item"]["id"] for child in node["children"]]


def flatten(node: dict) -> list[dict]:
    return [node["item"], *(item for child in node["children"] for item in flatten(child))]


def test_list_notebooks_is_open_only_ordered_and_uses_notebooks_scope():
    bridge = BrowsingBridge()
    result = HierarchyService(bridge).list_notebooks()

    assert [item["id"] for item in result["items"]] == ["n1", "n2"]
    assert result["count"] == 2
    assert [call[1]["scope"] for call in bridge.calls] == [2]


def test_expand_notebook_and_group_stop_at_section_leaves_in_snapshot_order():
    bridge = BrowsingBridge()
    service = HierarchyService(bridge)

    notebook = service.expand_typed("n1", "notebook")["tree"]
    group = service.expand_typed("g1", "section_group")["tree"]

    assert ids(notebook) == ["g1", "sr"]
    assert ids(notebook["children"][0]) == ["g2", "s1"]
    assert ids(notebook["children"][0]["children"][0]) == ["s2"]
    assert all(
        not node["children"]
        for node in (
            notebook["children"][1],
            notebook["children"][0]["children"][1],
            notebook["children"][0]["children"][0]["children"][0],
        )
    )
    assert ids(group) == ["g2", "s1"]
    assert {item["resource_type"] for item in flatten(group)} == {
        "section_group",
        "section",
    }
    assert [call[1]["scope"] for call in bridge.calls] == [3, 3]


def test_expand_section_and_page_reuse_complete_indentation_tree_without_siblings():
    bridge = BrowsingBridge()
    service = HierarchyService(bridge)

    section = service.expand_typed("s1", "section")["tree"]
    page = service.expand_typed("p1", "page")["tree"]

    assert ids(section) == ["p1", "p3"]
    assert ids(section["children"][0]) == ["p2"]
    assert ids(page) == ["p2"]
    assert "p3" not in {item["id"] for item in flatten(page)}
    assert [call[1]["scope"] for call in bridge.calls] == [4, 4]


@pytest.mark.parametrize("root_id", ["n1", "g1", "s1", "p1"])
def test_expand_hierarchy_accepts_all_four_root_types(root_id):
    bridge = BrowsingBridge()
    tree = HierarchyService(bridge).expand_hierarchy(root_id)["tree"]

    assert tree["item"]["id"] == root_id
    assert bridge.calls[0][1]["scope"] == 4


def test_expand_hierarchy_applies_numeric_depth_and_recycle_contract():
    service = HierarchyService(BrowsingBridge())

    bounded = service.expand_hierarchy("n1", max_depth=1)["tree"]
    recycled = service.expand_hierarchy(
        "trash", max_depth=2, include_recycle_bin=True
    )["tree"]

    assert ids(bounded) == ["g1", "sr"]
    assert all(not child["children"] for child in bounded["children"])
    assert recycled["item"]["id"] == "trash"
    assert ids(recycled) == ["sd"]
    with pytest.raises(ValueError, match="recycle bin"):
        service.expand_hierarchy("trash")


@pytest.mark.parametrize(
    ("root_id", "resource_type", "message"),
    [
        ("missing", "section", "No object"),
        ("s1", "notebook", "does not identify"),
        ("nc", "notebook", "closed Notebook"),
        ("sc", "section", "closed Notebook"),
        ("sd", "section", "recycle bin"),
    ],
)
def test_typed_expand_fails_closed_for_invalid_roots(root_id, resource_type, message):
    with pytest.raises(ValueError, match=message):
        HierarchyService(BrowsingBridge()).expand_typed(root_id, resource_type)


def test_duplicate_ids_and_broken_relationships_fail_closed():
    duplicate = CATALOG_XML.replace('ID="s2"', 'ID="s1"')
    with pytest.raises(ValueError, match="duplicate object IDs"):
        HierarchyService(BrowsingBridge(sections_xml=duplicate)).expand_typed(
            "n1", "notebook"
        )

    broken = [
        {"id": "n", "resource_type": "notebook", "parent_id": None},
        {
            "id": "p",
            "resource_type": "page",
            "parent_id": "missing",
            "section_id": "missing",
            "notebook_id": "n",
            "page_level": 1,
            "parent_page_id": None,
        },
    ]
    with pytest.raises(ValueError, match="incomplete"):
        HierarchyService._validate_hierarchy_snapshot(broken)


def test_exact_expand_is_not_blocked_by_unrelated_notebook_indentation_failure():
    unrelated_failure = PAGES_XML.replace(
        '<one:Page name="Beta Page" ID="pb" pageLevel="1" />',
        (
            '<one:Page name="Beta Page" ID="pb" pageLevel="1" />'
            '<one:Page name="Beta Jump" ID="pb-jump" pageLevel="3" />'
        ),
    ).replace(
        '<one:Page name="Closed Page" ID="pc" pageLevel="1" />',
        '<one:Page name="Closed Page" ID="" pageLevel="1" />',
    ).replace(
        "</one:Notebooks>",
        '<one:Notebook name="Fresh Fixture" ID="fixture" /></one:Notebooks>',
    )
    service = HierarchyService(BrowsingBridge(pages_xml=unrelated_failure))

    tree = service.expand_hierarchy("fixture")["tree"]

    assert tree == {
        "item": {
            "id": "fixture",
            "resource_type": "notebook",
            "name": "Fresh Fixture",
            "path": "Fresh Fixture",
            "parent_id": None,
            "depth": 0,
            "created": None,
            "modified": None,
            "is_in_recycle_bin": False,
            "relationship_source": "com",
            "section_group_ids": [],
            "section_ids": [],
            "is_open": None,
        },
        "children": [],
    }
    with pytest.raises(ValueError, match="discontinuous Page indentation"):
        service.expand_hierarchy("n2")


def test_response_boundary_fails_instead_of_returning_a_partial_tree(monkeypatch):
    service = HierarchyService(BrowsingBridge())
    monkeypatch.setattr(hierarchy_module, "MAX_HIERARCHY_TREE_ITEMS", 2)

    with pytest.raises(ValueError, match="public response boundary"):
        service.expand_typed("n1", "notebook")


def test_bridge_read_failure_is_not_converted_to_an_empty_tree():
    class BrokenBridge:
        def call(self, operation, **params):
            raise RuntimeError("COM read failed")

    with pytest.raises(RuntimeError, match="COM read failed"):
        HierarchyService(BrokenBridge()).expand_hierarchy("n1")
