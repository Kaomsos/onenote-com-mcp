import asyncio
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import xml.etree.ElementTree as ET

import pytest

from local_onenote_mcp import server
from local_onenote_mcp.page import (
    canonical_page_digest,
    copy_verification_tier,
    page_content_capability_projection,
    page_equivalence,
    semantic_content_comparison,
    transform_page_for_copy,
)
from local_onenote_mcp.services import (
    PageMixedContentReadbackMismatch,
    PageReadbackMismatch,
    PageRichTextReadbackMismatch,
    PageTableReadbackMismatch,
    PageUnknownContentReadbackMismatch,
    PartialFailure,
    page_readback_mismatch_error,
)
from local_onenote_mcp.services.pages import stable_page_content_digest
from local_onenote_mcp.tools.copying import copy_page
from local_onenote_mcp.tools.responses import caught
from tests.destination_position_assertions import assert_destination_position_contract


pytestmark = pytest.mark.usefixtures("virtual_convergence_clock")


async def plan_copy(
    source_id: str,
    destination_parent_id: str = "",
    destination_name: str = "",
    destination_base_folder: str = "",
    include_descendants: bool = False,
):
    """Exercise the internal plan builder without restoring a public MCP tool."""

    try:
        return {
            "ok": True,
            **server.services.copying._inspect_copy_plan(
                source_id,
                destination_parent_id,
                destination_name,
                destination_base_folder,
                include_descendants,
            ),
        }
    except Exception as exc:
        envelope = caught(exc)
        return {
            "ok": False,
            "complete": False,
            "code": envelope["error"]["code"],
            "error": envelope["error"]["message"],
            **envelope["error"]["details"],
        }


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
        "schema_version": 4,
        "capabilities": ["InsertedFile", "Outline"],
        "object_kind_counts": {"InsertedFile": 1, "OE": 1, "Outline": 1},
        "structural_marker_counts": {},
        "embedded_markup_tag_counts": {},
        "embedded_markup_attribute_name_counts": {},
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


def test_video_preview_markup_remains_plain_image_and_rich_text():
    secret = "https://video.example.invalid/private-token"
    source = page_xml("source", "Preview").replace(
        "</one:Page>",
        (
            '<one:Outline><one:OEChildren><one:OE><one:Image format="png">'
            "<one:Data>c3ludGhldGlj</one:Data><one:OCRData>"
            "<one:OCRText>private title</one:OCRText>"
            '<one:OCRToken x="1" y="2" width="3" height="4" />'
            "</one:OCRData></one:Image></one:OE><one:OE><one:T><![CDATA["
            f'<a href="{secret}" v="video"><span style="x">Preview</span></a>'
            "]]></one:T></one:OE></one:OEChildren></one:Outline></one:Page>"
        ),
    )

    projection = page_content_capability_projection(source)
    transformed = transform_page_for_copy(source, "target", {"source": "target"})

    assert projection["schema_version"] == 4
    assert projection["capabilities"] == ["Image", "Outline", "RichText"]
    assert projection["embedded_markup_tag_counts"] == {"a": 1, "span": 1}
    assert projection["embedded_markup_attribute_name_counts"] == {
        "a@href": 1,
        "a@v": 1,
        "span@style": 1,
    }
    assert projection["unknown_nodes"] == []
    assert projection["complete"] is True
    assert secret not in str(projection)
    assert "private title" not in str(projection)
    assert not any(
        issue["code"] == "unsupported_nested_page_node"
        for issue in transformed["issues"]
    )
    assert transformed["issues"] == []
    assert f'href="{secret}"' in transformed["xml"]
    assert 'v="video"' in transformed["xml"]
    assert transformed["lossless_candidate"] is True

    ordinary_link = source.replace(' v="video"', "")
    ordinary_projection = page_content_capability_projection(ordinary_link)
    assert ordinary_projection["capabilities"] == ["Image", "Outline", "RichText"]
    tier = copy_verification_tier(transformed["content_types"])
    strict_equivalence = page_equivalence(
        source,
        ordinary_link,
        verification_tier=tier,
    )
    changed_external_link = page_equivalence(
        source,
        ordinary_link.replace("private-token", "changed-token"),
        verification_tier=tier,
    )
    assert tier == "strict_canonical"
    assert strict_equivalence["equivalent"] is False
    assert strict_equivalence["checks"]["canonical_xml"] is False
    assert changed_external_link["equivalent"] is False

    misplaced = page_xml("source", "Preview").replace(
        "</one:Page>", "<one:OCRData><one:OCRText /></one:OCRData></one:Page>"
    )
    misplaced_projection = page_content_capability_projection(misplaced)
    assert misplaced_projection["complete"] is False
    assert set(misplaced_projection["unknown_nodes"]) == {
        "{http://schemas.microsoft.com/office/onenote/2013/onenote}OCRData",
        "{http://schemas.microsoft.com/office/onenote/2013/onenote}OCRText",
    }


@pytest.mark.parametrize(
    "shape_xml,expected_markers",
    [
        ("<one:ShapeInfo/>", {"ShapeInfo": 1}),
        (
            "<one:ShapeInfo><one:AnchorPoint/><one:AnchorPoint/></one:ShapeInfo>",
            {"AnchorPoint": 2, "ShapeInfo": 1},
        ),
    ],
)
def test_page_content_capability_projection_classifies_ui_shape_by_shape_info(
    shape_xml: str,
    expected_markers: dict[str, int],
) -> None:
    source = page_xml("page", "Shape").replace(
        "</one:Page>",
        (
            '<one:InkDrawing objectID="shape"><one:Position x="1" y="2"/>'
            f"{shape_xml}<one:Ink>synthetic</one:Ink></one:InkDrawing></one:Page>"
        ),
    )

    projection = page_content_capability_projection(source)

    assert projection["capabilities"] == ["UIShape"]
    assert projection["object_kind_counts"] == {"InkDrawing": 1}
    assert projection["structural_marker_counts"] == expected_markers
    assert projection["unknown_nodes"] == []
    assert projection["complete"] is True


def test_transform_preserves_validated_ui_shape_structure() -> None:
    source = page_xml("source", "Shape").replace(
        "</one:Page>",
        (
            '<one:InkDrawing objectID="shape"><one:Position x="1" y="2"/>'
            '<one:ShapeInfo><one:AnchorPoint/></one:ShapeInfo>'
            "<one:Ink>synthetic</one:Ink></one:InkDrawing></one:Page>"
        ),
    )

    result = transform_page_for_copy(source, "target", {"source": "target"})

    assert "ShapeInfo" in result["xml"]
    assert "AnchorPoint" in result["xml"]
    assert not any(
        issue["code"] == "unsupported_nested_page_node"
        for issue in result["issues"]
    )
    assert result["lossless_candidate"] is True
    assert not any(
        issue["code"] == "content_type_unverified" for issue in result["issues"]
    )


def test_shape_structural_nodes_outside_inkdrawing_remain_unknown_and_omitted() -> None:
    source = page_xml("source", "Title", "Body").replace(
        "</one:OEChildren></one:Outline>",
        "<one:ShapeInfo/><one:AnchorPoint/></one:OEChildren></one:Outline>",
    )

    projection = page_content_capability_projection(source)
    result = transform_page_for_copy(source, "target", {"source": "target"})

    assert projection["complete"] is False
    assert projection["structural_marker_counts"] == {}
    assert projection["unknown_nodes"] == [
        "{http://schemas.microsoft.com/office/onenote/2013/onenote}AnchorPoint",
        "{http://schemas.microsoft.com/office/onenote/2013/onenote}ShapeInfo",
    ]
    assert "ShapeInfo" not in result["xml"]
    assert "AnchorPoint" not in result["xml"]
    assert any(
        issue["code"] == "unsupported_nested_page_node"
        for issue in result["issues"]
    )


def test_recorded_media_playlist_projection_and_transform_are_context_bounded() -> None:
    source = page_xml("source", "Recording", "Synthetic").replace(
        "<one:T><![CDATA[Synthetic]]></one:T>",
        (
            "<one:MediaIndex><one:MediaReference/></one:MediaIndex>"
            "<one:MediaFile><one:MediaReference/></one:MediaFile>"
            "<one:MediaIndex><one:MediaReference/></one:MediaIndex>"
            "<one:T><![CDATA[Synthetic]]></one:T>"
        ),
    ).replace(
        "</one:Page>",
        (
            "<one:MediaPlaylist><one:MediaReference/>"
            "</one:MediaPlaylist></one:Page>"
        ),
    )

    projection = page_content_capability_projection(source)
    result = transform_page_for_copy(source, "target", {"source": "target"})

    assert projection["capabilities"] == ["MediaFile", "Outline"]
    assert projection["object_kind_counts"] == {
        "MediaFile": 1,
        "OE": 1,
        "Outline": 1,
    }
    assert projection["unknown_nodes"] == []
    assert projection["unsupported_page_roots"] == []
    assert projection["complete"] is True
    assert "MediaPlaylist" in result["xml"]
    assert "MediaIndex" in result["xml"]
    assert "MediaReference" in result["xml"]
    assert not any(
        issue["code"] in {"unsupported_page_root", "unsupported_nested_page_node"}
        for issue in result["issues"]
    )
    assert result["lossless_candidate"] is True
    assert not any(
        issue["code"] == "content_type_unverified" for issue in result["issues"]
    )


