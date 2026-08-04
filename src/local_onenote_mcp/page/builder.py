"""Build OneNote UpdatePageContent XML from typed Page content."""

from __future__ import annotations

import html
import re
from typing import Literal

from ..constants import ONE_NS
from . import formatting
from .models import TableCell, TextBlock


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


def content_to_oe_xml(
    content: str,
    content_format: Literal["plain", "html", "markdown", "md"] = "plain",
) -> str:
    if content_format == "plain":
        return oe_children(formatting.normalize_content(content, "plain"))
    if content_format == "html":
        blocks = formatting.html_content_blocks(content)
        if not blocks:
            return ""
        parts: list[str] = []
        for block in blocks:
            if isinstance(block, TextBlock):
                parts.append(oe_children(block.html))
            else:
                parts.append(one_table(block.rows))
        return "".join(parts)
    if content_format in {"markdown", "md"}:
        return content_to_oe_xml(formatting.markdown_to_html(content), "html")
    raise ValueError("content_format must be 'plain', 'html', or 'markdown'.")


def build_outline_xml(
    content: str,
    *,
    content_format: Literal["plain", "html", "markdown", "md"] = "plain",
    object_id: str | None = None,
    x: float | None = None,
    y: float | None = None,
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
        f"<one:OEChildren>{content_to_oe_xml(content, content_format)}</one:OEChildren>"
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
) -> str:
    parts = [f'<one:Page xmlns:one="{ONE_NS}" ID="{attr(page_id)}">']
    if title is not None:
        parts.append(build_title_xml(title))
    if content is not None and content != "":
        parts.append(build_outline_xml(content, content_format=content_format, x=x, y=y))
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
