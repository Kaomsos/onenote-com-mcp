import asyncio
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from local_onenote_mcp import server
from local_onenote_mcp.page import (
    canonical_page_digest,
    copy_verification_tier,
    page_content_capability_projection,
    page_equivalence,
    transform_page_for_copy,
)
from local_onenote_mcp.services import PartialFailure
from local_onenote_mcp.services.pages import stable_page_content_digest
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


def test_canonical_page_digest_ignores_only_empty_selection_text_placeholders():
    baseline = page_xml("page", "Title")
    selected_placeholder = baseline.replace(
        "</one:Title>",
        '<one:T selected="all" /></one:Title>',
    )
    ordinary_empty_text = baseline.replace("</one:Title>", "<one:T /></one:Title>")
    selected_visible_text = baseline.replace(
        "</one:Title>",
        '<one:T selected="all">visible</one:T></one:Title>',
    )

    assert canonical_page_digest(baseline) == canonical_page_digest(
        selected_placeholder
    )
    assert canonical_page_digest(baseline) != canonical_page_digest(
        ordinary_empty_text
    )
    assert canonical_page_digest(baseline) != canonical_page_digest(
        selected_visible_text
    )


def test_stable_page_content_digest_ignores_promotion_metadata_but_not_body():
    before = page_xml("page", "Title", "Body").replace(
        'lastModifiedTime="clock"',
        'lastModifiedTime="before" pageLevel="2" isCurrentlyViewed="true"',
    )
    promoted = before.replace(
        'lastModifiedTime="before" pageLevel="2" isCurrentlyViewed="true"',
        'lastModifiedTime="after" pageLevel="1" isCurrentlyViewed="false"',
    )
    changed_body = promoted.replace("Body", "Changed body")

    assert stable_page_content_digest(before) == stable_page_content_digest(promoted)
    assert stable_page_content_digest(before) != stable_page_content_digest(changed_body)


def test_page_content_capability_projection_is_content_free_and_kind_counted():
    source = page_xml("page", "Secret title", "Sensitive body").replace(
        "</one:Page>",
        '<one:InsertedFile objectID="inserted-id" path="C:/private/file.txt"/>'
        "</one:Page>",
    )

    projection = page_content_capability_projection(source)

    assert projection == {
        "schema_version": 1,
        "capabilities": ["InsertedFile", "Outline"],
        "object_kind_counts": {"InsertedFile": 1, "OE": 1, "Outline": 1},
        "unknown_nodes": [],
        "unsupported_page_roots": [],
        "complete": True,
    }
    assert "Sensitive" not in str(projection)
    assert "private" not in str(projection)
    assert "inserted-id" not in str(projection)


def test_page_content_capability_projection_fails_closed_on_unknown_nested_node():
    source = page_xml("page", "Title", "Body").replace(
        "</one:Outline>", "<one:FutureThing/></one:Outline>"
    )

    projection = page_content_capability_projection(source)

    assert projection["complete"] is False
    assert projection["unknown_nodes"] == [
        "{http://schemas.microsoft.com/office/onenote/2013/onenote}FutureThing"
    ]


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


def install_recursive_execute_fakes(monkeypatch, *, duplicate_page_titles: bool = False):
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
    xml_store = {"source-page": page_xml("source-page", "Page", "first body")}
    if duplicate_page_titles:
        duplicate = {
            "resource_type": "page",
            "id": "source-page-2",
            "title": "Page",
            "path": "Source Notebook/Source Group/Inner Group/Notes/Page",
            "parent_id": "source-section",
            "notebook_id": "source-notebook",
            "section_id": "source-section",
            "parent_page_id": None,
            "page_level": 1,
            "order": 1,
        }
        state.insert(-1, duplicate)
        xml_store["source-page-2"] = page_xml(
            "source-page-2", "Page", "second body"
        )
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
        return {
            "item": item,
            "path": str(Path(base_folder) / name),
            "allocated_id": item["id"],
        }

    def create_group(parent_id, name):
        item = append_container("section_group", parent_id, name)
        return {"section_group": item, "allocated_id": item["id"]}

    def create_section(parent_id, name):
        item = append_container("section", parent_id, name)
        return {"section": item, "allocated_id": item["id"]}

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
        return {"page": item, "allocated_id": item["id"]}

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


def test_page_copy_transform_strips_ids_rewrites_links_and_accepts_validated_content():
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
    assert not any(issue["code"] == "content_type_unverified" for issue in result["issues"])
    assert not any(issue["code"] == "content_object_link_not_rewritable" for issue in result["issues"])
    assert result["lossless_candidate"] is True


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