def test_media_transform_uses_existing_cache_when_original_source_is_missing(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "recording.wma"
    cache.write_bytes(b"synthetic-media")
    missing_source = tmp_path / "missing" / "recording.wma"
    source = _recorded_media_with_timeline_html("timeline").replace(
        "<one:MediaFile>",
        (
            f'<one:MediaFile pathCache="{cache}" '
            f'pathSource="{missing_source}" preferredName="recording.wma">'
        ),
    )

    result = transform_page_for_copy(source, "target", {"source": "target"})
    root = ET.fromstring(result["xml"])
    media = next(node for node in root.iter() if node.tag.endswith("}MediaFile"))

    assert media.attrib["pathSource"] == str(cache)
    assert "pathCache" not in media.attrib


def test_inserted_file_transform_preserves_existing_source_path(
    tmp_path: Path,
) -> None:
    original = tmp_path / "synthetic.md"
    original.write_text("synthetic attachment", encoding="utf-8")
    cache = tmp_path / "OneNote-cache.bin"
    cache.write_bytes(b"cached attachment")
    source = page_xml("source", "Title", "placeholder").replace(
        "<one:T><![CDATA[placeholder]]></one:T>",
        (
            f'<one:InsertedFile pathCache="{cache}" pathSource="{original}" '
            'preferredName="synthetic.md"/>'
        ),
    )

    result = transform_page_for_copy(source, "target", {"source": "target"})
    root = ET.fromstring(result["xml"])
    inserted = next(node for node in root.iter() if node.tag.endswith("}InsertedFile"))

    assert inserted.attrib == {
        "pathSource": str(original),
        "preferredName": "synthetic.md",
    }
    assert list(inserted) == []
    assert result["content_types"] == ["InsertedFile", "Outline"]
    assert result["lossless_candidate"] is True
    assert not any(
        issue["code"] == "content_type_unverified" for issue in result["issues"]
    )


def test_inserted_file_transform_uses_existing_cache_when_source_is_missing(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "OneNote-cache.bin"
    cache.write_bytes(b"cached attachment")
    missing_source = tmp_path / "missing" / "synthetic.md"
    source = page_xml("source", "Title", "placeholder").replace(
        "<one:T><![CDATA[placeholder]]></one:T>",
        (
            f'<one:InsertedFile pathCache="{cache}" pathSource="{missing_source}" '
            'preferredName="synthetic.md"/>'
        ),
    )

    result = transform_page_for_copy(source, "target", {"source": "target"})
    root = ET.fromstring(result["xml"])
    inserted = next(node for node in root.iter() if node.tag.endswith("}InsertedFile"))

    assert inserted.attrib["pathSource"] == str(cache)
    assert inserted.attrib["preferredName"] == "synthetic.md"
    assert "pathCache" not in inserted.attrib


def test_inserted_file_transform_fails_closed_without_readable_source(
    tmp_path: Path,
) -> None:
    missing_source = tmp_path / "missing" / "synthetic.md"
    missing_cache = tmp_path / "missing" / "OneNote-cache.bin"
    source = page_xml("source", "Title", "placeholder").replace(
        "<one:T><![CDATA[placeholder]]></one:T>",
        (
            f'<one:InsertedFile pathCache="{missing_cache}" '
            f'pathSource="{missing_source}" preferredName="synthetic.md"/>'
        ),
    )

    with pytest.raises(
        ValueError,
        match=r"InsertedFile Copy requires a readable local pathSource, pathCache, or path",
    ) as captured:
        transform_page_for_copy(source, "target", {"source": "target"})

    assert str(missing_source) not in str(captured.value)
    assert str(missing_cache) not in str(captured.value)


def test_media_local_source_paths_do_not_affect_content_digests() -> None:
    first = _recorded_media_with_timeline_html("timeline").replace(
        "<one:MediaFile>",
        '<one:MediaFile pathCache="C:/cache/one.wma" pathSource="C:/source/one.wma">',
    )
    second = first.replace("C:/cache/one.wma", "D:/cache/two.wma").replace(
        "C:/source/one.wma", "D:/source/two.wma"
    )

    assert stable_page_content_digest(first) == stable_page_content_digest(second)
    assert canonical_page_digest(first) == canonical_page_digest(second)


def test_media_index_and_reference_outside_media_context_remain_unknown() -> None:
    source = page_xml("source", "Title", "Body").replace(
        "</one:OEChildren></one:Outline>",
        "<one:MediaIndex/><one:MediaReference/></one:OEChildren></one:Outline>",
    )

    projection = page_content_capability_projection(source)
    result = transform_page_for_copy(source, "target", {"source": "target"})

    assert projection["complete"] is False
    assert projection["unknown_nodes"] == [
        "{http://schemas.microsoft.com/office/onenote/2013/onenote}MediaIndex",
        "{http://schemas.microsoft.com/office/onenote/2013/onenote}MediaReference",
    ]
    assert "MediaIndex" not in result["xml"]
    assert "MediaReference" not in result["xml"]


def _recorded_media_with_timeline_html(html: str, *, extra_timeline_child: str = "") -> str:
    return (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
        'ID="source"><one:Title><one:OE><one:T>Recording</one:T></one:OE></one:Title>'
        '<one:Outline objectID="outline"><one:OEChildren>'
        '<one:OE objectID="media"><one:MediaIndex><one:MediaReference/></one:MediaIndex>'
        '<one:MediaFile><one:MediaReference/></one:MediaFile></one:OE>'
        '<one:OE objectID="timeline"><one:MediaIndex><one:MediaReference/></one:MediaIndex>'
        f"{extra_timeline_child}<one:T><![CDATA[{html}]]></one:T></one:OE>"
        "</one:OEChildren></one:Outline>"
        "<one:MediaPlaylist><one:MediaReference/></one:MediaPlaylist></one:Page>"
    )


def test_materialized_media_timeline_span_is_supporting_not_user_rich_text() -> None:
    source = _recorded_media_with_timeline_html(
        '<span style="font-family:Calibri">synthetic timeline</span>'
    )

    projection = page_content_capability_projection(source)

    assert projection["capabilities"] == ["MediaFile", "Outline"]
    assert projection["complete"] is True
    assert projection["unknown_nodes"] == []


@pytest.mark.parametrize(
    "html,extra_child",
    [
        ("<b>user formatting</b>", ""),
        ('<span style="font-weight:bold">user formatting</span>', "<one:Tag/>"),
    ],
)
def test_media_timeline_does_not_hide_general_rich_text_or_extra_structure(
    html: str,
    extra_child: str,
) -> None:
    source = _recorded_media_with_timeline_html(
        html,
        extra_timeline_child=extra_child,
    )

    projection = page_content_capability_projection(source)

    assert "RichText" in projection["capabilities"]


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
        "hierarchy_xml",
        lambda start_id="", scope="pages": hierarchy_xml_from_items(state["items"]),
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


DEFAULT_FAKE_MODIFIED = "2026-01-01T00:00:00.000Z"


def fake_item_modified(item: dict[str, Any]) -> str:
    return str(item.get("modified") or DEFAULT_FAKE_MODIFIED)


def advance_fake_mutation_epoch() -> None:
    from local_onenote_mcp.services.backend_operation_classification import advance_mutation_epoch

    advance_mutation_epoch()


def remove_fake_items(state: dict[str, Any] | list[dict[str, Any]], *item_ids: str) -> None:
    if isinstance(state, dict):
        state["items"] = [item for item in state["items"] if item["id"] not in item_ids]
        return
    state[:] = [item for item in state if item["id"] not in item_ids]


def fake_item_path(item: dict[str, Any]) -> str:
    if item.get("path"):
        return str(item["path"])
    if item.get("resource_type") == "page":
        return str(item.get("title") or item["id"])
    return str(item.get("name") or item["id"])


def hierarchy_xml_from_items(items: list[dict[str, Any]]) -> str:
    """Build minimal hierarchy XML from flat fake resource records."""

    ns = "http://schemas.microsoft.com/office/onenote/2013/onenote"
    children_by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for item in items:
        if item["resource_type"] == "page":
            parent_key = item.get("parent_page_id") or item.get("section_id")
        else:
            parent_key = item.get("parent_id")
        children_by_parent.setdefault(parent_key, []).append(item)

    def render_pages(section_id: str, parent_page_id: str | None = None) -> str:
        pages = sorted(
            [
                item
                for item in items
                if item.get("resource_type") == "page"
                and item.get("section_id") == section_id
                and item.get("parent_page_id") == parent_page_id
            ],
            key=lambda item: int(item.get("order", 0)),
        )
        chunks: list[str] = []
        for page in pages:
            title = page.get("title", "Page")
            modified = fake_item_modified(page)
            recycle = (
                ' isInRecycleBin="true"'
                if page.get("is_in_recycle_bin") is True
                else ""
            )
            chunks.append(
                f'<one:Page name="{title}" ID="{page["id"]}" '
                f'lastModifiedTime="{modified}" dateTime="{modified}" '
                f'pageLevel="{int(page.get("page_level", 1))}"{recycle}>'
                f"{render_pages(section_id, page['id'])}"
                f"</one:Page>"
            )
        return "".join(chunks)

    def render_container(parent_id: str | None) -> str:
        chunks: list[str] = []
        for item in sorted(
            children_by_parent.get(parent_id, []),
            key=lambda entry: int(entry.get("order", 0)),
        ):
            kind = item["resource_type"]
            recycle = (
                ' isInRecycleBin="true"'
                if item.get("is_in_recycle_bin") is True
                else ""
            )
            if kind == "notebook":
                name = item["name"]
                modified = fake_item_modified(item)
                path = fake_item_path(item)
                chunks.append(
                    f'<one:Notebook name="{name}" ID="{item["id"]}" path="{path}" '
                    f'lastModifiedTime="{modified}"{recycle}>'
                    f"{render_container(item['id'])}"
                    f"</one:Notebook>"
                )
            elif kind == "section_group":
                name = item["name"]
                modified = fake_item_modified(item)
                path = fake_item_path(item)
                chunks.append(
                    f'<one:SectionGroup name="{name}" ID="{item["id"]}" path="{path}" '
                    f'lastModifiedTime="{modified}"{recycle}>'
                    f"{render_container(item['id'])}"
                    f"</one:SectionGroup>"
                )
            elif kind == "section":
                name = item["name"]
                modified = fake_item_modified(item)
                path = fake_item_path(item)
                chunks.append(
                    f'<one:Section name="{name}" ID="{item["id"]}" path="{path}" '
                    f'lastModifiedTime="{modified}"{recycle}>'
                    f"{render_pages(item['id'])}"
                    f"</one:Section>"
                )
        return "".join(chunks)

    body = render_container(None)
    return (
        f'<?xml version="1.0"?><one:Notebooks xmlns:one="{ns}">{body}</one:Notebooks>'
    )


def install_recursive_execute_fakes(
    monkeypatch,
    *,
    duplicate_page_titles: bool = False,
    source_page_title: str = "Page",
    source_page_body: str = "first body",
    include_destination_section: bool = False,
):
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
            "title": source_page_title,
            "path": (
                "Source Notebook/Source Group/Inner Group/Notes/"
                f"{source_page_title}"
            ),
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
    if include_destination_section:
        state.append(
            {
                "resource_type": "section",
                "id": "destination-section",
                "name": "Destination",
                "path": "Destination Notebook/Destination",
                "parent_id": "destination-notebook",
                "notebook_id": "destination-notebook",
            }
        )
    xml_store = {
        "source-page": page_xml(
            "source-page",
            source_page_title,
            source_page_body,
        )
    }
    if duplicate_page_titles:
        duplicate = {
            "resource_type": "page",
            "id": "source-page-2",
            "title": source_page_title,
            "path": (
                "Source Notebook/Source Group/Inner Group/Notes/"
                f"{source_page_title}"
            ),
            "parent_id": "source-section",
            "notebook_id": "source-notebook",
            "section_id": "source-section",
            "parent_page_id": None,
            "page_level": 1,
            "order": 1,
        }
        state.insert(-1, duplicate)
        xml_store["source-page-2"] = page_xml(
            "source-page-2", source_page_title, "second body"
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

    def create_notebook(name, base_folder, **_kwargs):
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

    def create_group(parent_id, name, **_kwargs):
        item = append_container("section_group", parent_id, name)
        return {"section_group": item, "allocated_id": item["id"]}

    def create_section(parent_id, name, **_kwargs):
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
        from local_onenote_mcp.services.backend_operation_classification import (
            notify_backend_operation,
        )

        notify_backend_operation(operation)
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

    monkeypatch.setattr(
        server.services.hierarchy,
        "hierarchy_xml",
        lambda start_id="", scope="pages": hierarchy_xml_from_items(state),
    )
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
    assert copy_verification_tier(["Outline", "MediaFile"]) == "strict_canonical"


def test_semantic_content_accepts_title_text_node_merge_on_image_page():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:Title><one:OE><one:T>Alpha</one:T><one:T>Beta</one:T></one:OE></one:Title>
    <one:Image format="png"><one:Data>YWJj</one:Data></one:Image></one:Page>"""
    target = source.replace('ID="source"', 'ID="target"').replace(
        "<one:T>Alpha</one:T><one:T>Beta</one:T>", "<one:T>AlphaBeta</one:T>"
    )
    tier = copy_verification_tier(
        ["Image"],
        page_xml=source,
    )

    result = page_equivalence(source, target, verification_tier=tier)

    assert tier == "semantic_content_v1"
    assert result["checks"]["canonical_xml"] is False
    assert result["checks"]["visible_text"] is False
    assert result["semantic_content_comparison"]["checks"]["title"] is True
    assert result["equivalent"] is True


def test_semantic_content_accepts_empty_outline_elimination_on_list_table_page():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>
    <one:Outline><one:Position x="1" y="2"/><one:OEChildren><one:OE/></one:OEChildren></one:Outline>
    <one:Outline><one:OEChildren><one:OE><one:List><one:Bullet/></one:List><one:T>Item</one:T>
      <one:Table><one:Columns><one:Column index="0" width="100"/></one:Columns><one:Row><one:Cell><one:OEChildren><one:OE><one:T>Cell</one:T></one:OE>
      </one:OEChildren></one:Cell></one:Row></one:Table>
    </one:OE></one:OEChildren></one:Outline></one:Page>"""
    target = source.replace('ID="source"', 'ID="target"').replace(
        '<one:Outline><one:Position x="1" y="2"/><one:OEChildren><one:OE/></one:OEChildren></one:Outline>',
        "",
    )
    tier = copy_verification_tier(
        ["Outline", "List", "Table"],
        page_xml=source,
    )

    result = page_equivalence(source, target, verification_tier=tier)

    assert tier == "semantic_content_v1"
    assert result["checks"]["canonical_xml"] is False
    assert result["checks"]["content_objects"] is False
    assert result["equivalent"] is True


def test_semantic_content_accepts_table_cell_oe_flattening():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:Outline><one:OEChildren><one:OE><one:List><one:Number/></one:List><one:T>Item</one:T>
      <one:Table><one:Columns><one:Column index="0" width="100"/></one:Columns><one:Row><one:Cell><one:OEChildren>
        <one:OE><one:T><![CDATA[<strong>A</strong>]]></one:T></one:OE>
        <one:OE><one:T><![CDATA[<strong>B</strong>]]></one:T></one:OE>
      </one:OEChildren></one:Cell></one:Row></one:Table>
    </one:OE></one:OEChildren></one:Outline></one:Page>"""
    target = source.replace('ID="source"', 'ID="target"').replace(
        "<one:OE><one:T><![CDATA[<strong>A</strong>]]></one:T></one:OE>\n        "
        "<one:OE><one:T><![CDATA[<strong>B</strong>]]></one:T></one:OE>",
        "<one:OE><one:T><![CDATA[<strong>AB</strong>]]></one:T></one:OE>",
    )
    tier = copy_verification_tier(
        ["Outline", "RichText", "List", "Table"],
        page_xml=source,
    )

    result = page_equivalence(source, target, verification_tier=tier)

    assert tier == "semantic_content_v1"
    assert result["checks"]["canonical_xml"] is False
    assert result["equivalent"] is True


def test_semantic_content_accepts_redundant_nested_span_collapse():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:Outline><one:OEChildren><one:OE><one:Table><one:Columns><one:Column index="0" width="100"/></one:Columns><one:Row><one:Cell>
      <one:OEChildren><one:OE><one:T><![CDATA[
        <span style="font-family:Calibri;color:#ff0000"><span style="color:#ff0000">Cell</span></span>
      ]]></one:T></one:OE></one:OEChildren>
    </one:Cell></one:Row></one:Table></one:OE></one:OEChildren></one:Outline></one:Page>"""
    target = source.replace('ID="source"', 'ID="target"').replace(
        '<span style="font-family:Calibri;color:#ff0000"><span style="color:#ff0000">Cell</span></span>',
        '<span style="color:#ff0000; font-family:Calibri">Cell</span>',
    )

    result = page_equivalence(
        source,
        target,
        verification_tier="semantic_content_v1",
    )

    assert result["checks"]["canonical_xml"] is False
    assert result["semantic_content_comparison"]["checks"][
        "rich_list_tag_table_outline"
    ] is True
    assert result["equivalent"] is True


def test_semantic_content_effective_span_projection_still_rejects_style_loss():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:Outline><one:OEChildren><one:OE><one:Table><one:Columns><one:Column index="0" width="100"/></one:Columns><one:Row><one:Cell>
      <one:OEChildren><one:OE><one:T><![CDATA[
        <span style="font-family:Calibri"><span style="color:#ff0000">Cell</span></span>
      ]]></one:T></one:OE></one:OEChildren>
    </one:Cell></one:Row></one:Table></one:OE></one:OEChildren></one:Outline></one:Page>"""
    target = source.replace('ID="source"', 'ID="target"').replace(
        '<span style="font-family:Calibri"><span style="color:#ff0000">Cell</span></span>',
        '<span style="font-family:Calibri">Cell</span>',
    )

    result = page_equivalence(
        source,
        target,
        verification_tier="semantic_content_v1",
    )

    assert result["semantic_content_comparison"]["checks"][
        "rich_list_tag_table_outline"
    ] is False
    assert result["equivalent"] is False


def test_semantic_content_accepts_whitespace_moved_between_formatted_runs():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:Outline><one:OEChildren><one:OE><one:T><![CDATA[
      <strong>Bold </strong><em>Italic</em>
    ]]></one:T></one:OE></one:OEChildren></one:Outline></one:Page>"""
    target = source.replace('ID="source"', 'ID="target"').replace(
        "<strong>Bold </strong><em>Italic</em>",
        "<strong>Bold</strong><em> Italic</em>",
    )

    result = page_equivalence(
        source,
        target,
        verification_tier="semantic_content_v1",
    )

    assert result["checks"]["visible_text"] is True
    assert result["semantic_content_comparison"]["checks"][
        "rich_list_tag_table_outline"
    ] is True
    assert result["equivalent"] is True


def test_semantic_content_rejects_non_whitespace_moved_between_formatted_runs():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:Outline><one:OEChildren><one:OE><one:T><![CDATA[
      <strong>Bold </strong><em>Italic</em>
    ]]></one:T></one:OE></one:OEChildren></one:Outline></one:Page>"""
    target = source.replace('ID="source"', 'ID="target"').replace(
        "<strong>Bold </strong><em>Italic</em>",
        "<strong>Bold I</strong><em>talic</em>",
    )

    result = page_equivalence(
        source,
        target,
        verification_tier="semantic_content_v1",
    )

    assert result["checks"]["visible_text"] is True
    assert result["semantic_content_comparison"]["checks"][
        "rich_list_tag_table_outline"
    ] is False
    assert result["equivalent"] is False


def test_semantic_content_mismatch_evidence_is_bounded_and_content_free():
    source = page_xml(
        "source",
        "Private source title",
        '<span style="color:#ff0000"><a href="https://secret.invalid/a">Sensitive body</a></span>',
    ).replace(
        "</one:OE></one:OEChildren></one:Outline>",
        "<one:Table><one:Columns><one:Column index=\"0\" width=\"100\"/></one:Columns><one:Row><one:Cell><one:OEChildren><one:OE>"
        "<one:T>Private cell</one:T></one:OE></one:OEChildren></one:Cell>"
        "</one:Row></one:Table></one:OE></one:OEChildren></one:Outline>",
    )
    target = source.replace('ID="source"', 'ID="target"').replace(
        "#ff0000", "#0000ff"
    )

    comparison = semantic_content_comparison(source, target)
    serialized = str(comparison)

    assert comparison["passed"] is False
    evidence = comparison["projection_evidence"]
    assert evidence["content_exposed"] is False
    assert evidence["mismatches"]["reported"] >= 1
    assert evidence["mismatches"]["reported"] <= evidence["mismatches"]["limit"]
    assert all("path" in item and "kind" in item for item in evidence["mismatches"]["items"])
    assert "Private source title" not in serialized
    assert "Sensitive body" not in serialized
    assert "Private cell" not in serialized
    assert "secret.invalid" not in serialized


@pytest.mark.parametrize(
    "change",
    [
        lambda xml: xml.replace("<one:T>Title</one:T>", "<one:T>Changed</one:T>"),
        lambda xml: xml.replace(
            '<strong><a href="https://example.com/a">Cell</a></strong>',
            '<a href="https://example.com/a">Cell</a>',
        ),
        lambda xml: xml.replace("https://example.com/a", "https://example.com/b"),
        lambda xml: xml.replace("<one:Cell>", '<one:Cell shadingColor="#ffffff">'),
        lambda xml: xml.replace("<one:T>Second</one:T>", ""),
        lambda xml: xml.replace("YWJj", "ZGVm"),
    ],
)
def test_semantic_content_rejects_meaningful_title_rich_outline_or_binary_loss(change):
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>
    <one:Outline><one:OEChildren><one:OE><one:List><one:Bullet/></one:List>
      <one:Table><one:Columns><one:Column index="0" width="100"/></one:Columns><one:Row><one:Cell><one:OEChildren><one:OE>
        <one:T><![CDATA[<strong><a href="https://example.com/a">Cell</a></strong>]]></one:T>
      </one:OE></one:OEChildren></one:Cell></one:Row></one:Table>
    </one:OE></one:OEChildren></one:Outline>
    <one:Outline><one:OEChildren><one:OE><one:T>Second</one:T></one:OE></one:OEChildren></one:Outline>
    <one:Image format="png"><one:Data>YWJj</one:Data></one:Image></one:Page>"""
    target = change(source.replace('ID="source"', 'ID="target"'))
    tier = copy_verification_tier(
        ["Image", "List", "Outline", "RichText", "Table"],
        page_xml=source,
    )

    result = page_equivalence(source, target, verification_tier=tier)

    assert tier == "semantic_content_v1"
    assert result["equivalent"] is False


def test_semantic_content_incomplete_inline_projection_falls_back_to_strict():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:Outline><one:OEChildren><one:OE><one:Table><one:Columns><one:Column index="0" width="100"/></one:Columns><one:Row><one:Cell>
      <one:OEChildren><one:OE><one:T><![CDATA[<mark>Cell</mark>]]></one:T></one:OE></one:OEChildren>
    </one:Cell></one:Row></one:Table></one:OE></one:OEChildren></one:Outline></one:Page>"""
    same = source.replace('ID="source"', 'ID="target"')
    changed = same.replace("<mark>Cell</mark>", "Cell")

    accepted = page_equivalence(
        source,
        same,
        verification_tier="semantic_content_v1",
    )
    rejected = page_equivalence(
        source,
        changed,
        verification_tier="semantic_content_v1",
    )

    assert accepted["checks"]["semantic_projection_complete"] is False
    assert accepted["checks"]["semantic_fallback_strict"] is True
    assert accepted["equivalent"] is True
    assert rejected["checks"]["semantic_fallback_strict"] is False
    assert rejected["equivalent"] is False
    assert "Unknown" in rejected["failed_content_object_types"]
    assert any(
        failure["code"] == "semantic_projection_incomplete"
        and failure["content_object_type"] == "Unknown"
        and failure["content_exposed"] is False
        for failure in rejected["content_object_failures"]
    )


def test_semantic_content_tier_selection_rejects_unknown_page_structure():
    source = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:Outline><one:OEChildren><one:OE><one:Table/><one:FutureWidget/></one:OE>
    </one:OEChildren></one:Outline></one:Page>"""

    assert (
        copy_verification_tier(["Outline", "Table"], page_xml=source)
        == "strict_canonical"
    )


def _table_width_page(width: str | None, *, cell: str = "Cell") -> str:
    width_attribute = "" if width is None else f' width="{width}"'
    return f'''<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="page">
    <one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>
    <one:Outline><one:OEChildren><one:OE><one:Table><one:Columns>
      <one:Column index="0"{width_attribute}/></one:Columns><one:Row><one:Cell>
      <one:OEChildren><one:OE><one:T>{cell}</one:T></one:OE></one:OEChildren>
    </one:Cell></one:Row></one:Table></one:OE></one:OEChildren></one:Outline></one:Page>'''


@pytest.mark.parametrize("target_width", ["95", "105", "100.0", "1.05E+2"])
def test_semantic_table_width_accepts_numeric_delta_at_or_below_five_percent(
    target_width,
):
    expected = _table_width_page("100")
    actual = _table_width_page(target_width).replace('ID="page"', 'ID="target"')

    result = page_equivalence(
        expected,
        actual,
        verification_tier="semantic_content_v1",
    )

    assert result["equivalent"] is True
    assert result["failed_content_object_types"] == []
    if target_width != "100":
        evidence = result["semantic_content_comparison"][
            "table_column_width_comparisons"
        ][0]
        assert evidence["allowed_relative_delta"] == 0.05
        assert evidence["passed"] is True
        assert evidence["content_exposed"] is False


@pytest.mark.parametrize("target_width", ["94.999", "105.001"])
def test_semantic_table_width_rejects_delta_above_five_percent(target_width):
    result = page_equivalence(
        _table_width_page("100"),
        _table_width_page(target_width).replace('ID="page"', 'ID="target"'),
        verification_tier="semantic_content_v1",
    )

    assert result["equivalent"] is False
    assert result["failed_content_object_types"] == ["Table"]
    failure = result["content_object_failures"][0]
    assert failure["code"] == "table_column_width_out_of_tolerance"
    assert failure["content_object_type"] == "Table"
    assert failure["component_type"] == "Column"
    assert failure["field"] == "width"
    assert failure["table_ordinal"] == 0
    assert failure["column_ordinal"] == 0
    assert failure["observed_relative_delta"] > 0.05


@pytest.mark.parametrize("target_width", [None, "0", "-1", "NaN", "Infinity", "not-a-number"])
def test_semantic_table_width_invalid_values_fail_closed(target_width):
    result = page_equivalence(
        _table_width_page("100"),
        _table_width_page(target_width).replace('ID="page"', 'ID="target"'),
        verification_tier="semantic_content_v1",
    )

    assert result["equivalent"] is False
    assert result["content_object_failures"][0]["code"] == "table_column_width_invalid"


@pytest.mark.parametrize("width", [None, "0", "NaN", "Infinity"])
def test_semantic_table_width_invalid_equal_values_still_fail_closed(width):
    result = page_equivalence(
        _table_width_page(width),
        _table_width_page(width).replace('ID="page"', 'ID="target"'),
        verification_tier="semantic_content_v1",
    )

    assert result["equivalent"] is False
    assert result["content_object_failures"][0]["code"] == "table_column_width_invalid"


def test_semantic_table_width_rejects_missing_column_mapping():
    no_columns = _table_width_page("100").replace(
        '<one:Columns>\n      <one:Column index="0" width="100"/></one:Columns>',
        "",
    )
    result = page_equivalence(
        _table_width_page("100"),
        no_columns.replace('ID="page"', 'ID="target"'),
        verification_tier="semantic_content_v1",
    )

    assert result["equivalent"] is False
    assert any(
        failure["code"] == "table_column_mapping_unavailable"
        for failure in result["content_object_failures"]
    )


def test_source_to_transformed_table_width_remains_exact():
    comparison = semantic_content_comparison(
        _table_width_page("100"),
        _table_width_page("100.0").replace('ID="page"', 'ID="target"'),
    )

    assert comparison["passed"] is False
    assert comparison["content_object_failures"][0]["code"] == "table_column_width_mismatch"
    assert comparison["content_object_failures"][0]["comparison"] == "exact"


def test_table_width_tolerance_does_not_hide_cell_or_topology_changes():
    expected = _table_width_page("100")
    cell_changed = page_equivalence(
        expected,
        _table_width_page("104", cell="Changed").replace('ID="page"', 'ID="target"'),
        verification_tier="semantic_content_v1",
    )
    topology_changed = page_equivalence(
        expected,
        _table_width_page("104")
        .replace("</one:Columns>", '<one:Column index="1" width="50"/></one:Columns>')
        .replace('ID="page"', 'ID="target"'),
        verification_tier="semantic_content_v1",
    )

    assert cell_changed["equivalent"] is False
    assert any(
        failure["code"] == "table_cell_content_mismatch"
        for failure in cell_changed["content_object_failures"]
    )
    assert topology_changed["equivalent"] is False
    assert any(
        failure["code"] == "table_column_attribute_mismatch"
        for failure in topology_changed["content_object_failures"]
    )


def test_pure_rich_text_page_uses_complete_semantic_tier():
    source = page_xml("source", "Title", "<strong>Body</strong>")

    tier = copy_verification_tier(
        ["Outline", "RichText"],
        page_xml=source,
    )

    assert tier == "semantic_content_v1"
    assert page_equivalence(
        source,
        source.replace('ID="source"', 'ID="target"'),
        verification_tier=tier,
    )["equivalent"] is True
    assert (
        copy_verification_tier(
            ["Outline"],
            page_xml=page_xml("source", "Title", "Plain body"),
        )
        == "strict_canonical"
    )


@pytest.mark.parametrize(
    ("change", "object_type", "code"),
    [
        (lambda xml: xml.replace("<one:T>Title</one:T>", "<one:T>Other</one:T>"), "PageTitle", "page_title_mismatch"),
        (lambda xml: xml.replace("<strong>Body</strong>", "Body"), "RichText", "rich_text_effective_style_mismatch"),
        (lambda xml: xml.replace("<one:Bullet/>", "<one:Number/>"), "List", "list_marker_mismatch"),
        (lambda xml: xml.replace('completed="false"', 'completed="true"'), "Tag", "tag_state_mismatch"),
        (lambda xml: xml.replace("</one:Page>", "<one:Outline><one:OEChildren><one:OE><one:T>Extra</one:T></one:OE></one:OEChildren></one:Outline></one:Page>"), "Outline", "outline_structure_mismatch"),
        (lambda xml: xml.replace("YWJj", "ZGVm"), "Image", "image_binary_mismatch"),
    ],
)
def test_semantic_failures_are_typed_and_content_free(change, object_type, code):
    source = '''<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:TagDef index="0" type="0" symbol="3"/><one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>
    <one:Outline><one:OEChildren><one:OE><one:List><one:Bullet/></one:List>
      <one:Tag index="0" completed="false" disabled="false"/><one:T><![CDATA[<strong>Body</strong>]]></one:T>
    </one:OE></one:OEChildren></one:Outline><one:Image><one:Data>YWJj</one:Data></one:Image></one:Page>'''
    actual = change(source.replace('ID="source"', 'ID="target"'))

    result = page_equivalence(source, actual, verification_tier="semantic_content_v1")

    assert result["equivalent"] is False
    assert object_type in result["failed_content_object_types"]
    matching = [
        failure
        for failure in result["content_object_failures"]
        if failure["content_object_type"] == object_type
    ]
    assert any(failure["code"] == code for failure in matching)
    assert all(failure["content_exposed"] is False for failure in matching)


def test_semantic_failures_report_multiple_types_in_stable_content_free_order():
    source = '''<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:TagDef index="0" type="0" symbol="3"/><one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>
    <one:Outline><one:OEChildren><one:OE><one:List><one:Bullet/></one:List>
      <one:Tag index="0" completed="false" disabled="false"/><one:T><![CDATA[<strong>Body</strong>]]></one:T>
    </one:OE></one:OEChildren></one:Outline><one:Image><one:Data>YWJj</one:Data></one:Image></one:Page>'''
    actual = (
        source.replace('ID="source"', 'ID="target"')
        .replace("<one:T>Title</one:T>", "<one:T>Other</one:T>")
        .replace("<strong>Body</strong>", "Body")
        .replace("<one:Bullet/>", "<one:Number/>")
        .replace('completed="false"', 'completed="true"')
        .replace("YWJj", "ZGVm")
    )

    result = page_equivalence(source, actual, verification_tier="semantic_content_v1")
    failures = result["content_object_failures"]

    assert result["equivalent"] is False
    assert result["failed_content_object_types"] == [
        "Image",
        "List",
        "PageTitle",
        "RichText",
        "Tag",
    ]
    assert failures == sorted(
        failures,
        key=lambda failure: (
            failure["path"],
            failure["content_object_type"],
            failure["code"],
        ),
    )
    assert len(
        {
            (failure["path"], failure["content_object_type"], failure["code"])
            for failure in failures
        }
    ) == len(failures)
    assert all(failure["content_exposed"] is False for failure in failures)


@pytest.mark.parametrize(
    ("content_type", "exception_name", "readback_error_code"),
    [
        ("PageTitle", "PageTitleReadbackMismatch", "page_title_readback_mismatch"),
        ("RichText", "PageRichTextReadbackMismatch", "page_rich_text_readback_mismatch"),
        ("List", "PageListReadbackMismatch", "page_list_readback_mismatch"),
        ("Tag", "PageTagReadbackMismatch", "page_tag_readback_mismatch"),
        ("Table", "PageTableReadbackMismatch", "page_table_readback_mismatch"),
        ("Outline", "PageOutlineReadbackMismatch", "page_outline_readback_mismatch"),
        ("Image", "PageImageReadbackMismatch", "page_image_readback_mismatch"),
        (
            "InsertedFile",
            "PageInsertedFileReadbackMismatch",
            "page_inserted_file_readback_mismatch",
        ),
        (
            "FileAttachment",
            "PageFileAttachmentReadbackMismatch",
            "page_file_attachment_readback_mismatch",
        ),
        (
            "MediaFile",
            "PageMediaFileReadbackMismatch",
            "page_media_file_readback_mismatch",
        ),
        (
            "DisplayEquation",
            "PageDisplayEquationReadbackMismatch",
            "page_display_equation_readback_mismatch",
        ),
        (
            "InkDrawing",
            "PageInkDrawingReadbackMismatch",
            "page_ink_drawing_readback_mismatch",
        ),
        ("UIShape", "PageUIShapeReadbackMismatch", "page_ui_shape_readback_mismatch"),
        (
            "Unknown",
            "PageUnknownContentReadbackMismatch",
            "page_unknown_content_readback_mismatch",
        ),
    ],
)
def test_page_readback_mismatch_factory_raises_typed_content_category(
    content_type,
    exception_name,
    readback_error_code,
):
    exc = page_readback_mismatch_error(
        "Page read-back mismatch.",
        [content_type],
        partial=True,
    )
    envelope = caught(exc)

    assert isinstance(exc, PageReadbackMismatch)
    assert type(exc).__name__ == exception_name
    assert exc.details["failed_content_object_types"] == [content_type]
    assert exc.details["readback_content_category"] == content_type
    assert exc.details["readback_error_code"] == readback_error_code
    assert exc.details["content_exposed"] is False
    assert envelope["error"]["code"] == "partial_failure"
    assert envelope["error"]["details"]["error_type"] == exception_name


def test_page_readback_mismatch_factory_types_mixed_and_unclassified_failures():
    mixed = page_readback_mismatch_error("mixed", ["Table", "RichText", "Table"])
    unclassified = page_readback_mismatch_error("unclassified", [])

    assert isinstance(mixed, PageMixedContentReadbackMismatch)
    assert mixed.details["failed_content_object_types"] == ["RichText", "Table"]
    assert mixed.details["readback_content_category"] == "Mixed"
    assert isinstance(unclassified, PageUnknownContentReadbackMismatch)
    assert unclassified.details["failed_content_object_types"] == ["Unknown"]
    assert unclassified.details["readback_content_category"] == "Unknown"


def test_strict_binary_failure_identifies_verified_non_image_object_type():
    source = '''<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:MediaFile><one:Data>YWJj</one:Data></one:MediaFile></one:Page>'''
    actual = source.replace('ID="source"', 'ID="target"').replace("YWJj", "ZGVm")

    result = page_equivalence(source, actual)

    assert result["equivalent"] is False
    assert "MediaFile" in result["failed_content_object_types"]
    assert any(
        failure["content_object_type"] == "MediaFile"
        and failure["code"] == "binary_object_mismatch"
        for failure in result["content_object_failures"]
    )


def test_strict_typed_failures_are_stably_sorted_across_object_types():
    source = '''<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">
    <one:Title><one:OE><one:T>Title</one:T></one:OE></one:Title>
    <one:MediaFile><one:Data>YWJj</one:Data></one:MediaFile></one:Page>'''
    actual = source.replace('ID="source"', 'ID="target"').replace(
        "<one:T>Title</one:T>",
        "<one:T>Other</one:T>",
    ).replace("<one:MediaFile><one:Data>YWJj</one:Data></one:MediaFile>", "")

    result = page_equivalence(source, actual)
    failures = result["content_object_failures"]

    assert result["failed_content_object_types"] == ["MediaFile", "PageTitle"]
    assert failures == sorted(
        failures,
        key=lambda failure: (
            failure["path"],
            failure["content_object_type"],
            failure["code"],
        ),
    )
    assert all(failure["content_exposed"] is False for failure in failures)


def test_semantic_failure_list_is_stably_sorted_deduplicated_and_bounded():
    outlines = "".join(
        f"<one:Outline><one:OEChildren><one:OE><one:T>Item {index}</one:T></one:OE></one:OEChildren></one:Outline>"
        for index in range(30)
    )
    source = (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="source">'
        + outlines
        + "</one:Page>"
    )
    actual = source.replace('ID="source"', 'ID="target"')
    for index in range(30):
        actual = actual.replace(f"Item {index}", f"Changed {index}")

    result = page_equivalence(source, actual, verification_tier="semantic_content_v1")
    summary = result["content_object_failure_summary"]

    assert result["failed_content_object_types"] == sorted(
        set(result["failed_content_object_types"])
    )
    assert summary == {"limit": 24, "reported": 24, "truncated": True, "total": 30}
    assert len(result["content_object_failures"]) == 24


@pytest.mark.parametrize(
    ("capability", "shape_info", "delta", "expected_tier", "equivalent"),
    [
        ("InkDrawing", "", "0.00005", "semantic_ink_drawing", True),
        ("InkDrawing", "", "0.00011", "semantic_ink_drawing", False),
        ("UIShape", "<one:ShapeInfo/>", "0.016", "semantic_ui_shape", True),
        ("UIShape", "<one:ShapeInfo/>", "0.021", "semantic_ui_shape", False),
    ],
)
def test_validated_ink_copy_tiers_use_bounded_geometry_semantics(
    capability: str,
    shape_info: str,
    delta: str,
    expected_tier: str,
    equivalent: bool,
) -> None:
    source = page_xml("source", "Ink").replace(
        "</one:Page>",
        (
            '<one:InkDrawing objectID="source-ink"><one:Position x="1" y="2"/>'
            '<one:Size width="10" height="20"/>'
            f"{shape_info}<one:Ink>synthetic-data</one:Ink>"
            "</one:InkDrawing></one:Page>"
        ),
    )
    target = source.replace('ID="source"', 'ID="target"').replace(
        'objectID="source-ink"', 'objectID="target-ink"'
    ).replace('x="1"', f'x="{Decimal("1") + Decimal(delta)}"')
    tier = copy_verification_tier(["Outline", capability])
    transformed = transform_page_for_copy(source, "target", {"source": "target"})

    result = page_equivalence(source, target, verification_tier=tier)

    assert tier == expected_tier
    assert transformed["lossless_candidate"] is True
    assert not any(
        issue["code"] == "content_type_unverified"
        for issue in transformed["issues"]
    )
    assert result["checks"]["canonical_xml"] is False
    assert result["equivalent"] is equivalent
    comparison = result["ink_projection_comparison"]
    assert comparison["geometry_within_tolerance"] is equivalent
    assert comparison["structure_and_data_equal"] is True


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


def test_plan_copy_ignores_volatile_raw_page_xml_but_exposes_its_digest(monkeypatch):
    state = install_plan_fakes(monkeypatch)
    reads = {"count": 0}

    def volatile_xml(page_id, page_info="basic"):
        reads["count"] += 1
        return page_xml(page_id, "Parent", state["body"]).replace(
            "<one:Outline",
            f'<one:Outline pathCache="cache-{reads["count"]}"',
        )

    monkeypatch.setattr(server.services.pages, "xml", volatile_xml)

    first = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))
    second = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))

    assert first["plan_digest"] == second["plan_digest"]
    assert first["source_snapshot_digest"] == second["source_snapshot_digest"]
    assert (
        first["snapshots"]["source"]["page_hashes"]
        == second["snapshots"]["source"]["page_hashes"]
    )
    assert (
        first["snapshots"]["source"]["page_xml_hashes"]
        != second["snapshots"]["source"]["page_xml_hashes"]
    )


