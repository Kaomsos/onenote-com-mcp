import xml.etree.ElementTree as ET

import pytest

from local_onenote_mcp.page import formatting
from local_onenote_mcp.page import (
    build_image_page_update_xml,
    build_page_update_xml,
    collect_page_objects,
    normalize_content,
    rich_html_from_page_xml,
    text_from_page_xml,
    truncate_rich_html,
)
from local_onenote_mcp.page.builder import tag_definitions_from_page_xml
from local_onenote_mcp.page.images import image_dimensions, proportional_dimensions


def test_plain_text_becomes_onenote_inline_html():
    assert normalize_content("a<b\nc", "plain") == "a&lt;b<br/>c"


def test_html_sanitizer_keeps_safe_inline_tags():
    assert normalize_content("<p>Hello <strong>world</strong></p><script>x</script>", "html") == (
        "Hello <strong>world</strong>"
    )


def test_html_sanitizer_maps_daily_inline_styles():
    html = normalize_content("<p><s>gone</s> <code>x=1</code> <mark>note</mark></p>", "html")

    assert "text-decoration:line-through" in html
    assert "font-family:Consolas" in html
    assert "background:#FFF2CC" in html


def test_html_sanitizer_preserves_bounded_inline_and_display_mathml():
    namespace = "http://www.w3.org/1998/Math/MathML"
    content = (
        f'<p>before <math xmlns="{namespace}"><mrow><mi>E</mi><mo>=</mo>'
        "<mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></mrow></math> after</p>"
        f'<math xmlns="{namespace}" display="block"><mfrac><mi>x</mi>'
        "<mi>y</mi></mfrac></math>"
    )

    rendered = normalize_content(content, "html")

    assert rendered.count(f'<math xmlns="{namespace}"') == 2
    assert rendered.count('display="block"') == 1
    assert "before <math" in rendered
    assert "</math> after" in rendered
    assert "<mfrac><mi>x</mi><mi>y</mi></mfrac>" in rendered


@pytest.mark.parametrize(
    "content",
    [
        "<math><mi>x</mi></math>",
        '<math xmlns="http://www.w3.org/1998/Math/MathML" display="inline"><mi>x</mi></math>',
        '<math xmlns="http://www.w3.org/1998/Math/MathML" onclick><mi>x</mi></math>',
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><mstyle mathcolor="red"><mi>x</mi></mstyle></math>',
    ],
)
def test_html_sanitizer_rejects_unbounded_mathml(content):
    with pytest.raises(ValueError, match="MathML"):
        normalize_content(content, "html")


def test_html_sanitizer_drops_mathml_inside_script_content():
    content = (
        '<script><math xmlns="http://www.w3.org/1998/Math/MathML">'
        "<mi>secret</mi></math></script><p>visible</p>"
    )

    assert normalize_content(content, "html") == "visible"


def test_build_page_update_xml_uses_cdata():
    xml = build_page_update_xml("page-id", title="Title", content="Hello\nWorld")
    assert 'ID="page-id"' in xml
    assert "<one:Title>" in xml
    assert "<![CDATA[Hello]]>" in xml
    assert "<![CDATA[World]]>" in xml


def test_display_mathml_is_emitted_as_a_dedicated_nonempty_oe() -> None:
    namespace = "http://www.w3.org/1998/Math/MathML"
    xml = build_page_update_xml(
        "page-id",
        content=(
            "<p>Inline fixture</p><span>Display equation fixture:</span>"
            f'<math xmlns="{namespace}" display="block">'
            "<mrow><mi>x</mi><mo>=</mo><mn>1</mn></mrow></math>"
            "<p>After fixture</p>"
        ),
        content_format="html",
    )
    root = ET.fromstring(xml)
    parents = {id(child): parent for parent in root.iter() for child in list(parent)}
    display_text = next(
        node
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] == "T"
        and 'display="block"' in (node.text or "")
    )
    display_oe = parents[id(display_text)]
    oe_children_node = parents[id(display_oe)]
    siblings = list(oe_children_node)
    display_index = siblings.index(display_oe)
    predecessor_text = next(
        node.text or ""
        for node in siblings[display_index - 1].iter()
        if node.tag.rsplit("}", 1)[-1] == "T"
    )

    assert [child.tag.rsplit("}", 1)[-1] for child in display_oe] == ["T"]
    assert (display_text.text or "").startswith(f'<math xmlns="{namespace}"')
    assert predecessor_text == "<span>Display equation fixture:</span>"
    assert all(
        (node.text or "").strip()
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] == "T"
    )


