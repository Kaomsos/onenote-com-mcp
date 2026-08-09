"""Build OneNote UpdatePageContent XML from typed Page content."""

from __future__ import annotations

from dataclasses import dataclass
import html
import re
import xml.etree.ElementTree as ET
from typing import Literal

from ..constants import ONE_NS
from . import formatting
from .models import ContentBlock, ListBlock, TableBlock, TableCell, TextBlock


TODO_TAG_KIND = "to-do"
TODO_TAG_ATTRIBUTES = {
    "type": "0",
    "symbol": "3",
    "fontColor": "automatic",
    "highlightColor": "none",
    "name": "To Do",
}


@dataclass
class TagDefinitionState:
    by_kind: dict[str, int]
    occupied_indices: set[int]


def cdata(value: str) -> str:
    return "<![CDATA[" + value.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def attr(value: str) -> str:
    return html.escape(value, quote=True)


def one_t(fragment_html: str) -> str:
    return f"<one:T>{cdata(fragment_html)}</one:T>"


def oe_children(fragment_html: str) -> str:
    parts = re.split(r"<br\s*/?>", fragment_html)
    if not parts:
        parts = [""]
    return "".join(f"<one:OE>{one_t(part)}</one:OE>" for part in parts)


def _one_table_cell(cell: TableCell) -> str:
    shading = ' shadingColor="#D9EAF7"' if cell.header else ""
    font_size = "10.5pt" if cell.header else "10.0pt"
    style_attr = f' style="font-family:\'Microsoft YaHei\';font-size:{font_size}"'
    cell_html = cell.html
    if cell.header:
        cell_html = f"<span style='font-weight:bold'>{cell_html}</span>"
    return (
        f"<one:Cell{shading}>"
        "<one:OEChildren>"
        f'<one:OE alignment="left" quickStyleIndex="0"{style_attr}>{one_t(cell_html)}</one:OE>'
        "</one:OEChildren>"
        "</one:Cell>"
    )


def _table_column_widths(rows: list[list[TableCell]]) -> list[float]:
    column_count = max((len(row) for row in rows), default=0)
    if column_count <= 0:
        return []
    total_width = 960.0
    width = max(90.0, min(220.0, total_width / column_count))
    return [width] * column_count


def one_table(rows: list[list[TableCell]]) -> str:
    widths = _table_column_widths(rows)
    if not widths:
        return ""
    column_xml = "".join(
        f'<one:Column index="{index}" width="{width:.1f}" isLocked="true"/>'
        for index, width in enumerate(widths)
    )
    row_xml = []
    column_count = len(widths)
    for row in rows:
        padded = row + [TableCell(html="")] * (column_count - len(row))
        row_xml.append("<one:Row>" + "".join(_one_table_cell(cell) for cell in padded[:column_count]) + "</one:Row>")
    return (
        '<one:OE alignment="left"><one:Table bordersVisible="true" hasHeaderRow="false">'
        f"<one:Columns>{column_xml}</one:Columns>"
        f"{''.join(row_xml)}"
        "</one:Table></one:OE>"
    )


def one_list(block: ListBlock, tag_indices: dict[str, int]) -> str:
    list_node = (
        '<one:Number numberSequence="0" numberFormat="##."/>'
        if block.ordered
        else '<one:Bullet bullet="2"/>'
    )
    parts: list[str] = []
    for item in block.items:
        tag_xml = ""
        if item.tag is not None:
            tag_index = tag_indices[item.tag.kind]
            tag_xml = (
                f'<one:Tag index="{tag_index}" '
                f'completed="{str(item.tag.completed).lower()}" disabled="false"/>'
            )
        parts.append(
            '<one:OE alignment="left">'
            f"{tag_xml}<one:List>{list_node}</one:List>"
            f"{one_t(item.html)}"
            "</one:OE>"
        )
    return "".join(parts)


def tag_definitions_from_page_xml(xml: str) -> TagDefinitionState:
    """Return supported native tag definitions, keyed by stable semantic kind."""

    definitions: dict[str, int] = {}
    occupied_indices: set[int] = set()
    root = ET.fromstring(xml)
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "TagDef":
            continue
        try:
            index = int(node.attrib["index"])
        except (KeyError, ValueError):
            continue
        occupied_indices.add(index)
        if node.attrib.get("type") == "0" and node.attrib.get("symbol") == "3":
            definitions[TODO_TAG_KIND] = index
    return TagDefinitionState(
        by_kind=definitions,
        occupied_indices=occupied_indices,
    )


