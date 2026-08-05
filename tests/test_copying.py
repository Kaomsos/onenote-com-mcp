import asyncio
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from local_onenote_mcp import server
from local_onenote_mcp.page import page_equivalence, transform_page_for_copy
from local_onenote_mcp.services import PartialFailure
from local_onenote_mcp.tools.copying import copy_page, plan_copy


def page_xml(page_id: str, title: str, body: str = "") -> str:
    outline = ""
    if body:
        outline = (
            '<one:Outline objectID="outline-id"><one:OEChildren><one:OE objectID="oe-id">'
            f"<one:T><![CDATA[{body}]]></one:T></one:OE></one:OEChildren></one:Outline>"
        )
    return (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
        f'ID="{page_id}" lastModifiedTime="clock"><one:Title><one:OE><one:T>{title}</one:T>'
        f"</one:OE></one:Title>{outline}</one:Page>"
    )


def hierarchy_items(modified: str = "m1") -> list[dict]:
    return [
        {
            "resource_type": "notebook",
            "id": "n",
            "name": "Notebook",
            "path": "Notebook",
            "parent_id": None,
            "modified": modified,
        },
        {
            "resource_type": "section",
            "id": "source-section",
            "name": "Source",
            "path": "Notebook/Source",
            "parent_id": "n",
            "notebook_id": "n",
            "modified": modified,
        },
        {
            "resource_type": "page",
            "id": "parent",
            "title": "Parent",
            "path": "Notebook/Source/Parent",
            "parent_id": "source-section",
            "notebook_id": "n",
            "section_id": "source-section",
            "parent_page_id": None,
            "page_level": 1,
            "order": 0,
            "modified": modified,
        },
        {
            "resource_type": "page",
            "id": "child",
            "title": "Child",
            "path": "Notebook/Source/Child",
            "parent_id": "source-section",
            "notebook_id": "n",
            "section_id": "source-section",
            "parent_page_id": "parent",
            "page_level": 2,
            "order": 1,
            "modified": modified,
        },
        {
            "resource_type": "page",
            "id": "sibling",
            "title": "Sibling",
            "path": "Notebook/Source/Sibling",
            "parent_id": "source-section",
            "notebook_id": "n",
            "section_id": "source-section",
            "parent_page_id": None,
            "page_level": 1,
            "order": 2,
            "modified": modified,
        },
        {
            "resource_type": "section",
            "id": "destination-section",
            "name": "Destination",
            "path": "Notebook/Destination",
            "parent_id": "n",
            "notebook_id": "n",
            "modified": modified,
        },
    ]


def install_plan_fakes(monkeypatch, *, body: str = "Body"):
    state = {"items": hierarchy_items(), "body": body}
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [dict(item) for item in state["items"]],
    )
    monkeypatch.setattr(
        server.services.pages,
        "xml",
        lambda page_id, page_info="basic": page_xml(
            page_id,
            next(item["title"] for item in state["items"] if item["id"] == page_id),
            state["body"],
        ),
    )
    return state