def test_html_table_becomes_native_onenote_table():
    xml = build_page_update_xml(
        "page-id",
        title="Title",
        content="<p>Before</p><table><tr><th>Due</th><th>Task</th></tr><tr><td>2026-07-15</td><td>Submit documents</td></tr></table><p>After</p>",
        content_format="html",
    )

    assert "<one:Table" in xml
    assert 'bordersVisible="true"' in xml
    assert 'hasHeaderRow="false"' in xml
    assert '<one:OE alignment="left"><one:Table' in xml
    assert '<one:Column index="0"' in xml
    assert 'shadingColor="#D9EAF7"' in xml
    assert 'quickStyleIndex="0"' in xml
    assert "<![CDATA[<span style='font-weight:bold'>Due</span>]]>" in xml
    assert "<![CDATA[Submit documents]]>" in xml
    assert "<![CDATA[Before]]>" in xml
    assert "<![CDATA[After]]>" in xml


def test_html_table_cells_are_padded_to_column_count():
    xml = build_page_update_xml(
        "page-id",
        content="<table><tr><th>A</th><th>B</th><th>C</th></tr><tr><td>1</td><td>2</td></tr></table>",
        content_format="html",
    )

    assert xml.count("<one:Column index=") == 3
    assert xml.count("<one:Cell") == 6


def test_html_table_cells_preserve_inline_formatting():
    xml = build_page_update_xml(
        "page-id",
        content='<table><tr><td><strong>Bold</strong> <s>gone</s> <a href="https://example.com">link</a></td></tr></table>',
        content_format="html",
    )

    assert "<strong>Bold</strong>" in xml
    assert "text-decoration:line-through" in xml
    assert 'href="https://example.com"' in xml


def test_html_list_with_todo_tags_becomes_native_onenote_xml():
    xml = build_page_update_xml(
        "page-id",
        content=(
            '<ol><li data-tag="to-do:completed">为</li>'
            '<li data-tag="to-do">答复</li>'
            '<li data-tag="to-do:completed">3发送</li></ol>'
        ),
        content_format="html",
    )

    assert '<one:TagDef index="0" type="0" symbol="3"' in xml
    assert xml.count('<one:Number numberSequence="0" numberFormat="##."/>') == 3
    assert xml.count('<one:Tag index="0"') == 3
    assert xml.count('completed="true"') == 2
    assert xml.count('completed="false"') == 1
    assert "<![CDATA[为]]>" in xml
    assert "<![CDATA[答复]]>" in xml
    assert "<![CDATA[3发送]]>" in xml


def test_tagged_list_item_uses_onenote_oe_schema_order():
    xml = build_page_update_xml(
        "page-id",
        content='<ol><li data-tag="to-do">A</li></ol>',
        content_format="html",
    )

    root = ET.fromstring(xml)
    tagged_item = next(
        node
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] == "OE"
        and any(child.tag.rsplit("}", 1)[-1] == "Tag" for child in node)
    )
    assert [child.tag.rsplit("}", 1)[-1] for child in tagged_item] == [
        "Tag",
        "List",
        "T",
    ]


def test_html_list_reuses_existing_todo_tag_definition():
    existing = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:TagDef index="7" type="0" symbol="3" name="To Do"/>
    </one:Page>"""

    definitions = tag_definitions_from_page_xml(existing)
    xml = build_page_update_xml(
        "page-id",
        content='<ul><li data-tag="to-do">A</li></ul>',
        content_format="html",
        existing_tag_definitions=definitions,
    )

    assert definitions.by_kind == {"to-do": 7}
    assert definitions.occupied_indices == {7}
    assert "<one:TagDef" not in xml
    assert '<one:Bullet bullet="2"/>' in xml
    assert '<one:Tag index="7" completed="false" disabled="false"/>' in xml


def test_every_native_number_list_has_schema_required_number_format():
    xml = build_page_update_xml(
        "page-id",
        content="<ol><li>A</li><li>B</li></ol>",
        content_format="html",
    )

    root = ET.fromstring(xml)
    numbers = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Number"]
    assert len(numbers) == 2
    assert all(node.attrib["numberFormat"] == "##." for node in numbers)


def test_html_list_does_not_collide_with_an_unrelated_existing_tag_definition():
    existing = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:TagDef index="0" type="2" symbol="107" name="Mail"/>
    </one:Page>"""

    xml = build_page_update_xml(
        "page-id",
        content='<ul><li data-tag="to-do">A</li></ul>',
        content_format="html",
        existing_tag_definitions=tag_definitions_from_page_xml(existing),
    )

    assert '<one:TagDef index="1" type="0" symbol="3"' in xml
    assert '<one:Tag index="1" completed="false" disabled="false"/>' in xml