def _content_blocks(
    content: str,
    content_format: Literal["plain", "html", "markdown", "md"],
) -> list[ContentBlock]:
    if content_format == "plain":
        return [TextBlock(html=formatting.normalize_content(content, "plain"))]
    if content_format == "html":
        return formatting.html_content_blocks(content)
    if content_format in {"markdown", "md"}:
        return formatting.html_content_blocks(formatting.markdown_to_html(content))
    raise ValueError("content_format must be 'plain', 'html', or 'markdown'.")


def _blocks_to_oe_xml(
    blocks: list[ContentBlock],
    tag_indices: dict[str, int],
) -> str:
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            parts.append(oe_children(block.html))
        elif isinstance(block, TableBlock):
            parts.append(one_table(block.rows))
        else:
            parts.append(one_list(block, tag_indices))
    return "".join(parts)


def content_to_oe_xml(
    content: str,
    content_format: Literal["plain", "html", "markdown", "md"] = "plain",
    *,
    tag_indices: dict[str, int] | None = None,
) -> str:
    return _blocks_to_oe_xml(_content_blocks(content, content_format), tag_indices or {})


def build_outline_xml(
    content: str,
    *,
    content_format: Literal["plain", "html", "markdown", "md"] = "plain",
    object_id: str | None = None,
    x: float | None = None,
    y: float | None = None,
    blocks: list[ContentBlock] | None = None,
    tag_indices: dict[str, int] | None = None,
) -> str:
    object_attr = f' objectID="{attr(object_id)}"' if object_id else ""
    position = ""
    if x is not None or y is not None:
        px = 36.0 if x is None else float(x)
        py = 86.0 if y is None else float(y)
        position = f'<one:Position x="{px:.2f}" y="{py:.2f}" z="0"/>'
    return (
        f"<one:Outline{object_attr}>"
        f"{position}"
        f"<one:OEChildren>{_blocks_to_oe_xml(blocks, tag_indices or {}) if blocks is not None else content_to_oe_xml(content, content_format, tag_indices=tag_indices)}</one:OEChildren>"
        "</one:Outline>"
    )


def build_title_xml(title: str) -> str:
    return f"<one:Title><one:OE>{one_t(html.escape(title, quote=False))}</one:OE></one:Title>"


def build_page_update_xml(
    page_id: str,
    *,
    title: str | None = None,
    content: str | None = None,
    content_format: str = "plain",
    x: float | None = None,
    y: float | None = None,
    existing_tag_definitions: TagDefinitionState | None = None,
) -> str:
    blocks = _content_blocks(content, content_format) if content not in {None, ""} else []
    required_tag_kinds = {
        item.tag.kind
        for block in blocks
        if isinstance(block, ListBlock)
        for item in block.items
        if item.tag is not None
    }
    tag_indices = dict(existing_tag_definitions.by_kind if existing_tag_definitions else {})
    used_indices = set(
        existing_tag_definitions.occupied_indices if existing_tag_definitions else set()
    )
    new_tag_kinds: list[str] = []
    for kind in sorted(required_tag_kinds):
        if kind in tag_indices:
            continue
        index = next(value for value in range(100) if value not in used_indices)
        tag_indices[kind] = index
        used_indices.add(index)
        new_tag_kinds.append(kind)

    parts = [f'<one:Page xmlns:one="{ONE_NS}" ID="{attr(page_id)}">']
    for kind in new_tag_kinds:
        if kind != TODO_TAG_KIND:
            raise ValueError(f"Unsupported native OneNote tag kind: {kind}")
        rendered = " ".join(
            f'{name}="{attr(value)}"' for name, value in TODO_TAG_ATTRIBUTES.items()
        )
        parts.append(f'<one:TagDef index="{tag_indices[kind]}" {rendered}/>')
    if title is not None:
        parts.append(build_title_xml(title))
    if blocks:
        parts.append(
            build_outline_xml(
                content or "",
                content_format=content_format,
                x=x,
                y=y,
                blocks=blocks,
                tag_indices=tag_indices,
            )
        )
    parts.append("</one:Page>")
    return "".join(parts)


def build_image_page_update_xml(
    page_id: str,
    *,
    image_base64: str,
    image_format: str,
    x: float = 36.0,
    y: float = 120.0,
    width: float | None = None,
    height: float | None = None,
) -> str:
    size = ""
    if width is not None and height is not None:
        size = f'<one:Size width="{float(width):.2f}" height="{float(height):.2f}"/>'
    return (
        f'<one:Page xmlns:one="{ONE_NS}" ID="{attr(page_id)}">'
        "<one:Outline>"
        f'<one:Position x="{float(x):.2f}" y="{float(y):.2f}" z="0"/>'
        "<one:OEChildren><one:OE>"
        f'<one:Image format="{attr(image_format.lower())}">'
        f"{size}<one:Data>{image_base64}</one:Data>"
        "</one:Image>"
        "</one:OE></one:OEChildren>"
        "</one:Outline>"
        "</one:Page>"
    )