def install_recursive_execute_fakes(monkeypatch):
    state = [
        {
            "resource_type": "notebook",
            "id": "source-notebook",
            "name": "Source Notebook",
            "path": "Source Notebook",
            "parent_id": None,
        },
        {
            "resource_type": "section_group",
            "id": "source-group",
            "name": "Source Group",
            "path": "Source Notebook/Source Group",
            "parent_id": "source-notebook",
            "notebook_id": "source-notebook",
        },
        {
            "resource_type": "section_group",
            "id": "inner-group",
            "name": "Inner Group",
            "path": "Source Notebook/Source Group/Inner Group",
            "parent_id": "source-group",
            "notebook_id": "source-notebook",
        },
        {
            "resource_type": "section",
            "id": "source-section",
            "name": "Notes",
            "path": "Source Notebook/Source Group/Inner Group/Notes",
            "parent_id": "inner-group",
            "notebook_id": "source-notebook",
        },
        {
            "resource_type": "page",
            "id": "source-page",
            "title": "Page",
            "path": "Source Notebook/Source Group/Inner Group/Notes/Page",
            "parent_id": "source-section",
            "notebook_id": "source-notebook",
            "section_id": "source-section",
            "parent_page_id": None,
            "page_level": 1,
            "order": 0,
        },
        {
            "resource_type": "notebook",
            "id": "destination-notebook",
            "name": "Destination Notebook",
            "path": "Destination Notebook",
            "parent_id": None,
        },
    ]
    xml_store = {"source-page": page_xml("source-page", "Page")}
    counters = {"notebook": 0, "section_group": 0, "section": 0, "page": 0}

    def resources(include_recycle_bin=False):
        return state

    def parent_item(parent_id):
        return next(item for item in state if item["id"] == parent_id)

    def append_container(kind, parent_id, name):
        counters[kind] += 1
        item_id = f"new-{kind}-{counters[kind]}"
        parent = parent_item(parent_id)
        notebook_id = parent["id"] if parent["resource_type"] == "notebook" else parent["notebook_id"]
        item = {
            "resource_type": kind,
            "id": item_id,
            "name": name,
            "path": f"{parent['path']}/{name}",
            "parent_id": parent_id,
            "notebook_id": notebook_id,
        }
        state.append(item)
        return item

    def create_notebook(name, base_folder):
        counters["notebook"] += 1
        item = {
            "resource_type": "notebook",
            "id": f"new-notebook-{counters['notebook']}",
            "name": name,
            "path": str(Path(base_folder) / name),
            "parent_id": None,
        }
        state.append(item)
        return {"item": item, "path": str(Path(base_folder) / name)}

    def create_group(parent_id, name):
        return {"section_group": append_container("section_group", parent_id, name)}

    def create_section(parent_id, name):
        return {"section": append_container("section", parent_id, name)}

    def create_page(section_id, title, *args, **kwargs):
        counters["page"] += 1
        page_id = f"new-page-{counters['page']}"
        section = parent_item(section_id)
        item = {
            "resource_type": "page",
            "id": page_id,
            "title": title,
            "path": f"{section['path']}/{title}",
            "parent_id": section_id,
            "notebook_id": section["notebook_id"],
            "section_id": section_id,
            "parent_page_id": None,
            "page_level": 1,
            "order": sum(
                candidate["resource_type"] == "page" and candidate.get("section_id") == section_id
                for candidate in state
            ),
        }
        state.append(item)
        xml_store[page_id] = page_xml(page_id, title)
        return {"page": item}

    def call(operation, **params):
        if operation == "update_page_content":
            root = ET.fromstring(params["xml"])
            xml_store[root.attrib["ID"]] = params["xml"]
            return {"updated": True}
        if operation == "update_hierarchy":
            root = ET.fromstring(params["xml"])
            pages = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Page"]
            for order, node in enumerate(pages):
                item = parent_item(node.attrib["ID"])
                item["order"] = order
                item["page_level"] = int(node.attrib["pageLevel"])
            return {"updated": True}
        raise AssertionError(operation)

    monkeypatch.setattr(server.services.hierarchy, "resources", resources)
    monkeypatch.setattr(server.services.pages, "xml", lambda page_id, page_info="basic": xml_store[page_id])
    monkeypatch.setattr(server.services.mutations, "create_notebook", create_notebook)
    monkeypatch.setattr(server.services.mutations, "create_section_group", create_group)
    monkeypatch.setattr(server.services.mutations, "create_section", create_section)
    monkeypatch.setattr(server.services.mutations, "create_page", create_page)
    monkeypatch.setattr(server.services.copying, "call", call)
    return state


def test_page_copy_transform_strips_ids_rewrites_links_and_reports_unverified_content():
    source = page_xml(
        "{source-page}",
        "Title",
        '<a href="onenote:%7Bsource-page%7D">Body</a>',
    )
    source = source.replace(
        "</one:Outline>",
        '<one:Meta name="semantic" content="value" ID="semantic-style-id"/></one:Outline>',
    )

    result = transform_page_for_copy(
        source,
        "{target-page}",
        {"{source-page}": "{target-page}"},
    )

    assert 'ID="{target-page}"' in result["xml"]
    assert "source-page" not in result["xml"]
    assert "%7Btarget-page%7D" in result["xml"]
    assert "outline-id" not in result["xml"]
    assert "semantic-style-id" in result["xml"]
    assert any(issue["code"] == "content_type_unverified" for issue in result["issues"])
    assert not any(issue["code"] == "content_object_link_not_rewritable" for issue in result["issues"])
    assert result["lossless_candidate"] is False


def test_transform_rewrites_cross_page_internal_link_and_preserves_external_link():
    body = (
        '<a href="onenote:#child-page">Child</a> '
        '<a href="onenote:#outside-page">Outside</a>'
    )
    source = page_xml("parent-page", "Title", body)

    result = transform_page_for_copy(
        source,
        "new-parent",
        {"parent-page": "new-parent", "child-page": "new-child"},
    )

    assert "#new-child" in result["xml"]
    assert "#child-page" not in result["xml"]
    assert "#outside-page" in result["xml"]
    assert not any(issue["code"] == "internal_link_not_rewritten" for issue in result["issues"])