def test_plan_copy_rejects_inserted_file_without_readable_path_before_mutation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    install_plan_fakes(monkeypatch)
    missing_source = tmp_path / "missing" / "synthetic.md"
    missing_cache = tmp_path / "missing" / "OneNote-cache.bin"

    def inserted_file_xml(page_id: str, page_info: str = "basic") -> str:
        del page_info
        return page_xml(page_id, "Parent", "placeholder").replace(
            "<one:T><![CDATA[placeholder]]></one:T>",
            (
                f'<one:InsertedFile pathCache="{missing_cache}" '
                f'pathSource="{missing_source}" preferredName="synthetic.md"/>'
            ),
        )

    monkeypatch.setattr(server.services.pages, "xml", inserted_file_xml)

    result = asyncio.run(
        plan_copy("parent", "destination-section", "Copied Parent")
    )

    assert result["ok"] is False
    assert result["code"] == "validation_error"
    assert result["complete"] is False
    assert result["error"] == (
        "InsertedFile Copy requires a readable local pathSource, pathCache, or path."
    )
    assert str(missing_source) not in result["error"]
    assert str(missing_cache) not in result["error"]


def test_plan_copy_still_changes_digest_when_authored_content_changes(monkeypatch):
    state = install_plan_fakes(monkeypatch)

    first = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))
    state["body"] = "Changed body"
    second = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))

    assert first["plan_digest"] != second["plan_digest"]
    assert first["source_snapshot_digest"] != second["source_snapshot_digest"]