def test_transform_removes_empty_selection_marker_before_replacing_title():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote"
      ID="source" name="Old">
      <one:Title><one:OE>
        <one:T selected="all"/>
        <one:T>Old</one:T>
      </one:OE></one:Title>
      <one:Outline><one:OEChildren><one:OE>
        <one:T selected="all">Visible selected body</one:T>
        <one:T/>
      </one:OE></one:OEChildren></one:Outline>
    </one:Page>"""

    result = transform_page_for_copy(
        source,
        "target",
        {"source": "target"},
        title="New",
    )
    root = ET.fromstring(result["xml"])
    title = next(node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Title")
    title_texts = [
        node.text or ""
        for node in title.iter()
        if node.tag.rsplit("}", 1)[-1] == "T"
    ]
    all_texts = [
        node.text or ""
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] == "T"
    ]

    assert title_texts == ["New"]
    assert "Old" not in all_texts
    assert "Visible selected body" in all_texts
    assert "" in all_texts


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
    first = page_xml("one", "Title").replace(
        "<one:OE>",
        '<one:OE author="Original" authorInitials="OR" creationTime="before" '
        'lastModifiedBy="Original" lastModifiedByInitials="OR">',
        1,
    )
    first = first.replace('ID="one"', 'ID="one" name="Old" pageLevel="2"')
    second = (
        page_xml("two", "Title")
        .replace(
            "<one:OE>",
            '<one:OE author="Copy" authorInitials="CP" creationTime="after" '
            'lastModifiedBy="Copy" lastModifiedByInitials="CP">',
            1,
        )
        .replace('ID="two"', 'ID="two" name="New" pageLevel="1"')
        .replace('lastModifiedTime="clock"', 'lastModifiedTime="later"')
    )

    result = page_equivalence(first, second)

    assert result["equivalent"] is True
    assert all(result["checks"].values())


def test_transform_drops_one_note_generated_authorship_metadata():
    source = page_xml("source", "Title", "Body").replace(
        '<one:Outline objectID="outline-id">',
        '<one:Outline objectID="outline-id" author="Original" authorInitials="OR" '
        'authorResolutionID="author-id" creationTime="before" '
        'lastModifiedBy="Original" lastModifiedByInitials="OR" '
        'lastModifiedByResolutionID="modifier-id">',
    )

    result = transform_page_for_copy(source, "target", {"source": "target"})

    for attribute in (
        "author",
        "authorInitials",
        "authorResolutionID",
        "creationTime",
        "lastModifiedBy",
        "lastModifiedByInitials",
        "lastModifiedByResolutionID",
    ):
        assert f"{attribute}=" not in result["xml"]


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


def test_semantic_list_tag_equivalence_ignores_com_reserialization():
    expected = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:TagDef index="0" type="0" symbol="3" name="To Do"/>
    <one:Outline><one:OEChildren>
      <one:OE><one:List><one:Number numberSequence="0"/></one:List><one:Tag index="0" completed="true" disabled="false"/><one:T>为</one:T></one:OE>
      <one:OE><one:List><one:Number numberSequence="0"/></one:List><one:Tag index="0" completed="false" disabled="false"/><one:T>答复</one:T></one:OE>
      <one:OE><one:List><one:Number numberSequence="0"/></one:List><one:Tag index="0" completed="true" disabled="false"/><one:T>3发送</one:T></one:OE>
    </one:OEChildren></one:Outline></one:Page>"""
    actual = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="target">
    <one:TagDef index="6" type="0" symbol="3" name="待办事项" creationTime="later"/>
    <one:Outline objectID="generated-a"><one:OEChildren>
      <one:OE objectID="generated-1"><one:List><one:Number numberSequence="4"/></one:List><one:Tag index="6" completed="true" disabled="false" creationTime="later"/><one:T>为</one:T></one:OE>
    </one:OEChildren></one:Outline>
    <one:Outline objectID="generated-b"><one:OEChildren>
      <one:OE><one:List><one:Number numberSequence="4"/></one:List><one:Tag index="6" completed="false" disabled="false"/><one:T>答复</one:T></one:OE>
      <one:OE><one:List><one:Number numberSequence="4"/></one:List><one:Tag index="6" completed="true" disabled="false"/><one:T>3发送</one:T></one:OE>
    </one:OEChildren></one:Outline></one:Page>"""

    result = page_equivalence(
        expected,
        actual,
        verification_tier=copy_verification_tier(
            ["Outline", "RichText", "List", "Tag"]
        ),
    )

    assert result["verification_tier"] == "semantic_list_tag"
    assert result["equivalent"] is True
    assert result["checks"]["canonical_xml"] is False
    assert result["checks"]["content_objects"] is False
    assert result["checks"]["semantic_list_tag"] is True
    assert result["acceptance_checks"] == [
        "visible_text",
        "binary_sha256",
        "semantic_list_tag",
    ]


def test_semantic_list_tag_equivalence_rejects_changed_completion():
    expected = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:TagDef index="0" type="0" symbol="3"/><one:Outline><one:OEChildren>
    <one:OE><one:List><one:Bullet bullet="2"/></one:List><one:Tag index="0" completed="false"/><one:T>A</one:T></one:OE>
    </one:OEChildren></one:Outline></one:Page>"""
    actual = expected.replace('completed="false"', 'completed="true"')

    result = page_equivalence(
        expected,
        actual,
        verification_tier="semantic_list_tag",
    )

    assert result["equivalent"] is False
    assert result["checks"]["visible_text"] is True
    assert result["checks"]["semantic_list_tag"] is False


def test_strict_copy_verification_remains_default_for_validated_content():
    assert copy_verification_tier(["Outline", "RichText", "Table", "Image"]) == "strict_canonical"
    assert copy_verification_tier(["Outline", "List", "Tag", "Table"]) == "strict_canonical"


