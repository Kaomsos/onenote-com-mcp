import pytest

from local_onenote_mcp.hierarchy import parse_hierarchy, resolve_resource


FULL_XML = """<one:Notebooks xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
  <one:Notebook name="NB" ID="n"><one:Section name="Sec" ID="s">
    <one:Page name="One" ID="p1" pageLevel="1" />
    <one:Page name="Two" ID="p2" pageLevel="1" />
  </one:Section></one:Notebook>
</one:Notebooks>"""


def test_search_fragment_is_hydrated_from_complete_catalog():
    catalog = parse_hierarchy(FULL_XML)
    fragment = """<one:Pages xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
      <one:Page name="stale title" ID="p2" unknown="not-public" />
    </one:Pages>"""

    result = parse_hierarchy(fragment, catalog=catalog)

    assert result == [next(item for item in catalog if item["id"] == "p2")]
    assert result[0]["title"] == "Two"
    assert result[0]["section_id"] == "s"
    assert result[0]["path"] == "NB/Sec/Two"
    assert "unknown" not in result[0]


def test_resolve_resource_supports_page_title_without_name_alias():
    page = resolve_resource(parse_hierarchy(FULL_XML), "Two", "page")
    assert page["id"] == "p2"
    assert "name" not in page


def test_resolve_resource_rejects_ambiguous_typed_names():
    xml = """<one:Notebooks xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
      <one:Notebook name="A" ID="a"><one:Section name="Same" ID="s1" /></one:Notebook>
      <one:Notebook name="B" ID="b"><one:Section name="Same" ID="s2" /></one:Notebook>
    </one:Notebooks>"""

    with pytest.raises(ValueError, match="Ambiguous section"):
        resolve_resource(parse_hierarchy(xml), "Same", "section")


def test_resolve_resource_prefers_exact_path_before_same_display_name():
    xml = """<one:Notebooks xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
      <one:Notebook name="Notebook" ID="notebook-id" />
      <one:Notebook name="Projects" ID="projects-id"><one:Section name="People" ID="section-id">
        <one:Page name="notebook" ID="page-id" />
      </one:Section></one:Notebook>
    </one:Notebooks>"""

    item = resolve_resource(parse_hierarchy(xml), "Notebook")

    assert item["resource_type"] == "notebook"
    assert item["id"] == "notebook-id"