def test_plan_copy_ignores_modified_clock_drift_but_preserves_observation(monkeypatch):
    state = install_plan_fakes(monkeypatch)

    first = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))
    for item in state["items"]:
        item["modified"] = "one-note-clock-drift"
    second = asyncio.run(plan_copy("parent", "destination-section", "Copied Parent"))

    assert first["plan_digest"] == second["plan_digest"]
    assert first["source_snapshot_digest"] == second["source_snapshot_digest"]
    assert (
        first["snapshots"]["source"]["resources"]
        != second["snapshots"]["source"]["resources"]
    )
    assert first["destination"] != second["destination"]


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


@pytest.mark.parametrize("planner", ["_inspect_copy_plan", "_inspect_move_page_plan"])
@pytest.mark.parametrize("explicit", [False, True])
def test_page_copy_and_move_titles_bypass_filesystem_leaf_cleaning(
    monkeypatch,
    planner,
    explicit,
):
    state = install_plan_fakes(monkeypatch)
    title = "Topic / Subtopic\\:  %~界"
    source = next(item for item in state["items"] if item["id"] == "parent")
    source["title"] = title
    source["path"] = f"Notebook/Source/{title}"

    args = ["parent", "destination-section"]
    if explicit:
        args.append(title)
    plan = getattr(server.services.copying, planner)(*args)

    assert plan["destination"]["name"] == title