def test_transform_reports_validated_rich_text_table_list_and_tag_capabilities():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>
    <one:TagDef index="0" type="0" symbol="3" name="To Do"/>
    <one:Outline objectID="outline"><one:OEChildren>
      <one:OE objectID="rich"><one:T><![CDATA[<strong>Rich</strong>]]></one:T></one:OE>
      <one:OE><one:Tag index="0" completed="false"/><one:List><one:Bullet bullet="2"/></one:List><one:T>Item</one:T></one:OE>
      <one:Table><one:Columns><one:Column index="0" width="100"/></one:Columns>
        <one:Row><one:Cell><one:OEChildren><one:OE><one:T>Cell</one:T></one:OE></one:OEChildren></one:Cell></one:Row>
      </one:Table>
    </one:OEChildren></one:Outline></one:Page>"""

    result = transform_page_for_copy(source, "target", {"p": "target"})

    assert result["content_types"] == ["List", "Outline", "RichText", "Table", "Tag"]
    assert result["lossless_candidate"] is True
    assert not any(issue["code"] == "content_type_unverified" for issue in result["issues"])


def test_default_validated_copy_types_are_lossless_candidates():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>
    <one:Outline><one:OEChildren>
      <one:OE><one:T><![CDATA[<strong>Rich</strong>]]></one:T></one:OE>
      <one:OE><one:Table><one:Columns><one:Column index="0" width="100"/></one:Columns>
        <one:Row><one:Cell><one:OEChildren><one:OE><one:T>Cell</one:T></one:OE></one:OEChildren></one:Cell></one:Row>
      </one:Table></one:OE>
    </one:OEChildren></one:Outline>
    <one:Image format="png"><one:Data>YWJj</one:Data></one:Image>
    </one:Page>"""

    result = transform_page_for_copy(source, "target", {"p": "target"})

    assert result["content_types"] == ["Image", "Outline", "RichText", "Table"]
    assert result["lossless_candidate"] is True
    assert not any(issue["code"] == "content_type_unverified" for issue in result["issues"])


def test_plan_copy_defaults_to_only_the_selected_page(monkeypatch):
    install_plan_fakes(monkeypatch)

    first = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))
    second = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))

    assert first["ok"] is True
    assert first["plan_digest"] == second["plan_digest"]
    assert first["source_snapshot_digest"] == second["source_snapshot_digest"]
    assert first["include_descendants"] is False
    assert [item["id"] for item in first["snapshots"]["source"]["resources"]] == ["parent"]
    assert set(first["snapshots"]["source"]["page_hashes"]) == {"parent"}
    assert first["snapshots"]["destination"] == first["destination"]
    assert first["estimated"]["resources"] == 1
    assert first["estimated"]["pages"] == 1
    assert first["execute_tool"] == "copy_page"
    assert first["copyability"]["lossless_candidate"] is True


def test_plan_copy_explicitly_includes_complete_page_subtree_and_changes_digest(monkeypatch):
    install_plan_fakes(monkeypatch)

    root_only = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))
    subtree = asyncio.run(
        plan_copy(
            "parent",
            "destination-section",
            "Copied Parent",
            include_descendants=True,
        )
    )

    assert subtree["ok"] is True
    assert subtree["include_descendants"] is True
    assert subtree["plan_digest"] != root_only["plan_digest"]
    assert [item["id"] for item in subtree["snapshots"]["source"]["resources"]] == [
        "parent",
        "child",
    ]
    assert set(subtree["snapshots"]["source"]["page_hashes"]) == {"parent", "child"}
    assert subtree["estimated"]["pages"] == 2


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


def test_include_descendants_does_not_change_container_copy_scope(monkeypatch):
    install_recursive_execute_fakes(monkeypatch)

    default_plan = server.services.copying._build_plan(
        "source-section", "destination-notebook", "Section Copy"
    )
    explicit_plan = server.services.copying._build_plan(
        "source-section",
        "destination-notebook",
        "Section Copy",
        include_descendants=True,
    )

    assert default_plan["include_descendants"] is True
    assert explicit_plan["include_descendants"] is True
    assert default_plan["plan_digest"] == explicit_plan["plan_digest"]
    assert [item["id"] for item in default_plan["resources"]] == [
        "source-section",
        "source-page",
    ]


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

    result = asyncio.run(
        plan_copy(
            "parent",
            "destination-section",
            "Copied Parent",
            include_descendants=True,
        )
    )

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


@pytest.mark.write_contract
@pytest.mark.parametrize(("planned_scope", "executed_scope"), [(True, False), (False, True)])
def test_copy_rejects_include_descendants_mismatch_before_create(
    monkeypatch, planned_scope, executed_scope
):
    install_plan_fakes(monkeypatch, body="")
    planned = asyncio.run(
        plan_copy(
            "parent",
            "destination-section",
            "Copied Parent",
            include_descendants=planned_scope,
        )
    )
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setattr(server.services.pages, "confirm", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        server.services.mutations,
        "create_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No mutation is allowed")),
    )

    result = asyncio.run(
        copy_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            planned["plan_digest"],
            destination_title="Copied Parent",
            include_descendants=executed_scope,
        )
    )

    assert result["ok"] is False
    assert "stale" in result["error"]