def test_transform_does_not_rewrite_plain_metadata_values_that_match_source_ids():
    source = page_xml("parent-page", "Title", "Body").replace(
        "</one:Outline>",
        '<one:Meta name="plain" content="child-page"/></one:Outline>',
    )

    result = transform_page_for_copy(
        source,
        "new-parent",
        {"parent-page": "new-parent", "child-page": "new-child"},
    )

    assert 'content="child-page"' in result["xml"]
    assert 'content="new-child"' not in result["xml"]
    assert not any(issue["code"] == "internal_link_not_rewritten" for issue in result["issues"])


def test_transform_rewrites_recognized_page_id_reference_attribute():
    source = page_xml("parent-page", "Title", "Body").replace(
        "</one:Outline>",
        '<one:Meta name="link" pageID="child-page"/></one:Outline>',
    )

    result = transform_page_for_copy(
        source,
        "new-parent",
        {"parent-page": "new-parent", "child-page": "new-child"},
    )

    assert 'pageID="new-child"' in result["xml"]
    assert 'pageID="child-page"' not in result["xml"]


def test_transform_preserves_stable_page_attributes_settings_and_escapes_title_once():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote"
      ID="source" name="Old" pageLevel="2" lang="zh-CN" dateTime="old">
      <one:PageSettings RTL="false"><one:PageSize><one:Automatic/></one:PageSize>
      <one:RuleLines visible="false"/></one:PageSettings>
      <one:Title><one:OE><one:T>Old</one:T></one:OE></one:Title></one:Page>"""

    result = transform_page_for_copy(source, "target", {"source": "target"}, title="A & B < C")
    root = ET.fromstring(result["xml"])

    assert root.attrib == {"ID": "target", "lang": "zh-CN"}
    assert any(node.tag.rsplit("}", 1)[-1] == "PageSettings" for node in root)
    title = next(node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "T")
    assert title.text == "A & B < C"
    assert not any(issue["code"] == "unsupported_page_root" for issue in result["issues"])


def test_transform_omits_whole_content_block_with_unknown_nested_node():
    source = page_xml("source", "Title", "Body").replace(
        "</one:OEChildren></one:Outline>",
        (
            "<one:FutureWidget><one:T>must not leak</one:T></one:FutureWidget>"
            "</one:OEChildren></one:Outline>"
        ),
    )

    result = transform_page_for_copy(source, "target", {"source": "target"})

    assert "FutureWidget" not in result["xml"]
    assert "must not leak" not in result["xml"]
    issue = next(issue for issue in result["issues"] if issue["code"] == "unsupported_nested_page_node")
    assert issue["content_type"] == "Outline"
    assert issue["unknown_nodes"] == ["{http://schemas.microsoft.com/office/onenote/2013/onenote}FutureWidget"]
    assert issue["action"] == "omitted"


def test_transform_builds_safe_title_when_original_title_block_is_unsupported():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:Title><one:OE><one:FutureTitle/><one:T>Old</one:T></one:OE></one:Title></one:Page>"""

    result = transform_page_for_copy(source, "target", {"source": "target"}, title="Safe title")
    root = ET.fromstring(result["xml"])
    title_text = next(node.text for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "T")

    assert title_text == "Safe title"
    assert "FutureTitle" not in result["xml"]
    assert any(issue["code"] == "unsupported_nested_page_node" for issue in result["issues"])


def test_page_equivalence_ignores_generated_ids_and_clocks():
    first = page_xml("one", "Title")
    first = first.replace('ID="one"', 'ID="one" name="Old" pageLevel="2"')
    second = page_xml("two", "Title").replace(
        'ID="two"', 'ID="two" name="New" pageLevel="1"'
    ).replace('lastModifiedTime="clock"', 'lastModifiedTime="later"')

    result = page_equivalence(first, second)

    assert result["equivalent"] is True
    assert all(result["checks"].values())


def test_page_equivalence_accepts_new_generated_content_object_ids():
    expected = page_xml("one", "Title", "Body").replace(' objectID="outline-id"', "").replace(
        ' objectID="oe-id"', ""
    )
    actual = page_xml("two", "Title", "Body").replace("outline-id", "new-outline").replace(
        "oe-id", "new-oe"
    )

    result = page_equivalence(expected, actual)

    assert result["equivalent"] is True
    assert result["checks"]["content_objects"] is True