def test_container_copy_destination_names_remain_filesystem_safe(monkeypatch):
    install_recursive_execute_fakes(monkeypatch)

    plan = server.services.copying._build_plan(
        "source-section",
        "destination-notebook",
        "A/B\\C: D",
    )

    assert plan["destination"]["name"] == "A B C D"


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


@pytest.mark.parametrize(
    ("planner", "args"),
    [
        ("_inspect_copy_plan", ("parent", "destination-section", "Copy")),
        ("_inspect_move_page_plan", ("parent", "destination-section", "Move")),
    ],
)
def test_page_plan_tools_do_not_predict_destination_position(monkeypatch, planner, args):
    install_plan_fakes(monkeypatch, body="")

    result = getattr(server.services.copying, planner)(*args)

    assert "destination_position" not in result


@pytest.mark.parametrize(
    ("source_id", "planner"),
    [
        ("source-container-section", "_inspect_move_section_plan"),
        ("source-group", "_inspect_move_section_group_plan"),
    ],
)
def test_container_move_plans_do_not_predict_destination_position(
    monkeypatch, source_id, planner
):
    items = container_move_items()
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: items,
    )
    monkeypatch.setattr(
        server.services.pages,
        "xml",
        lambda page_id, page_info="basic": page_xml(page_id, "Source Page", "Body"),
    )

    result = getattr(server.services.copying, planner)(
        source_id, "destination-notebook", "Moved"
    )

    assert "destination_position" not in result


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
def test_copy_rebuilds_plan_from_live_source_inside_single_call(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="Before")
    prior = server.services.copying._build_plan(
        "parent", "destination-section", "Copied Parent"
    )
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.pages, "confirm", lambda *args, **kwargs: {})
    observed = {}
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda plan: observed.update(plan) or {"warnings": []},
    )
    state["body"] = "After"

    result = asyncio.run(
        copy_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            destination_title="Copied Parent",
        )
    )

    assert result["ok"] is True
    assert observed["plan_digest"] != prior["plan_digest"]
    assert observed["page_xml"]["parent"] == page_xml("parent", "Parent", "After")


@pytest.mark.write_contract
def test_copy_notebook_allows_modified_clock_drift_when_semantic_plan_matches(monkeypatch):
    confirmations: list[tuple] = []
    executions: list[str] = []
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(
        server.services.copying,
        "_confirm_source",
        lambda *args: confirmations.append(args),
    )
    monkeypatch.setattr(
        server.services.copying,
        "_build_plan",
        lambda *args, **kwargs: {
            "plan_digest": "semantic-plan",
            "source": {
                "id": "source-notebook",
                "resource_type": "notebook",
                "name": "Source Notebook",
                "parent_id": None,
                "modified": "one-note-clock-drift",
            },
        },
    )
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda plan: executions.append(plan["plan_digest"])
        or {"item": {"id": "copied-notebook"}, "warnings": []},
    )

    result = server.services.copying.copy_resource(
        "source-notebook",
        "notebook",
        "",
        "Notebook Copy",
        "C:/validation",
        "Source Notebook",
        None,
        "planned-clock",
    )

    assert confirmations == [
        ("source-notebook", "notebook", "Source Notebook", None, None)
    ]
    assert executions == ["semantic-plan"]
    assert result["item"]["id"] == "copied-notebook"
    assert any("modified timestamps" in warning for warning in result["warnings"])


@pytest.mark.write_contract
def test_copy_rebuilds_plan_from_live_destination_inside_single_call(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.pages, "confirm", lambda *args, **kwargs: {})
    observed = {}
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda plan: observed.update(plan) or {"warnings": []},
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
            destination_title="Copied Parent",
        )
    )

    assert result["ok"] is True
    assert [
        item["id"] for item in observed["destination"]["existing_children"]
    ] == ["new-destination-child"]


@pytest.mark.write_contract
@pytest.mark.parametrize("include_descendants", [False, True])
def test_copy_binds_requested_scope_in_internal_plan(monkeypatch, include_descendants):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.pages, "confirm", lambda *args, **kwargs: {})
    observed = {}
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda plan: observed.update(plan) or {"warnings": []},
    )

    result = asyncio.run(
        copy_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            destination_title="Copied Parent",
            include_subpages=include_descendants,
        )
    )

    assert result["ok"] is True
    assert observed["include_descendants"] is include_descendants
    expected_ids = ["parent", "child"] if include_descendants else ["parent"]
    assert [item["id"] for item in observed["resources"]] == expected_ids


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
    assert caught.value.details["destination_position"] == {
        "status": "unavailable",
        "resource_type": "page",
        "reason": "destination_target_not_uniquely_observed",
    }
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
                "title": "Filesystem cleaned title",
                "section_id": "destination-section",
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
    state = install_recursive_execute_fakes(monkeypatch)
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
    assert result["copy_report"]["fidelity"] == "lossless"
    assert result["copy_report"]["copy_contract_satisfied"] is True
    assert result["copy_report"]["copied_counts"] == {"resources": 4, "pages": 1}
    assert_destination_position_contract(result, state, result["item"]["id"])


@pytest.mark.write_contract
def test_recursive_section_copy_executes_and_verifies(monkeypatch):
    state = install_recursive_execute_fakes(monkeypatch)
    plan = server.services.copying._build_plan(
        "source-section", "destination-notebook", "Section Copy"
    )

    result = server.services.copying._execute_copy(plan)

    assert list(result["copy_report"]["id_map"]) == ["source-section", "source-page"]
    assert result["item"]["name"] == "Section Copy"
    assert result["copy_report"]["verified"] is True
    assert result["copy_report"]["lossless"] is True
    assert_destination_position_contract(result, state, result["item"]["id"])


@pytest.mark.write_contract
def test_copy_report_exposes_content_free_semantic_stage_diagnostics(monkeypatch):
    install_recursive_execute_fakes(monkeypatch)
    monkeypatch.setattr(
        "local_onenote_mcp.services.copying.copy_verification_tier",
        lambda *_args, **_kwargs: "semantic_content_v1",
    )
    plan = server.services.copying._build_plan(
        "source-section", "destination-notebook", "Section Copy"
    )

    result = server.services.copying._execute_copy(plan)

    page_result = result["copy_report"]["page_results"][0]
    stages = page_result["semantic_content_stages"]
    title_stages = page_result["title_readback_stages"]
    assert stages["schema_version"] == 1
    assert stages["title_override_requested"] is False
    assert stages["source_to_transformed"]["passed"] is True
    assert stages["transformed_to_target"]["passed"] is True
    assert stages["content_exposed"] is False
    assert stages["source_to_transformed"]["projection_evidence"][
        "content_exposed"
    ] is False
    assert title_stages["schema_version"] == 1
    assert title_stages["title_override_requested"] is False
    assert title_stages["source_to_transformed"]["checks"]["title"] is True
    assert title_stages["transformed_to_target"]["checks"]["title"] is True
    assert title_stages["content_exposed"] is False


@pytest.mark.write_contract
def test_copy_raises_typed_exception_for_page_readback_content_category(monkeypatch):
    install_recursive_execute_fakes(
        monkeypatch,
        source_page_body="<strong>Body</strong>",
        include_destination_section=True,
    )
    monkeypatch.setattr(
        "local_onenote_mcp.services.copying.page_equivalence",
        lambda *_args, verification_tier, **_kwargs: {
            "equivalent": False,
            "verification_tier": verification_tier,
            "checks": {"semantic_content": False},
            "failed_content_object_types": ["Table"],
            "content_object_failures": [
                {
                    "code": "table_cell_content_mismatch",
                    "content_object_type": "Table",
                    "path": "$.outlines[0].table",
                    "content_exposed": False,
                }
            ],
            "content_object_failure_summary": {
                "limit": 24,
                "reported": 1,
                "truncated": False,
                "total": 1,
            },
        },
    )
    plan = server.services.copying._build_plan(
        "source-page",
        "destination-section",
    )

    with pytest.raises(PageTableReadbackMismatch) as caught_error:
        server.services.copying._execute_copy(plan)

    assert caught_error.value.details["outcome"] == "copy_unverified"
    assert caught_error.value.details["failed_step"] == "verify_copy"
    assert caught_error.value.details["source_untouched"] is True
    assert caught_error.value.details["source_deleted"] is False
    assert caught_error.value.details["readback_content_category"] == "Table"
    assert caught_error.value.details["readback_error_code"] == (
        "page_table_readback_mismatch"
    )


@pytest.mark.write_contract
def test_title_readback_stages_apply_to_non_semantic_content_tier(monkeypatch):
    source_title = "Topic / Subtopic\\:  %~界"
    install_recursive_execute_fakes(
        monkeypatch,
        source_page_title=source_title,
        include_destination_section=True,
    )
    monkeypatch.setattr(
        "local_onenote_mcp.services.copying.copy_verification_tier",
        lambda *_args, **_kwargs: "semantic_display_equation",
    )
    monkeypatch.setattr(
        "local_onenote_mcp.services.copying.page_equivalence",
        lambda *_args, verification_tier, **_kwargs: {
            "equivalent": True,
            "verification_tier": verification_tier,
            "checks": {},
            "failed_content_object_types": [],
            "content_object_failures": [],
            "content_object_failure_summary": {
                "limit": 24,
                "reported": 0,
                "truncated": False,
                "total": 0,
            },
        },
    )
    plan = server.services.copying._build_plan(
        "source-page",
        "destination-section",
    )

    result = server.services.copying._execute_copy(plan)

    page_result = result["copy_report"]["page_results"][0]
    stages = page_result["title_readback_stages"]
    assert page_result["equivalence"]["verification_tier"] == (
        "semantic_display_equation"
    )
    assert "semantic_content_stages" not in page_result
    assert stages["title_override_requested"] is False
    assert stages["source_to_transformed"]["checks"] == {
        "title": True,
        "source_matches_metadata": True,
        "transformed_matches_expected": True,
        "default_title_preserved": True,
    }
    assert stages["source_to_transformed"]["passed"] is True
    assert stages["transformed_to_target"]["checks"]["title"] is True
    assert stages["transformed_to_target"]["passed"] is True
    assert stages["content_exposed"] is False
    assert page_result["lossless"] is True
    assert result["copy_report"]["copy_contract_satisfied"] is True