def test_partial_create_reports_created_ids_without_rollback(monkeypatch):
    install_plan_fakes(monkeypatch)
    plan = server.services.copying._build_plan(
        "parent", "destination-section", "Copied Parent", include_descendants=True
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
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["outcome"] == "copy_unverified"
    assert caught.value.details["created_ids"] == ["new-parent", "new-child"]
    assert caught.value.details["id_map"] == {"parent": "new-parent"}
    assert caught.value.details["failed_step"] == "initialize_created_page"


def test_copy_rejects_create_readback_that_aliases_a_source_page(monkeypatch):
    install_plan_fakes(monkeypatch)
    plan = server.services.copying._build_plan(
        "parent", "destination-section", "Copied Parent", include_descendants=True
    )
    calls = []

    def create(section_id, title, *args, **kwargs):
        calls.append(title)
        target_id = "new-parent" if len(calls) == 1 else "child"
        return {
            "page": {
                "resource_type": "page",
                "id": target_id,
                "title": title,
                "section_id": section_id,
                "parent_id": section_id,
                "page_level": 1,
                "order": len(calls) - 1,
            }
        }

    monkeypatch.setattr(server.services.mutations, "create_page", create)

    with pytest.raises(PartialFailure, match="existing Copy source ID") as caught:
        server.services.copying._execute_copy(plan)

    assert caught.value.details["created_ids"] == ["new-parent"]
    assert caught.value.details["id_map"] == {"parent": "new-parent"}
    assert caught.value.details["failed_step"] == "create_resources"


@pytest.mark.parametrize(
    ("target", "message"),
    [
        (
            {
                "resource_type": "section",
                "id": "new-target",
                "name": "Wrong Type",
                "parent_id": "destination-section",
            },
            "mismatched target resource",
        ),
        (
            {
                "resource_type": "page",
                "id": "new-target",
                "title": "Copied Parent",
                "section_id": "wrong-section",
                "parent_id": "wrong-section",
            },
            "planned destination Section",
        ),
        (
            {
                "resource_type": "page",
                "id": "new-target",
                "title": "Copied Parent",
                "section_id": "destination-section",
                "parent_id": "destination-section",
                "is_in_recycle_bin": True,
            },
            "recycled",
        ),
    ],
)
def test_copy_rejects_invalid_created_target_before_content_or_reorder(
    monkeypatch, target, message
):
    install_plan_fakes(monkeypatch)
    plan = server.services.copying._build_plan(
        "parent", "destination-section", "Copied Parent"
    )
    monkeypatch.setattr(
        server.services.mutations,
        "create_page",
        lambda *args, **kwargs: {"allocated_id": "new-target", "page": target},
    )
    monkeypatch.setattr(
        server.services.copying,
        "call",
        lambda operation, **params: (_ for _ in ()).throw(
            AssertionError("content/topology mutation must not start")
        ),
    )

    with pytest.raises(PartialFailure, match=message) as caught:
        server.services.copying._execute_copy(plan)

    assert caught.value.details["allocated_ids"] == ["new-target"]
    assert caught.value.details["resolved_target_ids"] == []
    assert caught.value.details["id_map"] == {}
    assert caught.value.details["failed_step"] == "create_resources"
    assert caught.value.details["source_touched"] is False
    assert caught.value.details["topology_touched"] is False


def test_created_page_wins_over_contextual_parent_section():
    result = {
        "page": {"id": "new-page", "resource_type": "page"},
        "section": {"id": "parent-section", "resource_type": "section"},
    }
    assert server.services.copying._created_item(result)["id"] == "new-page"


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
@pytest.mark.parametrize(
    ("source_id", "destination_parent_id", "destination_name", "destination_base_folder"),
    [
        ("source-section", "destination-notebook", "Section Copy", ""),
        ("source-group", "destination-notebook", "Group Copy", ""),
        ("source-notebook", "", "Notebook Copy", "{tmp}"),
    ],
)
def test_container_copy_preserves_two_same_title_pages_as_fresh_distinct_targets(
    monkeypatch,
    tmp_path,
    source_id,
    destination_parent_id,
    destination_name,
    destination_base_folder,
):
    state = install_recursive_execute_fakes(monkeypatch, duplicate_page_titles=True)
    base_folder = str(tmp_path) if destination_base_folder else ""
    before_ids = {item["id"] for item in state}
    source_items_before = {
        item["id"]: dict(item)
        for item in state
        if item["id"] in {"source-page", "source-page-2"}
    }
    source_xml_before = {
        page_id: server.services.pages.xml(page_id, "all")
        for page_id in source_items_before
    }
    plan = server.services.copying._build_plan(
        source_id,
        destination_parent_id,
        destination_name,
        base_folder,
    )

    result = server.services.copying._execute_copy(plan)

    id_map = result["copy_report"]["id_map"]
    target_ids = [id_map["source-page"], id_map["source-page-2"]]
    assert len(set(target_ids)) == 2
    assert set(target_ids).isdisjoint(before_ids)
    targets = [next(item for item in state if item["id"] == target_id) for target_id in target_ids]
    assert [item["title"] for item in targets] == ["Page", "Page"]
    assert [item["order"] for item in targets] == sorted(item["order"] for item in targets)
    assert result["copy_report"]["verified"] is True
    assert result["copy_report"]["lossless"] is True
    assert result["copy_report"]["resolved_target_ids"] == list(id_map.values())
    assert result["copy_report"]["allocated_ids"] == list(id_map.values())
    for page_id, before_item in source_items_before.items():
        assert next(item for item in state if item["id"] == page_id) == before_item
        assert server.services.pages.xml(page_id, "all") == source_xml_before[page_id]


def test_copy_rejects_reused_previous_target_before_content_or_reorder(monkeypatch):
    install_plan_fakes(monkeypatch)
    plan = server.services.copying._build_plan(
        "parent", "destination-section", "Copied Parent", include_descendants=True
    )
    calls = []

    def create(section_id, title, *args, **kwargs):
        calls.append(title)
        return {
            "allocated_id": "new-parent",
            "page": {
                "resource_type": "page",
                "id": "new-parent",
                "title": title,
                "section_id": section_id,
                "parent_id": section_id,
                "page_level": 1,
                "order": 0,
            },
        }

    monkeypatch.setattr(server.services.mutations, "create_page", create)
    monkeypatch.setattr(
        server.services.copying,
        "call",
        lambda operation, **params: (_ for _ in ()).throw(
            AssertionError("content/topology mutation must not start")
        ),
    )

    with pytest.raises(PartialFailure, match="same target ID") as caught:
        server.services.copying._execute_copy(plan)

    assert calls == ["Copied Parent", "Child"]
    assert caught.value.details["resolved_target_ids"] == ["new-parent"]
    assert caught.value.details["allocated_ids"] == ["new-parent", "new-parent"]
    assert caught.value.details["source_touched"] is False
    assert caught.value.details["source_untouched"] is True
    assert caught.value.details["manual_recovery_required"] is True


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
@pytest.mark.parametrize("include_descendants", [False, True])
def test_page_copy_scope_creates_only_selected_ids_and_verifies(monkeypatch, include_descendants):
    state = hierarchy_items()
    xml_store = {
        "parent": page_xml("parent", "Parent", '<a href="onenote:#child">Child</a>'),
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
        "parent",
        "destination-section",
        "Copied Parent",
        include_descendants=include_descendants,
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
    expected_map = {"parent": "new-1"}
    if include_descendants:
        expected_map["child"] = "new-2"
    assert result["copy_report"]["id_map"] == expected_map
    assert result["created_ids"] == list(expected_map.values())
    assert result["copy_report"]["copied_counts"] == {
        "resources": len(expected_map),
        "pages": len(expected_map),
    }
    assert created[0]["page_level"] == 1
    if include_descendants:
        assert created[1]["page_level"] == 2
        assert "#new-2" in xml_store["new-1"]
        assert "#child" not in xml_store["new-1"]
    else:
        assert len(created) == 1
        assert "#child" in xml_store["new-1"]


def test_move_page_scope_defaults_to_root_and_binds_preserved_descendants(monkeypatch):
    install_plan_fakes(monkeypatch, body="")

    root_only = server.services.copying.plan_move_page(
        "parent", "destination-section", "Moved Parent"
    )
    subtree = server.services.copying.plan_move_page(
        "parent", "destination-section", "Moved Parent", True
    )

    assert root_only["include_descendants"] is False
    assert [item["id"] for item in root_only["snapshots"]["source"]["resources"]] == [
        "parent"
    ]
    assert [
        item["id"] for item in root_only["snapshots"]["move_source"]["resources"]
    ] == ["parent", "child"]
    assert {step["operation"] for step in root_only["steps"]} >= {
        "promote_preserved_descendants",
        "recycle_source_pages",
    }
    assert subtree["include_descendants"] is True
    assert [item["id"] for item in subtree["snapshots"]["source"]["resources"]] == [
        "parent",
        "child",
    ]
    assert not any(
        step["operation"] == "promote_preserved_descendants"
        for step in subtree["steps"]
    )


@pytest.mark.write_contract
@pytest.mark.parametrize("planned_scope,executed_scope", [(False, True), (True, False)])
def test_move_page_rejects_scope_mismatch_before_copy(
    monkeypatch, planned_scope, executed_scope
):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_move_page(
        "parent",
        "destination-section",
        "Moved Parent",
        planned_scope,
    )
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda value: (_ for _ in ()).throw(AssertionError("stale Move must not copy")),
    )

    with pytest.raises(ValueError, match="missing or stale"):
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            plan["plan_digest"],
            destination_title="Moved Parent",
            include_descendants=executed_scope,
        )