def test_html_list_rejects_unknown_native_tag_kind():
    try:
        build_page_update_xml(
            "page-id",
            content='<ol><li data-tag="important">A</li></ol>',
            content_format="html",
        )
    except ValueError as exc:
        assert "data-tag" in str(exc)
    else:
        raise AssertionError("unsupported data-tag must fail closed")


def test_markdown_content_uses_onemore_markdig_html(monkeypatch):
    monkeypatch.setattr(
        formatting,
        "markdown_to_html",
        lambda content: "<h1>Heading</h1><table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td><strong>2</strong></td></tr></tbody></table>",
    )

    xml = build_page_update_xml("page-id", content="# Heading", content_format="markdown")

    assert "<![CDATA[<span style=\"font-size:20.0pt;font-weight:bold\">Heading</span>]]>" in xml
    assert "<one:Table" in xml
    assert "<![CDATA[<strong>2</strong>]]>" in xml


def test_build_image_xml_omits_size_when_dimensions_are_missing():
    xml = build_image_page_update_xml("page-id", image_base64="abc", image_format="png")
    assert "<one:Image" in xml
    assert "<one:Size" not in xml


def test_build_image_xml_includes_size_when_dimensions_are_complete():
    xml = build_image_page_update_xml(
        "page-id",
        image_base64="abc",
        image_format="png",
        width=320,
        height=180,
    )
    assert '<one:Size width="320.00" height="180.00"/>' in xml


def test_png_dimensions_support_proportional_image_sizing(tmp_path):
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x02\x00"
        b"\x00\x00\x01\x00"
        b"\x08\x02\x00\x00\x00"
        b"\x00\x00\x00\x00"
    )

    assert image_dimensions(image_path) == (512, 256)
    assert proportional_dimensions(image_path, width=256, height=None) == (256.0, 128.0)


def test_text_from_page_xml_extracts_inline_html():
    xml = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Outline><one:OEChildren><one:OE><one:T><![CDATA[Hello<br/>World]]></one:T></one:OE></one:OEChildren></one:Outline>
    </one:Page>"""
    assert text_from_page_xml(xml) == "Hello\nWorld"


def test_rich_html_projection_preserves_safe_formatting_links_lists_tags_and_tables():
    xml = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Title><one:OE><one:T>Rich Page</one:T></one:OE></one:Title>
    <one:TagDef index="0" type="0" symbol="3" name="To Do"/>
    <one:Outline><one:OEChildren>
      <one:OE><one:List><one:Number/></one:List><one:Tag index="0" completed="true"/>
        <one:T><![CDATA[<span style="font-weight:bold;color:#123456;position:absolute">Bold</span>
          <a href="https://example.com" onclick="bad()">link</a><script>bad()</script>]]></one:T>
      </one:OE>
      <one:OE><one:Table><one:Row><one:Cell><one:OEChildren><one:OE>
        <one:T><![CDATA[<i>Cell</i>]]></one:T>
      </one:OE></one:OEChildren></one:Cell></one:Row></one:Table></one:OE>
    </one:OEChildren></one:Outline></one:Page>"""

    html = rich_html_from_page_xml(xml)

    assert 'data-onenote-projection="sanitized_html_v1"' in html
    assert "<h1>Rich Page</h1>" in html
    assert '<ol data-onenote-list-kind="number">' in html
    assert 'data-onenote-tag-completed="true"' in html
    assert 'style="color: #123456; font-weight: bold"' in html
    assert '<a href="https://example.com">link</a>' in html
    assert "onclick" not in html
    assert "position" not in html
    assert "bad()" not in html
    assert "<table><tbody><tr><td><p><i>Cell</i></p></td></tr></tbody></table>" in html


def test_rich_html_projection_preserves_canonical_conditional_mathml_comments():
    namespace = "http://www.w3.org/1998/Math/MathML"
    xml = f'''<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Outline><one:OEChildren><one:OE><one:T><![CDATA[
      before<!--[if mathML]><m:math xmlns:m="{namespace}" display="block">
        <m:mrow><m:mi>x</m:mi><m:mo>=</m:mo><m:mn>1</m:mn></m:mrow>
      </m:math><![endif]-->after
    ]]></one:T></one:OE></one:OEChildren></one:Outline></one:Page>'''

    html = rich_html_from_page_xml(xml)

    assert "<!--[if" not in html
    assert "m:math" not in html
    assert f'<math display="block" xmlns="{namespace}">' in html
    assert "<mrow><mi>x</mi><mo>=</mo><mn>1</mn></mrow>" in html
    assert "before" in html and "after" in html