def test_transform_omits_unknown_root_and_binary_mismatch_is_detected():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>
    <one:Image format="png"><one:Data>YWJj</one:Data></one:Image>
    <one:FutureWidget><one:Data>eHl6</one:Data></one:FutureWidget>
    </one:Page>"""
    transformed = transform_page_for_copy(
        source,
        "target",
        {"p": "target"},
        validated_content_types={"Image"},
    )

    assert "FutureWidget" not in transformed["xml"]
    assert any(issue["code"] == "unsupported_page_root" for issue in transformed["issues"])
    changed = transformed["xml"].replace("YWJj", "ZGVm")
    equivalence = page_equivalence(transformed["xml"], changed)
    assert equivalence["equivalent"] is False
    assert equivalence["checks"]["binary_sha256"] is False


def test_binary_equivalence_hashes_decoded_bytes_and_ignores_line_wrapping():
    compact = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Image format="png"><one:Data>YWJjZA==</one:Data></one:Image></one:Page>"""
    wrapped = compact.replace("YWJjZA==", "YWJj\n  ZA==")

    result = page_equivalence(compact, wrapped)

    assert result["checks"]["binary_sha256"] is True
    assert result["checks"]["canonical_xml"] is True


def test_transform_reports_semantic_rich_text_table_and_list_capabilities():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>
    <one:Outline objectID="outline"><one:OEChildren>
      <one:OE objectID="rich"><one:T><![CDATA[<strong>Rich</strong>]]></one:T></one:OE>
      <one:OE><one:List><one:Bullet bullet="2"/></one:List><one:T>Item</one:T></one:OE>
      <one:Table><one:Columns><one:Column index="0" width="100"/></one:Columns>
        <one:Row><one:Cell><one:OEChildren><one:OE><one:T>Cell</one:T></one:OE></one:OEChildren></one:Cell></one:Row>
      </one:Table>
    </one:OEChildren></one:Outline></one:Page>"""

    result = transform_page_for_copy(source, "target", {"p": "target"})

    assert result["content_types"] == ["List", "Outline", "RichText", "Table"]
    unverified = {
        issue["content_type"]
        for issue in result["issues"]
        if issue["code"] == "content_type_unverified"
    }
    assert unverified == {"List", "Outline", "RichText", "Table"}


def test_plan_copy_is_stable_and_includes_complete_page_subtree(monkeypatch):
    install_plan_fakes(monkeypatch)

    first = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))
    second = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))

    assert first["ok"] is True
    assert first["plan_digest"] == second["plan_digest"]
    assert first["source_snapshot_digest"] == second["source_snapshot_digest"]
    assert [item["id"] for item in first["snapshots"]["source"]["resources"]] == [
        "parent",
        "child",
    ]
    assert set(first["snapshots"]["source"]["page_hashes"]) == {"parent", "child"}
    assert first["snapshots"]["destination"] == first["destination"]
    assert first["estimated"]["pages"] == 2
    assert first["execute_tool"] == "copy_page"
    assert first["copyability"]["lossless_candidate"] is False


def test_plan_copy_rejects_case_insensitive_direct_name_conflict(monkeypatch):
    install_plan_fakes(monkeypatch)

    result = asyncio.run(plan_copy("parent", "source-section", "parent"))

    assert result["ok"] is False
    assert "never overwrites" in result["error"]


def test_non_notebook_plan_rejects_destination_base_folder(monkeypatch, tmp_path):
    install_plan_fakes(monkeypatch)

    result = asyncio.run(
        plan_copy(
            "parent",
            "destination-section",
            "Copied Parent",
            str(tmp_path),
        )
    )

    assert result["ok"] is False
    assert "only valid for Notebook Copy" in result["error"]


def test_notebook_copy_conflict_is_scoped_to_target_folder(monkeypatch, tmp_path):
    state = install_recursive_execute_fakes(monkeypatch)
    state.append(
        {
            "resource_type": "notebook",
            "id": "same-name-elsewhere",
            "name": "Notebook Copy",
            "path": "Unrelated/Notebook Copy",
            "parent_id": None,
        }
    )

    plan = server.services.copying._build_plan(
        "source-notebook",
        destination_name="Notebook Copy",
        destination_base_folder=str(tmp_path),
    )

    assert plan["destination"]["target_path"] == str(tmp_path / "Notebook Copy")


def test_section_group_source_selection_is_recursive_but_excludes_siblings():
    items = hierarchy_items()
    group = {
        "resource_type": "section_group",
        "id": "g",
        "name": "Group",
        "path": "Notebook/Group",
        "parent_id": "n",
        "notebook_id": "n",
    }
    section = {
        "resource_type": "section",
        "id": "gs",
        "name": "Grouped",
        "path": "Notebook/Group/Grouped",
        "parent_id": "g",
        "notebook_id": "n",
    }
    page = {
        "resource_type": "page",
        "id": "gp",
        "title": "Grouped Page",
        "path": "Notebook/Group/Grouped/Grouped Page",
        "parent_id": "gs",
        "notebook_id": "n",
        "section_id": "gs",
        "page_level": 1,
        "order": 0,
    }
    source, selected = server.services.copying._source_resources("g", [*items, group, section, page])

    assert source["id"] == "g"
    assert [item["id"] for item in selected] == ["g", "gs", "gp"]


def test_container_source_selection_normalizes_parent_before_child():
    items = hierarchy_items()
    group = {
        "resource_type": "section_group",
        "id": "g",
        "name": "Group",
        "path": "Notebook/Group",
        "parent_id": "n",
        "notebook_id": "n",
    }
    section = {
        "resource_type": "section",
        "id": "gs",
        "name": "Grouped",
        "path": "Notebook/Group/Grouped",
        "parent_id": "g",
        "notebook_id": "n",
    }
    page = {
        "resource_type": "page",
        "id": "gp",
        "title": "Grouped Page",
        "path": "Notebook/Group/Grouped/Grouped Page",
        "parent_id": "gs",
        "notebook_id": "n",
        "section_id": "gs",
        "page_level": 1,
        "order": 0,
    }

    _, selected = server.services.copying._source_resources("g", [*items, page, section, group])

    assert [item["id"] for item in selected] == ["g", "gs", "gp"]


def test_section_group_plan_rejects_destination_inside_source_tree(monkeypatch):
    items = hierarchy_items()
    group = {
        "resource_type": "section_group",
        "id": "g",
        "name": "Group",
        "path": "Notebook/Group",
        "parent_id": "n",
        "notebook_id": "n",
    }
    child_group = {
        "resource_type": "section_group",
        "id": "child-g",
        "name": "Child Group",
        "path": "Notebook/Group/Child Group",
        "parent_id": "g",
        "notebook_id": "n",
    }
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [*items, group, child_group],
    )

    result = asyncio.run(plan_copy("g", "child-g", "Nested Copy"))

    assert result["ok"] is False
    assert "cannot be copied into itself" in result["error"]


def test_plan_budget_rejects_subtree_before_reading_page_xml(monkeypatch):
    state = install_plan_fakes(monkeypatch)
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_COPY_PAGES", "1")
    monkeypatch.setattr(
        server.services.pages,
        "xml",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Page XML must not be read")),
    )

    result = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))

    assert state["items"]
    assert result["ok"] is False
    assert "2 pages" in result["error"]


@pytest.mark.write_contract
def test_copy_rejects_stale_plan_before_create(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="Before")
    planned = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setattr(server.services.pages, "confirm", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        server.services.mutations,
        "create_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No mutation is allowed")),
    )
    state["body"] = "After"

    result = asyncio.run(
        copy_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            planned["plan_digest"],
            destination_title="Copied Parent",
        )
    )

    assert result["ok"] is False
    assert "stale" in result["error"]


@pytest.mark.write_contract
def test_copy_rejects_changed_destination_snapshot_before_create(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    planned = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setattr(server.services.pages, "confirm", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        server.services.mutations,
        "create_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No mutation is allowed")),
    )
    state["items"].append(
        {
            "resource_type": "page",
            "id": "new-destination-child",
            "title": "Another Page",
            "parent_id": "destination-section",
            "notebook_id": "n",
            "section_id": "destination-section",
            "parent_page_id": None,
            "page_level": 1,
            "order": 0,
        }
    )

    result = asyncio.run(
        copy_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            planned["plan_digest"],
            destination_title="Copied Parent",
        )
    )

    assert result["ok"] is False
    assert "stale" in result["error"]


def test_partial_create_reports_created_ids_without_rollback(monkeypatch):
    install_plan_fakes(monkeypatch)
    plan = server.services.copying._build_plan(
        "parent", "destination-section", "Copied Parent"
    )
    calls = []

    def create(section_id, title, *args, **kwargs):
        calls.append(title)
        if len(calls) == 2:
            raise PartialFailure(
                "second create failed after allocation",
                partial=True,
                created_ids=["new-child"],
                completed_steps=[{"operation": "create_new_page", "object_id": "new-child"}],
                failed_step="initialize_created_page",
            )
        return {
            "page": {
                "resource_type": "page",
                "id": "new-parent",
                "title": title,
                "section_id": section_id,
                "parent_id": section_id,
                "page_level": 1,
                "order": 0,
            }
        }

    monkeypatch.setattr(server.services.mutations, "create_page", create)

    with pytest.raises(PartialFailure) as caught:
        server.services.copying._execute_copy(plan)

    assert caught.value.details["source_untouched"] is True
    assert caught.value.details["created_ids"] == ["new-parent", "new-child"]
    assert caught.value.details["id_map"] == {"parent": "new-parent"}
    assert caught.value.details["failed_step"] == "initialize_created_page"


def test_execute_budget_is_checked_before_first_mutation(monkeypatch):
    install_plan_fakes(monkeypatch, body="")
    plan = server.services.copying._build_plan("parent", "destination-section", "Copy")
    monkeypatch.setattr(
        "local_onenote_mcp.services.copying.CopyBudget.current",
        lambda: SimpleNamespace(max_execute_seconds=-1),
    )
    monkeypatch.setattr(
        server.services.mutations,
        "create_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No mutation is allowed")),
    )

    with pytest.raises(RuntimeError, match="execution exceeded"):
        server.services.copying._execute_copy(plan)


@pytest.mark.write_contract
def test_recursive_section_group_copy_executes_depth_first_and_verifies(monkeypatch):
    install_recursive_execute_fakes(monkeypatch)
    plan = server.services.copying._build_plan(
        "source-group", "destination-notebook", "Group Copy"
    )

    result = server.services.copying._execute_copy(plan)

    assert list(result["copy_report"]["id_map"]) == [
        "source-group",
        "inner-group",
        "source-section",
        "source-page",
    ]
    assert result["copy_report"]["verified"] is True
    assert result["copy_report"]["lossless"] is True
    assert result["copy_report"]["copied_counts"] == {"resources": 4, "pages": 1}


@pytest.mark.write_contract
def test_recursive_section_copy_executes_and_verifies(monkeypatch):
    install_recursive_execute_fakes(monkeypatch)
    plan = server.services.copying._build_plan(
        "source-section", "destination-notebook", "Section Copy"
    )

    result = server.services.copying._execute_copy(plan)

    assert list(result["copy_report"]["id_map"]) == ["source-section", "source-page"]
    assert result["item"]["name"] == "Section Copy"
    assert result["copy_report"]["verified"] is True
    assert result["copy_report"]["lossless"] is True


@pytest.mark.write_contract
def test_recursive_notebook_copy_creates_new_root_and_verifies(monkeypatch, tmp_path):
    install_recursive_execute_fakes(monkeypatch)
    plan = server.services.copying._build_plan(
        "source-notebook",
        destination_name="Notebook Copy",
        destination_base_folder=str(tmp_path),
    )

    result = server.services.copying._execute_copy(plan)

    assert list(result["copy_report"]["id_map"]) == [
        "source-notebook",
        "source-group",
        "inner-group",
        "source-section",
        "source-page",
    ]
    assert result["item"]["path"] == str(tmp_path / "Notebook Copy")
    assert result["destination_path"] == str(tmp_path / "Notebook Copy")
    assert result["copy_report"]["destination_path"] == str(tmp_path / "Notebook Copy")
    assert result["copy_report"]["verified"] is True
    assert result["copy_report"]["lossless"] is True


@pytest.mark.write_contract
def test_notebook_copy_path_mismatch_is_partial_and_reports_allocated_id(monkeypatch, tmp_path):
    install_recursive_execute_fakes(monkeypatch)
    plan = server.services.copying._build_plan(
        "source-notebook",
        destination_name="Notebook Copy",
        destination_base_folder=str(tmp_path),
    )
    original_create = server.services.mutations.create_notebook

    def create_with_wrong_path(name, base_folder):
        result = original_create(name, base_folder)
        result["path"] = str(tmp_path / "wrong-place")
        return result

    monkeypatch.setattr(server.services.mutations, "create_notebook", create_with_wrong_path)

    with pytest.raises(PartialFailure) as caught:
        server.services.copying._execute_copy(plan)

    assert caught.value.details["created_ids"] == ["new-notebook-1"]
    assert caught.value.details["failed_step"] == "create_resources"


@pytest.mark.write_contract
def test_page_subtree_copy_creates_new_ids_restores_relative_levels_and_verifies(monkeypatch):
    state = hierarchy_items()
    xml_store = {
        "parent": page_xml("parent", "Parent"),
        "child": page_xml("child", "Child"),
    }
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: state,
    )
    monkeypatch.setattr(
        server.services.pages,
        "xml",
        lambda page_id, page_info="basic": xml_store[page_id],
    )
    plan = server.services.copying._build_plan(
        "parent", "destination-section", "Copied Parent"
    )
    created = []

    def create_page(section_id, title, *args, **kwargs):
        page_id = f"new-{len(created) + 1}"
        item = {
            "resource_type": "page",
            "id": page_id,
            "title": title,
            "path": f"Notebook/Destination/{title}",
            "parent_id": section_id,
            "notebook_id": "n",
            "section_id": section_id,
            "parent_page_id": None,
            "page_level": 1,
            "order": len([item for item in state if item.get("section_id") == section_id]),
            "modified": "new",
        }
        state.append(item)
        created.append(item)
        xml_store[page_id] = page_xml(page_id, title)
        return {"page": item}

    def fake_call(operation, **params):
        if operation == "update_page_content":
            root = ET.fromstring(params["xml"])
            xml_store[root.attrib["ID"]] = params["xml"]
            return {"updated": True}
        if operation == "update_hierarchy":
            root = ET.fromstring(params["xml"])
            pages = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Page"]
            for order, node in enumerate(pages):
                item = next(value for value in state if value["id"] == node.attrib["ID"])
                item["order"] = order
                item["page_level"] = int(node.attrib["pageLevel"])
            return {"updated": True}
        raise AssertionError(operation)

    monkeypatch.setattr(server.services.mutations, "create_page", create_page)
    monkeypatch.setattr(server.services.copying, "call", fake_call)

    result = server.services.copying._execute_copy(plan)

    assert result["copy_report"]["verified"] is True
    assert result["copy_report"]["lossless"] is True
    assert result["copy_report"]["id_map"] == {"parent": "new-1", "child": "new-2"}
    assert created[0]["page_level"] == 1
    assert created[1]["page_level"] == 2


@pytest.mark.write_contract
def test_reconstructive_move_degrades_to_copy_when_fidelity_is_unverified(monkeypatch):
    install_plan_fakes(monkeypatch)
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_reconstructive_move_page(
        "parent", "destination-section", "Moved Parent"
    )
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda value: {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent"],
            "copy_report": {"lossless": False, "verified": True, "id_map": {"parent": "new-parent"}},
            "warnings": ["unverified"],
        },
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.copying.reconstructive_move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            plan["plan_digest"],
            destination_title="Moved Parent",
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["source_deleted"] is False


@pytest.mark.write_contract
def test_reconstructive_move_normalizes_copy_readback_failure_to_copy_only(monkeypatch):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_reconstructive_move_page(
        "parent", "destination-section", "Moved Parent"
    )
    report = {
        "lossless": False,
        "verified": False,
        "id_map": {"parent": "new-parent"},
    }
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda value: (_ for _ in ()).throw(
            PartialFailure(
                "readback failed",
                partial=True,
                outcome="copy_unverified",
                source_untouched=True,
                source_deleted=False,
                copy_report=report,
                created_ids=["new-parent"],
                failed_step="verify_copy",
            )
        ),
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.copying.reconstructive_move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            plan["plan_digest"],
            destination_title="Moved Parent",
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["copy_report"] == report
    assert caught.value.details["created_ids"] == ["new-parent"]


@pytest.mark.write_contract
def test_reconstructive_move_reports_copy_only_when_source_revalidation_fails(monkeypatch):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_reconstructive_move_page(
        "parent", "destination-section", "Moved Parent"
    )
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda value: {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent"],
            "copy_report": {
                "lossless": True,
                "verified": True,
                "id_map": {"parent": "new-parent"},
            },
            "warnings": [],
        },
    )
    original_capture = server.services.copying._capture_source
    captures = 0

    def capture_then_fail(*args, **kwargs):
        nonlocal captures
        captures += 1
        if captures == 1:
            return original_capture(*args, **kwargs)
        raise ValueError("source vanished")

    monkeypatch.setattr(server.services.copying, "_capture_source", capture_then_fail)
    monkeypatch.setattr(
        server.services.mutations,
        "delete_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("delete is blocked")),
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.copying.reconstructive_move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            plan["plan_digest"],
            destination_title="Moved Parent",
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["created_ids"] == ["new-parent"]
    assert caught.value.details["source_revalidation_error"] == "source vanished"


@pytest.mark.write_contract
def test_reconstructive_move_blocks_delete_when_source_changes_after_copy(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_reconstructive_move_page(
        "parent", "destination-section", "Moved Parent"
    )

    def copy_then_change_source(value):
        state["body"] = "changed after copy"
        return {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent"],
            "copy_report": {
                "lossless": True,
                "verified": True,
                "id_map": {"parent": "new-parent", "child": "new-child"},
            },
            "warnings": [],
        }

    monkeypatch.setattr(server.services.copying, "_execute_copy", copy_then_change_source)
    monkeypatch.setattr(
        server.services.mutations,
        "delete_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("delete is blocked")),
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.copying.reconstructive_move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            plan["plan_digest"],
            destination_title="Moved Parent",
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["created_ids"] == ["new-parent"]


@pytest.mark.write_contract
def test_reconstructive_move_recycles_source_pages_leaf_to_root(monkeypatch):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_reconstructive_move_page(
        "parent", "destination-section", "Moved Parent"
    )
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda value: {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent", "new-child"],
            "copy_report": {
                "lossless": True,
                "verified": True,
                "id_map": {"parent": "new-parent", "child": "new-child"},
            },
            "warnings": [],
        },
    )
    deleted = []

    def delete_page(page_id, expected_title, expected_section_id, expected_modified, permanently):
        assert permanently is False
        deleted.append(page_id)
        return {"deleted": True, "final_state": {"id": page_id, "is_in_recycle_bin": True}}

    monkeypatch.setattr(server.services.mutations, "delete_page", delete_page)

    result = server.services.copying.reconstructive_move_page(
        "parent",
        "destination-section",
        "Parent",
        "source-section",
        plan["plan_digest"],
        destination_title="Moved Parent",
    )

    assert deleted == ["child", "parent"]
    assert result["source_deleted_to_recycle_bin"] is True
    assert result["outcome"] == "moved"


@pytest.mark.write_contract
def test_reconstructive_move_reports_verified_and_remaining_ids_on_delete_failure(monkeypatch):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_reconstructive_move_page(
        "parent", "destination-section", "Moved Parent"
    )
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda value: {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent", "new-child"],
            "copy_report": {
                "lossless": True,
                "verified": True,
                "id_map": {"parent": "new-parent", "child": "new-child"},
            },
            "warnings": [],
        },
    )

    def delete_page(page_id, *args, **kwargs):
        if page_id == "parent":
            raise RuntimeError("parent delete failed")
        return {"deleted": True, "final_state": {"id": page_id, "is_in_recycle_bin": True}}

    monkeypatch.setattr(server.services.mutations, "delete_page", delete_page)

    with pytest.raises(PartialFailure) as caught:
        server.services.copying.reconstructive_move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            plan["plan_digest"],
            destination_title="Moved Parent",
        )

    assert caught.value.details["outcome"] == "source_partially_recycled"
    assert caught.value.details["attempted_source_ids"] == ["child", "parent"]
    assert caught.value.details["recycled_source_ids"] == ["child"]
    assert caught.value.details["remaining_source_ids"] == ["parent"]
    assert caught.value.details["unverified_source_ids"] == []
    assert caught.value.details["created_ids"] == ["new-parent", "new-child"]


@pytest.mark.write_contract
def test_reconstructive_move_does_not_claim_success_without_recycle_bin_evidence(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    state["items"] = [item for item in state["items"] if item["id"] != "child"]
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_reconstructive_move_page(
        "parent", "destination-section", "Moved Parent"
    )
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda value: {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent"],
            "copy_report": {
                "lossless": True,
                "verified": True,
                "id_map": {"parent": "new-parent"},
            },
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        server.services.mutations,
        "delete_page",
        lambda *args, **kwargs: {"deleted": True, "final_state": None},
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.copying.reconstructive_move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            plan["plan_digest"],
            destination_title="Moved Parent",
        )

    assert caught.value.details["outcome"] == "source_recycle_unverified"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["attempted_source_ids"] == ["parent"]
    assert caught.value.details["deleted_source_ids"] == []
    assert caught.value.details["unverified_source_ids"] == ["parent"]
    assert caught.value.details["remaining_source_ids"] == []