@pytest.mark.write_contract
def test_root_only_move_promotes_and_preserves_excluded_descendants(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    state["xml_clock"] = "before-promotion"

    def hierarchy_sensitive_page_xml(page_id, page_info="basic"):
        item = next(value for value in state["items"] if value["id"] == page_id)
        return page_xml(page_id, item["title"], state["body"]).replace(
            'lastModifiedTime="clock"',
            f'lastModifiedTime="{state["xml_clock"]}" pageLevel="{item["page_level"]}"',
        )

    monkeypatch.setattr(server.services.pages, "xml", hierarchy_sensitive_page_xml)
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_move_page(
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

    def update_hierarchy(operation, **params):
        assert operation == "update_hierarchy"
        root = ET.fromstring(params["xml"])
        nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Page"]
        stack = []
        for order, node in enumerate(nodes):
            item = next(value for value in state["items"] if value["id"] == node.attrib["ID"])
            level = int(node.attrib["pageLevel"])
            while stack and stack[-1]["page_level"] >= level:
                stack.pop()
            item.update(
                order=order,
                page_level=level,
                parent_page_id=stack[-1]["id"] if stack else None,
            )
            stack.append(item)
        state["xml_clock"] = "after-promotion"
        return {"updated": True}

    monkeypatch.setattr(server.services.copying, "call", update_hierarchy)

    def delete_page(page_id, *args, **kwargs):
        state["items"] = [item for item in state["items"] if item["id"] != page_id]
        return {"deleted": True, "final_state": {"id": page_id, "is_in_recycle_bin": True}}

    monkeypatch.setattr(server.services.mutations, "delete_page", delete_page)

    result = server.services.copying.move_page(
        "parent",
        "destination-section",
        "Parent",
        "source-section",
        plan["plan_digest"],
        destination_title="Moved Parent",
    )

    child = next(item for item in state["items"] if item["id"] == "child")
    assert result["include_descendants"] is False
    assert result["deleted_source_ids"] == ["parent"]
    assert result["preserved_descendants"]["preserved_descendant_ids"] == ["child"]
    assert child["page_level"] == 1
    assert child["parent_page_id"] is None


@pytest.mark.write_contract
def test_root_only_move_blocks_delete_when_descendant_promotion_fails(monkeypatch):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_move_page(
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
        server.services.copying,
        "call",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("promotion failed")),
    )
    monkeypatch.setattr(
        server.services.mutations,
        "delete_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("source delete must remain blocked")
        ),
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            plan["plan_digest"],
            destination_title="Moved Parent",
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["source_topology_may_have_changed"] is True
    assert caught.value.details["preservation_error"] == "promotion failed"


@pytest.mark.write_contract
def test_move_page_degrades_to_copy_when_fidelity_is_unverified(monkeypatch):
    install_plan_fakes(monkeypatch)
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_move_page(
        "parent", "destination-section", "Moved Parent", True
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
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            plan["plan_digest"],
            destination_title="Moved Parent",
            include_descendants=True,
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["source_deleted"] is False


@pytest.mark.write_contract
def test_move_page_normalizes_copy_readback_failure_to_copy_only(monkeypatch):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_move_page(
        "parent", "destination-section", "Moved Parent", True
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
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            plan["plan_digest"],
            destination_title="Moved Parent",
            include_descendants=True,
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["copy_report"] == report
    assert caught.value.details["created_ids"] == ["new-parent"]


@pytest.mark.write_contract
@pytest.mark.parametrize("failure_mode", ["source_alias", "ambiguous_readback"])
def test_move_page_actual_copy_identity_failure_blocks_all_source_deletes(
    monkeypatch, failure_mode
):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    planned = server.services.copying.plan_move_page(
        "parent", "destination-section", "Moved Parent", True
    )

    def create_page(section_id, title, *args, **kwargs):
        if failure_mode == "source_alias":
            return {
                "allocated_id": "parent",
                "page": {
                    "resource_type": "page",
                    "id": "parent",
                    "title": title,
                    "section_id": section_id,
                    "parent_id": section_id,
                },
            }
        raise PartialFailure(
            "created Page path remained ambiguous",
            partial=True,
            allocated_ids=["new-ambiguous-page"],
            resolved_target_ids=[],
            created_ids=["new-ambiguous-page"],
            source_touched=False,
            topology_touched=True,
            manual_recovery_required=True,
            failed_step="verify_created_page",
        )

    monkeypatch.setattr(server.services.mutations, "create_page", create_page)
    monkeypatch.setattr(
        server.services.copying,
        "call",
        lambda operation, **params: (_ for _ in ()).throw(
            AssertionError("content/topology mutation must not start")
        ),
    )
    monkeypatch.setattr(
        server.services.mutations,
        "delete_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("source delete must remain blocked")
        ),
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            planned["plan_digest"],
            destination_title="Moved Parent",
            include_descendants=True,
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["source_touched"] is False
    assert caught.value.details["resolved_target_ids"] == []


@pytest.mark.write_contract
def test_move_page_reports_copy_only_when_source_revalidation_fails(monkeypatch):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_move_page(
        "parent", "destination-section", "Moved Parent", True
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
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            plan["plan_digest"],
            destination_title="Moved Parent",
            include_descendants=True,
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["created_ids"] == ["new-parent"]
    assert caught.value.details["source_revalidation_error"] == "source vanished"


@pytest.mark.write_contract
def test_move_page_blocks_delete_when_source_changes_after_copy(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_move_page(
        "parent", "destination-section", "Moved Parent", True
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
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            plan["plan_digest"],
            destination_title="Moved Parent",
            include_descendants=True,
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["created_ids"] == ["new-parent"]


@pytest.mark.write_contract
def test_move_page_recycles_source_pages_leaf_to_root(monkeypatch):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_move_page(
        "parent", "destination-section", "Moved Parent", True
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

    result = server.services.copying.move_page(
        "parent",
        "destination-section",
        "Parent",
        "source-section",
        plan["plan_digest"],
        destination_title="Moved Parent",
        include_descendants=True,
    )

    assert deleted == ["child", "parent"]
    assert result["source_deleted"] is True
    assert result["source_deleted_nonpermanently"] is True
    assert result["source_deleted_to_recycle_bin"] is True
    assert result["recycle_bin_verification"] == "verified"
    assert result["outcome"] == "moved"


@pytest.mark.write_contract
def test_move_page_reports_verified_and_remaining_ids_on_delete_failure(monkeypatch):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_move_page(
        "parent", "destination-section", "Moved Parent", True
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
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            plan["plan_digest"],
            destination_title="Moved Parent",
            include_descendants=True,
        )

    assert caught.value.details["outcome"] == "source_partially_removed"
    assert caught.value.details["attempted_source_ids"] == ["child", "parent"]
    assert caught.value.details["recycled_source_ids"] == ["child"]
    assert caught.value.details["deleted_source_ids"] == ["child"]
    assert caught.value.details["remaining_source_ids"] == ["parent"]
    assert caught.value.details["recycle_unverified_source_ids"] == []
    assert caught.value.details["created_ids"] == ["new-parent", "new-child"]


@pytest.mark.write_contract
def test_move_page_accepts_active_absence_without_recycle_metadata(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    state["items"] = [item for item in state["items"] if item["id"] != "child"]
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_PAGE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying.plan_move_page(
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
    result = server.services.copying.move_page(
        "parent",
        "destination-section",
        "Parent",
        "source-section",
        plan["plan_digest"],
        destination_title="Moved Parent",
    )

    assert result["outcome"] == "moved"
    assert result["source_deleted"] is True
    assert result["source_deleted_nonpermanently"] is True
    assert result["source_deleted_to_recycle_bin"] is None
    assert result["recycle_bin_verification"] == "not_required_com_unavailable"
    assert result["attempted_source_ids"] == ["parent"]
    assert result["deleted_source_ids"] == ["parent"]
    assert result["recycled_source_ids"] == []
    assert result["recycle_unverified_source_ids"] == ["parent"]
    assert any("COM did not expose" in warning for warning in result["warnings"])


def container_move_items() -> list[dict]:
    return [
        {
            "resource_type": "notebook",
            "id": "source-notebook",
            "name": "Source Notebook",
            "path": "Source Notebook",
            "parent_id": None,
            "modified": "m1",
        },
        {
            "resource_type": "section_group",
            "id": "source-group",
            "name": "Source Group",
            "path": "Source Notebook/Source Group",
            "parent_id": "source-notebook",
            "notebook_id": "source-notebook",
            "modified": "m1",
        },
        {
            "resource_type": "section",
            "id": "source-container-section",
            "name": "Source Section",
            "path": "Source Notebook/Source Group/Source Section",
            "parent_id": "source-group",
            "notebook_id": "source-notebook",
            "modified": "m1",
        },
        {
            "resource_type": "page",
            "id": "source-container-page",
            "title": "Source Page",
            "path": "Source Notebook/Source Group/Source Section/Source Page",
            "parent_id": "source-container-section",
            "notebook_id": "source-notebook",
            "section_id": "source-container-section",
            "parent_page_id": None,
            "page_level": 1,
            "order": 0,
            "modified": "m1",
        },
        {
            "resource_type": "notebook",
            "id": "destination-notebook",
            "name": "Destination Notebook",
            "path": "Destination Notebook",
            "parent_id": None,
            "modified": "m1",
        },
    ]


@pytest.mark.parametrize(
    ("resource_type", "source_id", "planner", "execute_tool"),
    [
        ("section", "source-container-section", "plan_move_section", "move_section"),
        (
            "section_group",
            "source-group",
            "plan_move_section_group",
            "move_section_group",
        ),
    ],
)
def test_container_move_plan_is_cross_notebook_and_move_specific(
    monkeypatch, resource_type, source_id, planner, execute_tool
):
    items = container_move_items()
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [dict(item) for item in items],
    )
    monkeypatch.setattr(
        server.services.pages,
        "xml",
        lambda page_id, page_info="basic": page_xml(page_id, "Source Page", "Body"),
    )

    plan = getattr(server.services.copying, planner)(
        source_id, "destination-notebook", "Moved Container"
    )

    assert plan["operation"] == f"move_{resource_type}"
    assert plan["execute_tool"] == execute_tool
    assert plan["move_notebooks"] == {
        "source_notebook_id": "source-notebook",
        "destination_notebook_id": "destination-notebook",
        "cross_notebook": True,
    }
    assert [step["operation"] for step in plan["steps"]][-4:] == [
        "revalidate_source",
        "delete_source_root_nonpermanently",
        "verify_source_subtree_inactive",
        "revalidate_destination",
    ]


@pytest.mark.parametrize(
    ("source_id", "planner", "suggestion"),
    [
        ("source-container-section", "plan_move_section", "reparent_section"),
        ("source-group", "plan_move_section_group", "reparent_section_group"),
    ],
)
def test_container_move_rejects_same_notebook_before_mutation(
    monkeypatch, source_id, planner, suggestion
):
    items = container_move_items()
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [dict(item) for item in items],
    )
    monkeypatch.setattr(
        server.services.pages,
        "xml",
        lambda page_id, page_info="basic": page_xml(page_id, "Source Page", "Body"),
    )

    with pytest.raises(ValueError, match=suggestion):
        getattr(server.services.copying, planner)(source_id, "source-notebook", "Moved")


def install_container_move_execution_fakes(monkeypatch, resource_type: str):
    source_id = "source-section" if resource_type == "section" else "source-group"
    child_id = "source-page" if resource_type == "section" else "source-section"
    target_root = f"target-{source_id}"
    target_child = f"target-{child_id}"
    plan = {
        "operation": f"move_{resource_type}",
        "plan_digest": "move-digest",
        "source_digest": "source-digest",
        "source": {
            "resource_type": resource_type,
            "id": source_id,
            "name": "Source",
            "parent_id": "source-notebook",
            "notebook_id": "source-notebook",
            "modified": "m1",
        },
        "resources": [
            {"resource_type": resource_type, "id": source_id},
            {"resource_type": "page" if resource_type == "section" else "section", "id": child_id},
        ],
        "move_notebooks": {
            "source_notebook_id": "source-notebook",
            "destination_notebook_id": "destination-notebook",
            "cross_notebook": True,
        },
    }
    report = {
        "id_map": {source_id: target_root, child_id: target_child},
        "lossless": True,
        "verified": True,
        "skipped_content": [],
    }
    copied = {
        "item": {"resource_type": resource_type, "id": target_root},
        "copy_report": report,
        "created_ids": [target_root, target_child],
        "warnings": [],
    }
    captures = iter(
        [
            {"source_digest": "source-digest"},
            {"source_digest": "target-digest"},
            {"source_digest": "target-digest"},
        ]
    )
    delete_calls = []
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    monkeypatch.setattr(server.services.copying, "_build_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(server.services.copying, "_execute_copy", lambda _plan: copied)
    monkeypatch.setattr(server.services.copying, "_capture_source", lambda *args, **kwargs: next(captures))
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [
            {"resource_type": resource_type, "id": target_root},
            {"resource_type": "page", "id": target_child},
        ],
    )

    def delete_resource(*args):
        delete_calls.append(args)
        return {"final_state": None}

    monkeypatch.setattr(server.services.mutations, "delete_resource", delete_resource)
    return source_id, child_id, copied, delete_calls


@pytest.mark.write_contract
@pytest.mark.parametrize("resource_type", ["section", "section_group"])
def test_container_move_uses_one_nonpermanent_root_delete(monkeypatch, resource_type):
    source_id, child_id, _copied, delete_calls = install_container_move_execution_fakes(
        monkeypatch, resource_type
    )
    method = getattr(server.services.copying, f"move_{resource_type}")

    result = method(
        source_id,
        "destination-notebook",
        "Source",
        "source-notebook",
        "move-digest",
        "m1",
        "Moved",
    )

    assert len(delete_calls) == 1
    assert delete_calls[0] == (
        source_id,
        resource_type,
        "Source",
        "source-notebook",
        "m1",
        False,
    )
    assert result["outcome"] == "moved"
    assert result["attempted_source_ids"] == [source_id]
    assert result["deleted_source_ids"] == [source_id]
    assert result["inactive_source_ids"] == [source_id, child_id]
    assert result["source_deleted_nonpermanently"] is True


@pytest.mark.write_contract
def test_container_move_blocks_root_delete_when_copy_gate_is_not_lossless(monkeypatch):
    source_id, _child_id, copied, delete_calls = install_container_move_execution_fakes(
        monkeypatch, "section"
    )
    copied["copy_report"]["lossless"] = False

    with pytest.raises(PartialFailure) as raised:
        server.services.copying.move_section(
            source_id,
            "destination-notebook",
            "Source",
            "source-notebook",
            "move-digest",
            "m1",
            "Moved",
        )

    assert raised.value.details["outcome"] == "copy_only"
    assert raised.value.details["source_deleted"] is False
    assert delete_calls == []


@pytest.mark.write_contract
def test_container_move_does_not_delete_when_source_revalidation_changes(monkeypatch):
    source_id, _child_id, _copied, delete_calls = install_container_move_execution_fakes(
        monkeypatch, "section"
    )
    monkeypatch.setattr(
        server.services.copying,
        "_capture_source",
        lambda *args, **kwargs: {"source_digest": "changed-source"},
    )

    with pytest.raises(PartialFailure) as raised:
        server.services.copying.move_section(
            source_id,
            "destination-notebook",
            "Source",
            "source-notebook",
            "move-digest",
            "m1",
            "Moved",
        )

    assert raised.value.details["outcome"] == "copy_only"
    assert delete_calls == []


@pytest.mark.write_contract
def test_container_move_reports_remaining_descendant_without_extra_deletes(monkeypatch):
    source_id, child_id, _copied, delete_calls = install_container_move_execution_fakes(
        monkeypatch, "section_group"
    )
    monkeypatch.setattr("local_onenote_mcp.services.copying.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [
            {"resource_type": "page", "id": child_id},
            {"resource_type": "section_group", "id": f"target-{source_id}"},
            {"resource_type": "section", "id": f"target-{child_id}"},
        ],
    )

    with pytest.raises(PartialFailure) as raised:
        server.services.copying.move_section_group(
            source_id,
            "destination-notebook",
            "Source",
            "source-notebook",
            "move-digest",
            "m1",
            "Moved",
        )

    assert len(delete_calls) == 1
    assert raised.value.details["outcome"] == "source_partially_removed"
    assert raised.value.details["attempted_source_ids"] == [source_id]
    assert raised.value.details["remaining_source_ids"] == [child_id]
