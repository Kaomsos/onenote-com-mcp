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


GAPPED_PAGES_XML = PAGES_XML.replace(
    '<one:Page name="Beta Page" ID="pb" pageLevel="1" />',
    (
        '<one:Page name="Beta Page" ID="pb" pageLevel="1" />'
        '<one:Page name="Beta Jump" ID="pb-jump" pageLevel="3" />'
        '<one:Page name="Beta Jump Sibling" ID="pb-jump-2" pageLevel="3" />'
    ),
).replace(
    "  </one:Section></one:Notebook>\n  <one:Notebook name=\"Closed\"",
    (
        '  </one:Section><one:Section name="Beta Clean Section" ID="sb-clean">'
        '<one:Page name="Beta Clean Page" ID="pb-clean" pageLevel="1" />'
        '</one:Section></one:Notebook>\n  <one:Notebook name="Closed"'
    ),
)


def _gapped_service() -> HierarchyService:
    return HierarchyService(BrowsingBridge(pages_xml=GAPPED_PAGES_XML))


def test_expand_maps_adjacent_l3_as_direct_child_of_preceding_l1():
    service = _gapped_service()

    section = service.expand_typed("sb", "section")["tree"]
    page = service.expand_typed("pb", "page")["tree"]
    hierarchy = service.expand_hierarchy("sb")["tree"]

    for tree in (section, hierarchy):
        assert ids(tree) == ["pb"]
        assert ids(tree["children"][0]) == ["pb-jump", "pb-jump-2"]
        assert all(
            child["item"]["page_level"] == 3
            and child["item"]["parent_page_id"] == "pb"
            and child["item"]["id"] != "pb"
            and not child["children"]
            for child in tree["children"][0]["children"]
        )
        assert "l2" not in {item["id"] for item in flatten(tree)}
        assert {item["page_level"] for item in flatten(tree) if item["resource_type"] == "page"} == {
            1,
            3,
        }

    assert ids(page) == ["pb-jump", "pb-jump-2"]
    assert all(child["item"]["parent_page_id"] == "pb" for child in page["children"])


def test_expand_of_ungapped_root_is_not_blocked_by_sibling_section_l1_l3_gap():
    service = _gapped_service()

    notebook = service.expand_hierarchy("n2")["tree"]
    section = service.expand_typed("sb-clean", "section")["tree"]

    assert ids(notebook) == ["sb", "sb-clean"]
    assert "pb-jump" in {item["id"] for item in flatten(notebook)}
    assert ids(section) == ["pb-clean"]


@pytest.mark.parametrize("page_level", ["0", "-1", "4", "not-an-integer"])
def test_expand_fails_closed_for_explicit_invalid_com_page_level(page_level):
    invalid_xml = PAGES_XML.replace('pageLevel="1"', f'pageLevel="{page_level}"', 1)

    with pytest.raises(ValueError, match="invalid Page indentation root"):
        HierarchyService(BrowsingBridge(pages_xml=invalid_xml)).expand_typed("s1", "section")


def test_query_page_matches_expand_parent_for_adjacent_l3():
    service = _gapped_service()
    queried = service.metadata_query(
        "page",
        {"mode": "start_node", "start_node_id": "sb"},
    )
    by_id = {item["id"]: item for item in queried["items"]}
    section = service.expand_typed("sb", "section")["tree"]
    expanded_l3 = {
        child["item"]["id"]: child["item"]
        for child in section["children"][0]["children"]
    }

    assert set(by_id) == {"pb", "pb-jump", "pb-jump-2"}
    assert by_id["pb"]["page_level"] == 1
    assert by_id["pb"]["parent_page_id"] is None
    for jump_id in ("pb-jump", "pb-jump-2"):
        assert by_id[jump_id]["page_level"] == 3
        assert by_id[jump_id]["parent_page_id"] == "pb"
        assert expanded_l3[jump_id]["parent_page_id"] == by_id[jump_id]["parent_page_id"]
        assert expanded_l3[jump_id]["page_level"] == by_id[jump_id]["page_level"]


def test_exact_expand_is_not_blocked_by_unrelated_notebook_blank_page_id():
    unrelated_failure = GAPPED_PAGES_XML.replace(
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
    gapped = service.expand_hierarchy("n2")["tree"]
    assert ids(gapped) == ["sb", "sb-clean"]
    assert ids(gapped["children"][0]) == ["pb"]
    assert ids(gapped["children"][0]["children"][0]) == ["pb-jump", "pb-jump-2"]


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        (
            [
                {
                    "id": "n",
                    "resource_type": "notebook",
                    "parent_id": None,
                },
                {
                    "id": "s",
                    "resource_type": "section",
                    "parent_id": "n",
                    "notebook_id": "n",
                    "section_ids": [],
                    "section_group_ids": [],
                },
                {
                    "id": "p-root",
                    "resource_type": "page",
                    "parent_id": "s",
                    "section_id": "s",
                    "notebook_id": "n",
                    "page_level": 2,
                    "order": 0,
                    "parent_page_id": None,
                },
            ],
            "invalid Page indentation root",
        ),
        (
            [
                {
                    "id": "n",
                    "resource_type": "notebook",
                    "parent_id": None,
                },
                {
                    "id": "s",
                    "resource_type": "section",
                    "parent_id": "n",
                    "notebook_id": "n",
                    "section_ids": [],
                    "section_group_ids": [],
                },
                {
                    "id": "p-root",
                    "resource_type": "page",
                    "parent_id": "s",
                    "section_id": "s",
                    "notebook_id": "n",
                    "page_level": 1,
                    "order": 0,
                    "parent_page_id": None,
                },
                {
                    "id": "p-out",
                    "resource_type": "page",
                    "parent_id": "s",
                    "section_id": "s",
                    "notebook_id": "n",
                    "page_level": 4,
                    "order": 1,
                    "parent_page_id": "p-root",
                },
            ],
            "invalid Page indentation root",
        ),
    ],
)
def test_page_indentation_still_fails_closed_for_unprojectable_levels(pages, message):
    with pytest.raises(ValueError, match=message):
        HierarchyService._validate_hierarchy_snapshot(pages)


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