@pytest.mark.write_contract
@pytest.mark.parametrize(
    ("destination_title", "override_requested"),
    [
        ("", False),
        ("Renamed / Page\\:  %~文", True),
    ],
)
def test_page_copy_executes_exact_special_title_through_semantic_readback(
    monkeypatch,
    destination_title,
    override_requested,
):
    source_title = "Topic / Subtopic\\:  %~界"
    state = install_recursive_execute_fakes(
        monkeypatch,
        source_page_title=source_title,
        source_page_body="<strong>Body</strong>",
        include_destination_section=True,
    )
    plan = server.services.copying._build_plan(
        "source-page",
        "destination-section",
        destination_title,
    )

    result = server.services.copying._execute_copy(plan)

    expected_title = destination_title or source_title
    target = next(item for item in state if item["id"] == result["item"]["id"])
    stages = result["copy_report"]["page_results"][0][
        "semantic_content_stages"
    ]
    title_stages = result["copy_report"]["page_results"][0][
        "title_readback_stages"
    ]
    assert plan["destination"]["name"] == expected_title
    assert result["item"]["title"] == expected_title
    assert target["title"] == expected_title
    assert stages["title_override_requested"] is override_requested
    assert stages["source_to_transformed"]["checks"]["title"] is (
        not override_requested
    )
    assert stages["transformed_to_target"]["checks"]["title"] is True
    assert stages["transformed_to_target"]["passed"] is True
    assert title_stages["title_override_requested"] is override_requested
    assert title_stages["source_to_transformed"]["checks"]["title"] is True
    assert title_stages["transformed_to_target"]["checks"]["title"] is True
    assert title_stages["source_to_transformed"]["passed"] is True
    assert title_stages["transformed_to_target"]["passed"] is True
    assert result["copy_report"]["copy_contract_satisfied"] is True


@pytest.mark.write_contract
def test_recursive_notebook_copy_creates_new_root_and_verifies(monkeypatch, tmp_path):
    state = install_recursive_execute_fakes(monkeypatch)
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
    assert result["item"]["name"] == "Notebook Copy"
    assert result["destination_path"] == str(tmp_path / "Notebook Copy")
    assert result["copy_report"]["destination_path"] == str(tmp_path / "Notebook Copy")
    assert result["copy_report"]["verified"] is True
    assert result["copy_report"]["lossless"] is True
    assert_destination_position_contract(result, state, result["item"]["id"])


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

    def create_with_wrong_path(name, base_folder, **kwargs):
        result = original_create(name, base_folder, **kwargs)
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
    assert_destination_position_contract(result, state, result["item"]["id"])
    assert "page_level" not in result["destination_position"]
    assert created[0]["page_level"] == 1
    if include_descendants:
        assert created[1]["page_level"] == 2
        assert "#new-2" in xml_store["new-1"]
        assert "#child" not in xml_store["new-1"]
    else:
        assert len(created) == 1
        assert "#child" in xml_store["new-1"]


@pytest.mark.write_contract
def test_video_preview_player_marker_loss_fails_strict_copy_readback(monkeypatch):
    state = [
        item
        for item in hierarchy_items()
        if item.get("id") != "child"
    ]
    source_xml = page_xml("parent", "Preview").replace(
        "</one:Page>",
        (
            '<one:Outline><one:OEChildren><one:OE><one:Image format="png">'
            "<one:Data>c3ludGhldGlj</one:Data></one:Image></one:OE>"
            "<one:OE><one:T><![CDATA["
            '<a href="https://video.example.invalid/watch/synthetic" v="video">Link</a>'
            "]]></one:T></one:OE></one:OEChildren></one:Outline></one:Page>"
        ),
    )
    xml_store = {"parent": source_xml}
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
        "Copied Preview",
        include_descendants=False,
    )

    def create_page(section_id, title, *args, **kwargs):
        item = {
            "resource_type": "page",
            "id": "new-preview-page",
            "title": title,
            "path": f"Notebook/Destination/{title}",
            "parent_id": section_id,
            "notebook_id": "n",
            "section_id": section_id,
            "parent_page_id": None,
            "page_level": 1,
            "order": 0,
            "modified": "new",
        }
        state.append(item)
        xml_store[item["id"]] = page_xml(item["id"], title)
        return {"page": item, "allocated_id": item["id"]}

    def fake_call(operation, **params):
        root = ET.fromstring(params["xml"])
        if operation == "update_page_content":
            xml_store[root.attrib["ID"]] = params["xml"].replace(
                ' v="video"', ""
            )
            return {"updated": True}
        if operation == "update_hierarchy":
            pages = [
                node
                for node in root.iter()
                if node.tag.rsplit("}", 1)[-1] == "Page"
            ]
            for order, node in enumerate(pages):
                item = next(value for value in state if value["id"] == node.attrib["ID"])
                item["order"] = order
                item["page_level"] = int(node.attrib["pageLevel"])
            return {"updated": True}
        raise AssertionError(operation)

    monkeypatch.setattr(server.services.mutations, "create_page", create_page)
    monkeypatch.setattr(server.services.copying, "call", fake_call)

    with pytest.raises(PartialFailure) as raised:
        server.services.copying._execute_copy(plan)

    report = raised.value.details["copy_report"]
    expected_position = assert_destination_position_contract(
        {"destination_position": raised.value.details["destination_position"]},
        state,
        "new-preview-page",
    )
    assert expected_position["status"] == "observed"
    assert report["verified"] is False
    assert report["lossless"] is False
    assert report["fidelity"] == "unverified"
    assert report["copy_contract_satisfied"] is False
    assert report["issues"] == []
    assert report["page_results"][0]["content_types"] == [
        "Image",
        "Outline",
        "RichText",
    ]
    assert report["page_results"][0]["equivalence"]["verification_tier"] == (
        "strict_canonical"
    )


def test_move_page_scope_defaults_to_root_and_binds_preserved_descendants(monkeypatch):
    install_plan_fakes(monkeypatch, body="")

    root_only = server.services.copying._inspect_move_page_plan(
        "parent", "destination-section", "Moved Parent"
    )
    subtree = server.services.copying._inspect_move_page_plan(
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
@pytest.mark.parametrize("include_descendants", [False, True])
def test_move_page_binds_requested_scope_in_internal_plan(
    monkeypatch, include_descendants
):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    observed = {}
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda plan: observed.update(plan)
        or {
            "item": {"id": "target-parent"},
            "id_map": {"parent": "target-parent"},
            "copy_report": {"copy_contract_satisfied": False},
            "created_ids": ["target-parent"],
            "completed_steps": [],
            "warnings": [],
        },
    )

    with pytest.raises(PartialFailure) as raised:
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            destination_title="Moved Parent",
            include_descendants=include_descendants,
        )

    assert raised.value.details["outcome"] == "copy_only"
    assert observed["include_descendants"] is include_descendants