def test_rich_html_projection_normalizes_unwrapped_prefixed_mathml():
    namespace = "http://www.w3.org/1998/Math/MathML"
    xml = f'''<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Outline><one:OEChildren><one:OE><one:T><![CDATA[
      <m:math xmlns:m="{namespace}"><m:mfrac><m:mi>x</m:mi><m:mi>y</m:mi></m:mfrac></m:math>
    ]]></one:T></one:OE></one:OEChildren></one:Outline></one:Page>'''

    html = rich_html_from_page_xml(xml)

    assert "m:math" not in html
    assert f'<math xmlns="{namespace}"><mfrac><mi>x</mi><mi>y</mi></mfrac></math>' in html


@pytest.mark.parametrize(
    "fragment",
    [
        "<!--[if mathML]><math><mi>x</mi></math><![endif]-->",
        "<!--[if mathML]><m:math xmlns:m=\"urn:not-mathml\"><m:mi>x</m:mi></m:math><![endif]-->",
        "<!--[if mathML]><m:math xmlns:m=\"http://www.w3.org/1998/Math/MathML\"><m:script>x</m:script></m:math><![endif]-->",
        "<!--[if somethingElse]><math xmlns=\"http://www.w3.org/1998/Math/MathML\"><mi>x</mi></math><![endif]-->",
    ],
)
def test_rich_html_projection_rejects_noncanonical_conditional_mathml(fragment):
    xml = f'''<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Outline><one:OEChildren><one:OE><one:T><![CDATA[{fragment}]]></one:T>
    </one:OE></one:OEChildren></one:Outline></one:Page>'''

    html = rich_html_from_page_xml(xml)

    assert "<math" not in html
    assert "<mi" not in html
    assert "<script" not in html


def test_rich_html_projection_strips_unsafe_link_schemes_and_truncates_well_formed():
    xml = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Outline><one:OEChildren><one:OE><one:T><![CDATA[
      <a href="javascript:alert(1)">unsafe</a><strong>abcdefghijklmnopqrstuvwxyz</strong>
    ]]></one:T></one:OE></one:OEChildren></one:Outline></one:Page>"""
    html = rich_html_from_page_xml(xml)

    projected, truncated = truncate_rich_html(html, 120)

    assert "javascript:" not in html
    assert truncated is True
    assert len(projected) <= 120
    assert projected.endswith("</section></article>")


def test_collect_page_objects_keeps_idless_images_with_container():
    xml = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Outline objectID="outline-id"><one:OEChildren><one:OE objectID="oe-id">
      <one:Image format="png"><one:Data>abc</one:Data></one:Image>
    </one:OE></one:OEChildren></one:Outline>
    </one:Page>"""

    objects = collect_page_objects(xml)

    image = next(obj for obj in objects if obj["type"] == "Image")
    assert image["container_object_id"] == "oe-id"
    assert image["format"] == "png"


def test_collect_page_objects_reads_nested_binary_callback_id():
    xml = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Outline objectID="outline-id"><one:OEChildren><one:OE objectID="oe-id">
      <one:Image format="png">
        <one:CallbackID callbackID="image-callback-id"/>
      </one:Image>
    </one:OE></one:OEChildren></one:Outline>
    </one:Page>"""

    objects = collect_page_objects(xml)

    image = next(obj for obj in objects if obj["type"] == "Image")
    assert image["callback_id"] == "image-callback-id"
    assert not any(obj["type"] == "CallbackID" for obj in objects)


def test_collect_page_objects_marks_deletable_containers_and_child_suggestions():
    xml = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Outline objectID="outline-id"><one:OEChildren><one:OE objectID="oe-id">
      <one:T><![CDATA[hello]]></one:T>
    </one:OE></one:OEChildren></one:Outline>
    </one:Page>"""

    objects = collect_page_objects(xml)

    outline = next(obj for obj in objects if obj["type"] == "Outline")
    oe = next(obj for obj in objects if obj["type"] == "OE")
    assert outline["delete_supported"] is True
    assert outline["delete_object_id"] == "outline-id"
    assert oe["delete_supported"] is False
    assert oe["delete_object_id"] == "outline-id"