@pytest.mark.write_contract
def test_default_page_move_preserves_exact_special_title_through_shared_copy(
    monkeypatch,
):
    source_title = "Topic / Subtopic\\:  %~界"
    state = install_recursive_execute_fakes(
        monkeypatch,
        source_page_title=source_title,
        source_page_body="<strong>Body</strong>",
        include_destination_section=True,
    )
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(
        server.services.copying,
        "_confirm_source",
        lambda *args, **kwargs: None,
    )

    def delete_page(page_id, *args, **kwargs):
        remove_fake_items(state, page_id)
        advance_fake_mutation_epoch()
        return {
            "deleted": True,
            "final_state": {"id": page_id, "is_in_recycle_bin": True},
        }

    monkeypatch.setattr(server.services.mutations, "delete_page", delete_page)

    result = server.services.copying.move_page(
        "source-page",
        "destination-section",
        source_title,
        "source-section",
    )

    stages = result["copy_report"]["page_results"][0][
        "semantic_content_stages"
    ]
    assert result["outcome"] == "moved"
    assert result["item"]["title"] == source_title
    assert stages["title_override_requested"] is False
    assert stages["source_to_transformed"]["checks"]["title"] is True
    assert stages["transformed_to_target"]["checks"]["title"] is True
    assert result["copy_report"]["copy_contract_satisfied"] is True
    assert result["deleted_source_ids"] == ["source-page"]


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
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying._inspect_move_page_plan(
        "parent", "destination-section", "Moved Parent"
    )
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda value: state["items"].append(
            {
                "resource_type": "page",
                "id": "new-parent",
                "title": "Moved Parent",
                "parent_id": "destination-section",
                "section_id": "destination-section",
                "notebook_id": "n",
                "page_level": 1,
                "parent_page_id": None,
                "order": 0,
            }
        ) or {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent"],
            "copy_report": {
                "lossless": True,
                "verified": True,
                "copy_contract_satisfied": True,
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
            if item["id"] == "parent":
                item["modified"] = "after-promotion-clock"
            stack.append(item)
        state["xml_clock"] = "after-promotion"
        advance_fake_mutation_epoch()
        return {"updated": True}

    monkeypatch.setattr(server.services.copying, "call", update_hierarchy)
    delete_calls: list[dict[str, Any]] = []

    def delete_page(
        page_id,
        expected_title,
        expected_section_id,
        expected_modified,
        permanently,
        **kwargs,
    ):
        delete_calls.append(
            {
                "page_id": page_id,
                "expected_title": expected_title,
                "expected_section_id": expected_section_id,
                "expected_modified": expected_modified,
                "permanently": permanently,
            }
        )
        remove_fake_items(state, page_id)
        advance_fake_mutation_epoch()
        return {"deleted": True, "final_state": {"id": page_id, "is_in_recycle_bin": True}}

    monkeypatch.setattr(server.services.mutations, "delete_page", delete_page)

    result = server.services.copying.move_page(
        "parent",
        "destination-section",
        "Parent",
        "source-section",
        destination_title="Moved Parent",
    )

    child = next(item for item in state["items"] if item["id"] == "child")
    assert result["include_descendants"] is False
    assert result["deleted_source_ids"] == ["parent"]
    assert result["preserved_descendants"]["preserved_descendant_ids"] == ["child"]
    assert "source_root_modified" not in result["preserved_descendants"]
    assert "modified" not in result["preserved_descendants"]
    assert all(
        "modified" not in page_evidence
        for page_evidence in result["preserved_descendants"]["pages"].values()
    )
    assert delete_calls == [
        {
            "page_id": "parent",
            "expected_title": "Parent",
            "expected_section_id": "source-section",
            "expected_modified": "after-promotion-clock",
            "permanently": False,
        }
    ]
    assert child["page_level"] == 1
    assert child["parent_page_id"] is None
    assert_destination_position_contract(
        result,
        state["items"],
        result["item"]["id"],
    )
    assert "page_level" not in result["destination_position"]


@pytest.mark.write_contract
def test_root_only_move_blocks_delete_when_descendant_promotion_fails(monkeypatch):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying._inspect_move_page_plan(
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
                "copy_contract_satisfied": True,
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
            destination_title="Moved Parent",
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["source_topology_may_have_changed"] is True
    assert caught.value.details["preservation_error"] == "promotion failed"
    assert caught.value.details["destination_position"]["status"] == "unavailable"


@pytest.mark.write_contract
def test_move_page_degrades_to_copy_when_fidelity_is_unverified(monkeypatch):
    install_plan_fakes(monkeypatch)
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying._inspect_move_page_plan(
        "parent", "destination-section", "Moved Parent", True
    )
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda value: {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent"],
            "copy_report": {
                "lossless": False,
                "verified": True,
                "copy_contract_satisfied": False,
                "id_map": {"parent": "new-parent"},
            },
            "warnings": ["unverified"],
        },
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            destination_title="Moved Parent",
            include_descendants=True,
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["destination_position"]["status"] == "unavailable"


@pytest.mark.write_contract
def test_move_page_uses_shared_copy_contract_without_lossless_gate(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    state["items"] = [item for item in state["items"] if item["id"] != "child"]
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying._inspect_move_page_plan(
        "parent", "destination-section", "Moved Parent"
    )
    def execute_copy(_value):
        state["items"].append(
            {
                "resource_type": "page",
                "id": "new-parent",
                "title": "Moved Parent",
                "parent_id": "destination-section",
                "section_id": "destination-section",
                "notebook_id": "n",
                "page_level": 1,
                "parent_page_id": None,
                "order": 0,
            }
        )
        return {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent"],
            "copy_report": {
                "lossless": False,
                "verified": True,
                "fidelity": "unverified",
                "copy_contract_satisfied": True,
                "id_map": {"parent": "new-parent"},
            },
            "warnings": [],
        }

    monkeypatch.setattr(server.services.copying, "_execute_copy", execute_copy)
    deleted = []

    def delete_page(page_id, *args, **kwargs):
        deleted.append(page_id)
        state["items"] = [item for item in state["items"] if item["id"] != page_id]
        return {"deleted": True, "final_state": None}

    monkeypatch.setattr(server.services.mutations, "delete_page", delete_page)

    result = server.services.copying.move_page(
        "parent",
        "destination-section",
        "Parent",
        "source-section",
        destination_title="Moved Parent",
    )

    assert deleted == ["parent"]
    assert result["outcome"] == "moved"
    assert result["copy_report"]["copy_contract_satisfied"] is True
    assert result["destination_position"]["status"] == "observed"


@pytest.mark.write_contract
def test_move_page_same_section_recomputes_position_after_source_delete(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    state["items"] = [item for item in state["items"] if item["id"] != "child"]
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(
        server.services.copying, "_confirm_source", lambda *args, **kwargs: None
    )
    plan = server.services.copying._inspect_move_page_plan(
        "parent", "source-section", "Moved Parent"
    )
    copy_stage_position: dict = {}

    def execute_copy(_value):
        target = {
            "resource_type": "page",
            "id": "new-parent",
            "title": "Moved Parent",
            "parent_id": "source-section",
            "section_id": "source-section",
            "notebook_id": "n",
            "page_level": 1,
            "parent_page_id": None,
            "order": 3,
        }
        state["items"].append(target)
        copy_stage_position.update(
            assert_destination_position_contract(
                {
                    "destination_position": {
                        "status": "observed",
                        "resource_type": "page",
                        "parent_id": "source-section",
                        "parent_type": "section",
                        "sibling_scope": "section_page_sequence",
                        "index": 2,
                        "sibling_count": 3,
                        "sequence_source": "page_order",
                    }
                },
                state["items"],
                "new-parent",
            )
        )
        return {
            "item": target,
            "destination_position": dict(copy_stage_position),
            "created_ids": ["new-parent"],
            "copy_report": {
                "lossless": True,
                "verified": True,
                "copy_contract_satisfied": True,
                "id_map": {"parent": "new-parent"},
            },
            "warnings": [],
        }

    def delete_page(page_id, *_args, **_kwargs):
        assert page_id == "parent"
        remove_fake_items(state, page_id)
        advance_fake_mutation_epoch()
        return {"deleted": True, "final_state": None}

    monkeypatch.setattr(server.services.copying, "_execute_copy", execute_copy)
    monkeypatch.setattr(server.services.mutations, "delete_page", delete_page)

    result = server.services.copying.move_page(
        "parent",
        "source-section",
        "Parent",
        "source-section",
        destination_title="Moved Parent",
    )

    final_position = assert_destination_position_contract(
        result, state["items"], "new-parent"
    )
    assert copy_stage_position["index"] == 2
    assert copy_stage_position["sibling_count"] == 3
    assert final_position["index"] == 1
    assert final_position["sibling_count"] == 2
    assert result["destination_position"] != copy_stage_position


@pytest.mark.write_contract
def test_move_page_normalizes_copy_readback_failure_to_copy_only(monkeypatch):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying._inspect_move_page_plan(
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
            PageRichTextReadbackMismatch(
                "readback failed",
                partial=True,
                outcome="copy_unverified",
                source_untouched=True,
                source_deleted=False,
                failed_content_object_types=["RichText"],
                readback_content_category="RichText",
                copy_report=report,
                created_ids=["new-parent"],
                failed_step="verify_copy",
            )
        ),
    )

    with pytest.raises(PageRichTextReadbackMismatch) as caught:
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            destination_title="Moved Parent",
            include_descendants=True,
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["copy_report"] == report
    assert caught.value.details["created_ids"] == ["new-parent"]
    assert caught.value.details["readback_error_code"] == (
        "page_rich_text_readback_mismatch"
    )


@pytest.mark.write_contract
@pytest.mark.parametrize("failure_mode", ["source_alias", "ambiguous_readback"])
def test_move_page_actual_copy_identity_failure_blocks_all_source_deletes(
    monkeypatch, failure_mode
):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    planned = server.services.copying._inspect_move_page_plan(
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
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying._inspect_move_page_plan(
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
                "copy_contract_satisfied": True,
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
            destination_title="Moved Parent",
            include_descendants=True,
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["created_ids"] == ["new-parent"]
    assert caught.value.details["source_revalidation_error"] == "source vanished"
    assert caught.value.details["destination_position"]["status"] == "unavailable"


@pytest.mark.write_contract
def test_move_page_blocks_delete_when_source_changes_after_copy(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying._inspect_move_page_plan(
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
                "copy_contract_satisfied": True,
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
            destination_title="Moved Parent",
            include_descendants=True,
        )

    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["created_ids"] == ["new-parent"]


@pytest.mark.write_contract
def test_move_page_recycles_source_pages_leaf_to_root(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying._inspect_move_page_plan(
        "parent", "destination-section", "Moved Parent", True
    )
    def execute_copy(_value):
        for item in state["items"]:
            if item.get("id") in {"parent", "child"}:
                item["modified"] = f"drifted-{item['id']}"
        state["items"].extend(
            [
                {
                    "resource_type": "page",
                    "id": "new-parent",
                    "title": "Moved Parent",
                    "parent_id": "destination-section",
                    "section_id": "destination-section",
                    "notebook_id": "n",
                    "page_level": 1,
                    "parent_page_id": None,
                    "order": 0,
                },
                {
                    "resource_type": "page",
                    "id": "new-child",
                    "title": "Child",
                    "parent_id": "destination-section",
                    "section_id": "destination-section",
                    "notebook_id": "n",
                    "page_level": 2,
                    "parent_page_id": "new-parent",
                    "order": 1,
                },
            ]
        )
        return {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent", "new-child"],
            "copy_report": {
                "lossless": True,
                "verified": True,
                "copy_contract_satisfied": True,
                "id_map": {"parent": "new-parent", "child": "new-child"},
            },
            "warnings": [],
        }

    monkeypatch.setattr(server.services.copying, "_execute_copy", execute_copy)
    deleted = []
    delete_confirmations = {}

    def delete_page(page_id, expected_title, expected_section_id, expected_modified, permanently, **_kwargs):
        assert permanently is False
        deleted.append(page_id)
        delete_confirmations[page_id] = expected_modified
        remove_fake_items(state, page_id)
        advance_fake_mutation_epoch()
        return {"deleted": True, "final_state": {"id": page_id, "is_in_recycle_bin": True}}

    monkeypatch.setattr(server.services.mutations, "delete_page", delete_page)

    result = server.services.copying.move_page(
        "parent",
        "destination-section",
        "Parent",
        "source-section",
        destination_title="Moved Parent",
        include_descendants=True,
    )

    assert deleted == ["child", "parent"]
    assert delete_confirmations == {
        "child": "drifted-child",
        "parent": "drifted-parent",
    }
    assert result["source_deleted"] is True
    assert result["source_deleted_nonpermanently"] is True
    assert result["source_deleted_to_recycle_bin"] is True
    assert result["recycle_bin_verification"] == "verified"
    assert result["outcome"] == "moved"
    assert any("source modified timestamps" in warning for warning in result["warnings"])
    assert result["destination_position"]["status"] == "observed"


@pytest.mark.write_contract
def test_move_page_reports_verified_and_remaining_ids_on_delete_failure(monkeypatch):
    install_plan_fakes(monkeypatch, body="")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying._inspect_move_page_plan(
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
                "copy_contract_satisfied": True,
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
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    plan = server.services.copying._inspect_move_page_plan(
        "parent", "destination-section", "Moved Parent"
    )
    def execute_copy(_value):
        state["items"].append(
            {
                "resource_type": "page",
                "id": "new-parent",
                "title": "Moved Parent",
                "parent_id": "destination-section",
                "section_id": "destination-section",
                "notebook_id": "n",
                "page_level": 1,
                "parent_page_id": None,
                "order": 0,
            }
        )
        return {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent"],
            "copy_report": {
                "lossless": True,
                "verified": True,
                "copy_contract_satisfied": True,
                "id_map": {"parent": "new-parent"},
            },
            "warnings": [],
        }

    monkeypatch.setattr(server.services.copying, "_execute_copy", execute_copy)
    monkeypatch.setattr(
        server.services.mutations,
        "delete_page",
        lambda *args, **kwargs: (
            remove_fake_items(state, args[0]),
            advance_fake_mutation_epoch(),
            {"deleted": True, "final_state": None},
        )[-1],
    )
    result = server.services.copying.move_page(
        "parent",
        "destination-section",
        "Parent",
        "source-section",
        destination_title="Moved Parent",
    )

    assert result["outcome"] == "moved"
    assert result["source_deleted"] is True
    assert result["source_deleted_nonpermanently"] is True
    assert result["source_deleted_to_recycle_bin"] is None
    assert result["recycle_bin_verification"] == "not_required_com_unavailable"
    assert result["destination_position"]["status"] == "observed"
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
        ("section", "source-container-section", "_inspect_move_section_plan", "move_section"),
        (
            "section_group",
            "source-group",
            "_inspect_move_section_group_plan",
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
        ("source-container-section", "_inspect_move_section_plan", "reparent_section"),
        ("source-group", "_inspect_move_section_group_plan", "reparent_section_group"),
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
        "protected_digest": "source-protected",
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
        "copy_contract_satisfied": True,
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
            {
                "source_digest": "source-digest",
                "protected_digest": "source-protected",
                "source": {"modified": "m1"},
                "resources": [],
            },
            {"source_digest": "target-digest", "protected_digest": "target-protected"},
            {"source_digest": "target-digest", "protected_digest": "target-protected"},
        ]
    )
    delete_calls = []
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    monkeypatch.setattr(server.services.copying, "_build_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(server.services.copying, "_execute_copy", lambda _plan: copied)
    monkeypatch.setattr(server.services.copying, "_capture_source", lambda *args, **kwargs: next(captures))
    final_items = [
        {
            "resource_type": "notebook",
            "id": "destination-notebook",
            "name": "Destination",
            "path": "Destination",
            "parent_id": None,
        },
        {
            "resource_type": resource_type,
            "id": target_root,
            "name": "Moved",
            "path": f"Destination/{resource_type}",
            "parent_id": "destination-notebook",
            "notebook_id": "destination-notebook",
            "order": 0,
        },
        (
            {
                "resource_type": "page",
                "id": target_child,
                "title": "Child Page",
                "path": "Destination/Child Page",
                "parent_id": target_root if resource_type == "section" else "target-section",
                "section_id": target_root if resource_type == "section" else "target-section",
                "notebook_id": "destination-notebook",
                "parent_page_id": None,
                "page_level": 1,
                "order": 0,
            }
            if resource_type == "section"
            else {
                "resource_type": "section",
                "id": target_child,
                "name": "Moved Section",
                "path": "Destination/Moved Section",
                "parent_id": target_root,
                "notebook_id": "destination-notebook",
                "order": 0,
            }
        ),
    ]
    monkeypatch.setattr(
        server.services.hierarchy,
        "hierarchy_xml",
        lambda start_id="", scope="pages": hierarchy_xml_from_items(final_items),
    )

    def delete_resource(*args, **_kwargs):
        delete_calls.append(args)
        advance_fake_mutation_epoch()
        return {"final_state": None}

    monkeypatch.setattr(server.services.mutations, "delete_resource", delete_resource)
    return source_id, child_id, copied, delete_calls, final_items


@pytest.mark.write_contract
@pytest.mark.parametrize("resource_type", ["section", "section_group"])
def test_container_move_uses_one_nonpermanent_root_delete(monkeypatch, resource_type):
    source_id, child_id, _copied, delete_calls, final_items = install_container_move_execution_fakes(
        monkeypatch, resource_type
    )
    method = getattr(server.services.copying, f"move_{resource_type}")

    result = method(
        source_id,
        "destination-notebook",
        "Source",
        "source-notebook",
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
    assert_destination_position_contract(result, final_items, result["item"]["id"])


@pytest.mark.write_contract
def test_container_move_uses_shared_copy_contract_without_lossless_gate(monkeypatch):
    source_id, _child_id, copied, delete_calls, _final_items = install_container_move_execution_fakes(
        monkeypatch, "section"
    )
    copied["copy_report"]["lossless"] = False
    copied["copy_report"]["fidelity"] = "unverified"

    result = server.services.copying.move_section(
        source_id,
        "destination-notebook",
        "Source",
        "source-notebook",
        "m1",
        "Moved",
    )

    assert result["outcome"] == "moved"
    assert len(delete_calls) == 1


@pytest.mark.write_contract
def test_container_move_accepts_destination_modified_clock_drift(monkeypatch):
    source_id, _child_id, _copied, delete_calls, _final_items = (
        install_container_move_execution_fakes(monkeypatch, "section_group")
    )
    captures = iter(
        [
            {"source_digest": "source-digest", "protected_digest": "source-protected"},
            {"source_digest": "target-before", "protected_digest": "target-protected"},
            {"source_digest": "target-after", "protected_digest": "target-protected"},
        ]
    )
    monkeypatch.setattr(
        server.services.copying,
        "_capture_source",
        lambda *args, **kwargs: next(captures),
    )

    result = server.services.copying.move_section_group(
        source_id,
        "destination-notebook",
        "Source",
        "source-notebook",
        "m1",
        "Moved",
    )

    assert result["outcome"] == "moved"
    assert len(delete_calls) == 1
    assert any("modified timestamps" in warning for warning in result["warnings"])


@pytest.mark.write_contract
def test_container_move_accepts_source_modified_clock_drift_and_rebinds_delete(monkeypatch):
    source_id, _child_id, _copied, delete_calls, _final_items = (
        install_container_move_execution_fakes(monkeypatch, "section_group")
    )
    captures = iter(
        [
            {
                "source_digest": "source-clock-drift",
                "protected_digest": "source-protected",
                "source": {"modified": "m2"},
                "resources": [],
            },
            {"source_digest": "target", "protected_digest": "target-protected"},
            {"source_digest": "target", "protected_digest": "target-protected"},
        ]
    )
    monkeypatch.setattr(
        server.services.copying,
        "_capture_source",
        lambda *args, **kwargs: next(captures),
    )

    result = server.services.copying.move_section_group(
        source_id,
        "destination-notebook",
        "Source",
        "source-notebook",
        "m1",
        "Moved",
    )

    assert result["outcome"] == "moved"
    assert len(delete_calls) == 1
    assert delete_calls[0][4] == "m2"
    assert any("source modified timestamps" in warning for warning in result["warnings"])


@pytest.mark.write_contract
def test_container_move_reports_destination_semantic_drift_after_source_delete(monkeypatch):
    source_id, _child_id, _copied, delete_calls, _final_items = (
        install_container_move_execution_fakes(monkeypatch, "section_group")
    )
    captures = iter(
        [
            {"source_digest": "source-digest", "protected_digest": "source-protected"},
            {"source_digest": "target-before", "protected_digest": "target-protected-before"},
            {"source_digest": "target-after", "protected_digest": "target-protected-after"},
        ]
    )
    monkeypatch.setattr(
        server.services.copying,
        "_capture_source",
        lambda *args, **kwargs: next(captures),
    )

    with pytest.raises(PartialFailure) as raised:
        server.services.copying.move_section_group(
            source_id,
            "destination-notebook",
            "Source",
            "source-notebook",
            "m1",
            "Moved",
        )

    assert len(delete_calls) == 1
    assert raised.value.details["outcome"] == "source_removed_destination_revalidation_failed"
    assert raised.value.details["source_deleted"] is True
    assert "protected topology or content changed" in raised.value.details[
        "destination_revalidation_error"
    ]


@pytest.mark.write_contract
def test_container_move_does_not_delete_when_source_revalidation_changes(monkeypatch):
    source_id, _child_id, _copied, delete_calls, final_items = install_container_move_execution_fakes(
        monkeypatch, "section"
    )
    monkeypatch.setattr(
        server.services.copying,
        "_capture_source",
        lambda *args, **kwargs: {
            "source_digest": "changed-source",
            "protected_digest": "changed-protected-source",
        },
    )

    with pytest.raises(PartialFailure) as raised:
        server.services.copying.move_section(
            source_id,
            "destination-notebook",
            "Source",
            "source-notebook",
            "m1",
            "Moved",
        )

    assert raised.value.details["outcome"] == "copy_only"
    assert raised.value.details["destination_position"] == (
        assert_destination_position_contract(
            {"destination_position": raised.value.details["destination_position"]},
            final_items,
            f"target-{source_id}",
        )
    )
    assert delete_calls == []


@pytest.mark.write_contract
def test_container_move_reports_remaining_descendant_without_extra_deletes(monkeypatch):
    source_id, child_id, _copied, delete_calls, _final_items = install_container_move_execution_fakes(
        monkeypatch, "section_group"
    )
    remaining_after_delete = [
        {
            "resource_type": "notebook",
            "id": "source-notebook",
            "name": "Source Notebook",
            "path": "Source Notebook",
            "parent_id": None,
        },
        {
            "resource_type": "section",
            "id": child_id,
            "name": "Remaining Section",
            "path": "Source Notebook/Remaining Section",
            "parent_id": "source-notebook",
            "notebook_id": "source-notebook",
            "order": 0,
        },
    ]
    monkeypatch.setattr(
        server.services.hierarchy,
        "hierarchy_xml",
        lambda start_id="", scope="pages": hierarchy_xml_from_items(remaining_after_delete),
    )

    with pytest.raises(PartialFailure) as raised:
        server.services.copying.move_section_group(
            source_id,
            "destination-notebook",
            "Source",
            "source-notebook",
            "m1",
            "Moved",
        )

    assert len(delete_calls) == 1
    assert raised.value.details["outcome"] == "source_partially_removed"
    assert raised.value.details["attempted_source_ids"] == [source_id]
    assert raised.value.details["remaining_source_ids"] == [child_id]


def _enable_copy_move(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")


@pytest.mark.write_contract
def test_gui_change_after_source_drift_blocks_delete_and_promotion(monkeypatch):
    state = install_recursive_execute_fakes(
        monkeypatch,
        include_destination_section=True,
    )
    _enable_copy_move(monkeypatch)
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    dispatched: list[str] = []

    def mutations_call(operation, **params):
        dispatched.append(operation)
        raise AssertionError(f"unexpected mutation after GUI intercept: {operation}")

    monkeypatch.setattr(server.services.mutations, "call", mutations_call)
    original_fresh = server.services.copying._fresh_hierarchy_snapshot

    def fresh_then_hijack(*, reason: str):
        if reason == "delete_confirmation":
            for item in state:
                if item["id"] == "source-page":
                    item["title"] = "Hijacked By GUI"
        return original_fresh(reason=reason)

    monkeypatch.setattr(server.services.copying, "_fresh_hierarchy_snapshot", fresh_then_hijack)

    with pytest.raises(PartialFailure) as caught:
        server.services.copying.move_page(
            "source-page",
            "destination-section",
            "Page",
            "source-section",
            destination_title="Moved Page",
            include_descendants=False,
        )

    assert "delete_hierarchy" not in dispatched
    assert caught.value.details["outcome"] == "source_delete_failed"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details.get("deleted_source_ids") in (None, [])


@pytest.mark.write_contract
def test_gui_change_after_source_drift_blocks_promotion_update(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    _enable_copy_move(monkeypatch)
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda value: {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent"],
            "copy_report": {
                "lossless": True,
                "verified": True,
                "copy_contract_satisfied": True,
                "id_map": {"parent": "new-parent"},
            },
            "warnings": [],
        },
    )
    dispatched: list[str] = []

    def record_call(operation, **params):
        dispatched.append(operation)
        raise AssertionError(f"unexpected mutation after GUI intercept: {operation}")

    monkeypatch.setattr(server.services.copying, "call", record_call)
    monkeypatch.setattr(server.services.mutations, "call", record_call)
    original_fresh = server.services.copying._fresh_hierarchy_snapshot

    def fresh_then_remove_child(*, reason: str):
        if reason == "delete_confirmation":
            state["items"] = [item for item in state["items"] if item["id"] != "child"]
        return original_fresh(reason=reason)

    monkeypatch.setattr(server.services.copying, "_fresh_hierarchy_snapshot", fresh_then_remove_child)

    with pytest.raises(PartialFailure) as caught:
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            destination_title="Moved Parent",
            include_descendants=False,
        )

    assert dispatched == []
    assert caught.value.details["outcome"] == "copy_only"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details.get("source_topology_may_have_changed") is True


@pytest.mark.write_contract
def test_gui_change_after_promotion_keeps_update_and_blocks_delete(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    state["xml_clock"] = "before-promotion"
    _enable_copy_move(monkeypatch)
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda value: state["items"].append(
            {
                "resource_type": "page",
                "id": "new-parent",
                "title": "Moved Parent",
                "parent_id": "destination-section",
                "section_id": "destination-section",
                "notebook_id": "n",
                "page_level": 1,
                "parent_page_id": None,
                "order": 0,
            }
        )
        or {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent"],
            "copy_report": {
                "lossless": True,
                "verified": True,
                "copy_contract_satisfied": True,
                "id_map": {"parent": "new-parent"},
            },
            "warnings": [],
        },
    )

    def hierarchy_sensitive_page_xml(page_id, page_info="basic"):
        item = next(value for value in state["items"] if value["id"] == page_id)
        return page_xml(page_id, item["title"], state["body"]).replace(
            'lastModifiedTime="clock"',
            f'lastModifiedTime="{state["xml_clock"]}" pageLevel="{item["page_level"]}"',
        )

    monkeypatch.setattr(server.services.pages, "xml", hierarchy_sensitive_page_xml)
    dispatched: list[str] = []

    def update_hierarchy(operation, **params):
        dispatched.append(operation)
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
        advance_fake_mutation_epoch()
        return {"updated": True}

    monkeypatch.setattr(server.services.copying, "call", update_hierarchy)

    def mutations_call(operation, **params):
        dispatched.append(operation)
        raise AssertionError(f"delete must remain blocked: {operation}")

    monkeypatch.setattr(server.services.mutations, "call", mutations_call)
    original_fresh = server.services.copying._fresh_hierarchy_snapshot
    confirmations = {"count": 0}

    def fresh_then_hijack_after_promotion(*, reason: str):
        snapshot = original_fresh(reason=reason)
        if reason == "delete_confirmation":
            confirmations["count"] += 1
            if confirmations["count"] >= 2:
                for item in state["items"]:
                    if item["id"] == "parent":
                        item["title"] = "Hijacked By GUI"
                return original_fresh(reason=reason)
        return snapshot

    monkeypatch.setattr(
        server.services.copying,
        "_fresh_hierarchy_snapshot",
        fresh_then_hijack_after_promotion,
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            destination_title="Moved Parent",
            include_descendants=False,
        )

    assert dispatched.count("update_hierarchy") == 1
    assert "delete_hierarchy" not in dispatched
    assert caught.value.details["outcome"] == "source_delete_failed"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["preserved_descendants"]["promoted"] is True
    child = next(item for item in state["items"] if item["id"] == "child")
    assert child["page_level"] == 1
    assert child["parent_page_id"] is None


@pytest.mark.write_contract
def test_gui_modified_drift_after_promotion_keeps_update_and_blocks_delete(monkeypatch):
    state = install_plan_fakes(monkeypatch, body="")
    state["xml_clock"] = "before-promotion"
    _enable_copy_move(monkeypatch)
    monkeypatch.setattr(server.services.copying, "_confirm_source", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        server.services.copying,
        "_execute_copy",
        lambda value: state["items"].append(
            {
                "resource_type": "page",
                "id": "new-parent",
                "title": "Moved Parent",
                "parent_id": "destination-section",
                "section_id": "destination-section",
                "notebook_id": "n",
                "page_level": 1,
                "parent_page_id": None,
                "order": 0,
            }
        )
        or {
            "item": {"id": "new-parent", "resource_type": "page"},
            "created_ids": ["new-parent"],
            "copy_report": {
                "lossless": True,
                "verified": True,
                "copy_contract_satisfied": True,
                "id_map": {"parent": "new-parent"},
            },
            "warnings": [],
        },
    )

    def hierarchy_sensitive_page_xml(page_id, page_info="basic"):
        item = next(value for value in state["items"] if value["id"] == page_id)
        return page_xml(page_id, item["title"], state["body"]).replace(
            'lastModifiedTime="clock"',
            f'lastModifiedTime="{state["xml_clock"]}" pageLevel="{item["page_level"]}"',
        )

    monkeypatch.setattr(server.services.pages, "xml", hierarchy_sensitive_page_xml)
    dispatched: list[str] = []

    def update_hierarchy(operation, **params):
        dispatched.append(operation)
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
            if item["id"] == "parent":
                item["modified"] = "after-promotion-clock"
            stack.append(item)
        state["xml_clock"] = "after-promotion"
        advance_fake_mutation_epoch()
        return {"updated": True}

    monkeypatch.setattr(server.services.copying, "call", update_hierarchy)

    def mutations_call(operation, **params):
        dispatched.append(operation)
        raise AssertionError(f"delete must remain blocked: {operation}")

    monkeypatch.setattr(server.services.mutations, "call", mutations_call)
    original_fresh = server.services.copying._fresh_hierarchy_snapshot
    confirmations = {"count": 0}

    def fresh_then_drift_modified_after_promotion(*, reason: str):
        snapshot = original_fresh(reason=reason)
        if reason == "delete_confirmation":
            confirmations["count"] += 1
            if confirmations["count"] >= 2:
                for item in state["items"]:
                    if item["id"] == "parent":
                        item["modified"] = "external-drift-after-promotion"
                return original_fresh(reason=reason)
        return snapshot

    monkeypatch.setattr(
        server.services.copying,
        "_fresh_hierarchy_snapshot",
        fresh_then_drift_modified_after_promotion,
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            destination_title="Moved Parent",
            include_descendants=False,
        )

    assert dispatched.count("update_hierarchy") == 1
    assert "delete_hierarchy" not in dispatched
    assert caught.value.details["outcome"] == "source_delete_failed"
    assert caught.value.details["source_deleted"] is False
    assert caught.value.details["preserved_descendants"]["promoted"] is True
    assert "source_root_modified" not in caught.value.details["preserved_descendants"]
    assert "modified" not in caught.value.details["preserved_descendants"]
    child = next(item for item in state["items"] if item["id"] == "child")
    assert child["page_level"] == 1
    assert child["parent_page_id"] is None
