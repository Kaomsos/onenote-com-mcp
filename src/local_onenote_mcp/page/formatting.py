"""Sanitize Page content and convert plain/HTML/Markdown into content blocks."""

from __future__ import annotations

import html
import os
import re
import subprocess
import tempfile
import winreg
from html.parser import HTMLParser
from pathlib import Path

from .models import ContentBlock, ListBlock, ListItem, NoteTag, TableBlock, TableCell, TextBlock


BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "div",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "p",
    "pre",
    "section",
    "td",
    "th",
    "tr",
}

INLINE_TAGS = {
    "a",
    "b",
    "br",
    "code",
    "del",
    "em",
    "i",
    "mark",
    "span",
    "strike",
    "strong",
    "sub",
    "sup",
    "s",
    "u",
}

SAFE_ATTRS = {
    "a": {"href", "title"},
    "span": {"style"},
}

INLINE_STYLE_TAGS = {
    "code": "font-family:Consolas,'Courier New',monospace",
    "del": "text-decoration:line-through",
    "mark": "background:#FFF2CC",
    "s": "text-decoration:line-through",
    "strike": "text-decoration:line-through",
}

HEADING_STYLES = {
    "h1": "font-size:20.0pt;font-weight:bold",
    "h2": "font-size:16.0pt;font-weight:bold",
    "h3": "font-size:14.0pt;font-weight:bold",
    "h4": "font-size:12.0pt;font-weight:bold",
    "h5": "font-size:11.0pt;font-weight:bold",
    "h6": "font-size:10.0pt;font-weight:bold",
}

MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML"
MATHML_TAGS = {
    "math",
    "mfrac",
    "mi",
    "mn",
    "mo",
    "mrow",
    "msqrt",
    "msup",
}


class InlineHTMLSanitizer(HTMLParser):
    """Convert arbitrary HTML-ish input to a OneNote-friendly inline fragment."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._drop_stack: list[str] = []
        self._math_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._math_stack:
            if tag not in MATHML_TAGS or tag == "math" or attrs:
                raise ValueError("MathML contains an unsupported element or attribute.")
            self.parts.append(f"<{tag}>")
            self._math_stack.append(tag)
            return
        if tag in {"script", "style"}:
            self._drop_stack.append(tag)
            return
        if self._drop_stack:
            return
        if tag == "math":
            attribute_names = [name.casefold() for name, _value in attrs]
            if len(attribute_names) != len(set(attribute_names)):
                raise ValueError("MathML math contains a duplicate attribute.")
            if set(attribute_names) - {"xmlns", "display"}:
                raise ValueError("MathML math contains an unsupported attribute.")
            values = {
                name.casefold(): value for name, value in attrs if value is not None
            }
            if values.get("xmlns") != MATHML_NAMESPACE:
                raise ValueError(
                    "MathML math must declare the canonical MathML namespace."
                )
            display = values.get("display")
            if display not in {None, "block"}:
                raise ValueError("MathML display must be omitted or 'block'.")
            display_attr = ' display="block"' if display == "block" else ""
            self.parts.append(
                f'<math xmlns="{MATHML_NAMESPACE}"{display_attr}>'
            )
            self._math_stack.append(tag)
            return
        if tag in MATHML_TAGS:
            raise ValueError("MathML elements must be contained by a math root.")
        if tag in HEADING_STYLES:
            self._append_break()
            self.parts.append(f'<span style="{HEADING_STYLES[tag]}">')
            return
        if tag in BLOCK_TAGS:
            self._append_break()
            return
        if tag not in INLINE_TAGS:
            return
        if tag == "br":
            self._append_break()
            return
        if tag in INLINE_STYLE_TAGS:
            self.parts.append(f'<span style="{INLINE_STYLE_TAGS[tag]}">')
            return
        allowed = SAFE_ATTRS.get(tag, set())
        rendered_attrs = []
        for name, value in attrs:
            if value is None:
                continue
            name = name.lower()
            if name not in allowed:
                continue
            if name == "href" and not value.lower().startswith(("http://", "https://", "onenote:", "mailto:")):
                continue
            rendered_attrs.append(f'{name}="{html.escape(value, quote=True)}"')
        attr_text = (" " + " ".join(rendered_attrs)) if rendered_attrs else ""
        self.parts.append(f"<{tag}{attr_text}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._math_stack:
            if tag != self._math_stack[-1]:
                raise ValueError("MathML elements must be properly nested.")
            self.parts.append(f"</{tag}>")
            self._math_stack.pop()
            return
        if self._drop_stack:
            if tag == self._drop_stack[-1]:
                self._drop_stack.pop()
            return
        if tag in MATHML_TAGS:
            raise ValueError("MathML closing element has no matching root.")
        if tag in HEADING_STYLES:
            self.parts.append("</span>")
            self._append_break()
            return
        if tag in BLOCK_TAGS:
            self._append_break()
            return
        if tag in INLINE_STYLE_TAGS:
            self.parts.append("</span>")
            return
        if tag in INLINE_TAGS and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._drop_stack:
            self.parts.append(html.escape(data, quote=False))

    def get_html(self) -> str:
        if self._math_stack:
            raise ValueError("MathML is missing a closing element.")
        text = "".join(self.parts)
        text = re.sub(r"(?:<br/>){3,}", "<br/><br/>", text)
        text = re.sub(r"^(?:<br/>)+|(?:<br/>)+$", "", text)
        return text.strip()

    def _append_break(self) -> None:
        if not self.parts or self.parts[-1] != "<br/>":
            self.parts.append("<br/>")


class OneNoteHTMLBlockParser(HTMLParser):
    """Convert simple HTML into ordered text/table blocks for OneNote XML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[ContentBlock] = []
        self._text = InlineHTMLSanitizer()
        self._display_math: InlineHTMLSanitizer | None = None
        self._display_math_depth = 0
        self._table_depth = 0
        self._rows: list[list[TableCell]] = []
        self._current_row: list[TableCell] | None = None
        self._current_cell: InlineHTMLSanitizer | None = None
        self._current_cell_header = False
        self._drop_stack: list[str] = []
        self._list_tag: str | None = None
        self._list_items: list[ListItem] = []
        self._current_list_item: InlineHTMLSanitizer | None = None
        self._current_list_tag: NoteTag | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._display_math is not None:
            self._display_math.handle_starttag(tag, attrs)
            self._display_math_depth += 1
            return
        if tag in {"ol", "ul"} and not self._table_depth:
            if self._list_tag is not None:
                raise ValueError("Nested HTML lists are not supported.")
            self._flush_text()
            self._list_tag = tag
            self._list_items = []
            return
        if self._list_tag is not None:
            self._handle_list_starttag(tag, attrs)
            return
        if tag == "table":
            if self._table_depth == 0:
                self._flush_text()
                self._rows = []
                self._current_row = None
                self._current_cell = None
            self._table_depth += 1
            return
        if self._table_depth:
            self._handle_table_starttag(tag, attrs)
            return
        values = {
            name.casefold(): value for name, value in attrs if value is not None
        }
        if tag == "math" and values.get("display") == "block":
            self._flush_text()
            self._display_math = InlineHTMLSanitizer()
            self._display_math.handle_starttag(tag, attrs)
            self._display_math_depth = 1
            return
        self._text.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._display_math is not None:
            self._display_math.handle_endtag(tag)
            self._display_math_depth -= 1
            if self._display_math_depth == 0:
                self.blocks.append(TextBlock(html=self._display_math.get_html()))
                self._display_math = None
            return
        if tag in {"ol", "ul"} and self._list_tag == tag:
            self._close_list_item()
            if self._list_items:
                self.blocks.append(
                    ListBlock(ordered=tag == "ol", items=self._list_items)
                )
            self._list_tag = None
            self._list_items = []
            return
        if self._list_tag is not None:
            self._handle_list_endtag(tag)
            return
        if tag == "table" and self._table_depth:
            self._close_cell()
            self._close_row()
            self._table_depth -= 1
            if self._table_depth == 0:
                rows = [row for row in self._rows if row]
                if rows:
                    self.blocks.append(TableBlock(rows=rows))
            return
        if self._table_depth:
            self._handle_table_endtag(tag)
            return
        self._text.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._display_math is not None:
            self._display_math.handle_data(data)
            return
        if self._list_tag is not None:
            if self._current_list_item is not None:
                self._current_list_item.handle_data(data)
            return
        if self._table_depth:
            if not self._drop_stack and self._current_cell is not None:
                self._current_cell.handle_data(data)
            return
        self._text.handle_data(data)

    def get_blocks(self) -> list[ContentBlock]:
        if self._display_math is not None:
            raise ValueError("Display MathML is missing its closing tag.")
        if self._list_tag is not None:
            raise ValueError("HTML list is missing its closing tag.")
        self._flush_text()
        return self.blocks

    def _handle_list_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag == "li":
            self._close_list_item()
            self._current_list_item = InlineHTMLSanitizer()
            values = {name.casefold(): value for name, value in attrs if value is not None}
            tag_value = str(values.get("data-tag", "")).strip().casefold()
            if tag_value:
                if tag_value not in {"to-do", "to-do:completed"}:
                    raise ValueError(
                        "HTML list data-tag must be 'to-do' or 'to-do:completed'."
                    )
                self._current_list_tag = NoteTag(
                    kind="to-do",
                    completed=tag_value.endswith(":completed"),
                )
            else:
                self._current_list_tag = None
            return
        if self._current_list_item is not None:
            self._current_list_item.handle_starttag(tag, attrs)

    def _handle_list_endtag(self, tag: str) -> None:
        if tag == "li":
            self._close_list_item()
            return
        if self._current_list_item is not None:
            self._current_list_item.handle_endtag(tag)

    def _close_list_item(self) -> None:
        if self._current_list_item is None:
            return
        self._list_items.append(
            ListItem(
                html=self._current_list_item.get_html(),
                tag=self._current_list_tag,
            )
        )
        self._current_list_item = None
        self._current_list_tag = None

    def _handle_table_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._drop_stack.append(tag)
            return
        if self._drop_stack:
            return
        if tag == "tr":
            self._close_cell()
            self._close_row()
            self._current_row = []
            return
        if tag in {"td", "th"}:
            if self._current_row is None:
                self._current_row = []
            self._close_cell()
            self._current_cell = InlineHTMLSanitizer()
            self._current_cell_header = tag == "th"
            return
        if self._current_cell is not None:
            self._current_cell.handle_starttag(tag, attrs)

    def _handle_table_endtag(self, tag: str) -> None:
        if self._drop_stack:
            if tag == self._drop_stack[-1]:
                self._drop_stack.pop()
            return
        if tag in {"td", "th"}:
            self._close_cell()
            return
        if tag == "tr":
            self._close_cell()
            self._close_row()
            return
        if self._current_cell is not None:
            self._current_cell.handle_endtag(tag)

    def _flush_text(self) -> None:
        text = self._text.get_html()
        if text:
            self.blocks.append(TextBlock(html=text))
        self._text = InlineHTMLSanitizer()

    def _close_row(self) -> None:
        if self._current_row:
            self._rows.append(self._current_row)
        self._current_row = None

    def _close_cell(self) -> None:
        if self._current_cell is None:
            return
        cell_html = self._current_cell.get_html()
        if cell_html or self._current_row is not None:
            if self._current_row is None:
                self._current_row = []
            self._current_row.append(TableCell(html=cell_html, header=self._current_cell_header))
        self._current_cell = None
        self._current_cell_header = False


def normalize_content(content: str, content_format: str = "plain") -> str:
    """Return OneNote inline HTML for plain text or simple HTML input."""

    if content_format == "plain":
        escaped = html.escape(content, quote=False)
        return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
    if content_format == "html":
        parser = InlineHTMLSanitizer()
        parser.feed(content)
        parser.close()
        return parser.get_html()
    if content_format in {"markdown", "md"}:
        return normalize_content(markdown_to_html(content), "html")
    raise ValueError("content_format must be 'plain', 'html', or 'markdown'.")


def html_content_blocks(content: str) -> list[ContentBlock]:
    parser = OneNoteHTMLBlockParser()
    parser.feed(content)
    parser.close()
    return parser.get_blocks()


def _registry_value(root: int, subkey: str, value_name: str) -> str | None:
    try:
        with winreg.OpenKey(root, subkey) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
            return str(value) if value else None
    except OSError:
        return None


def find_markdig_dll() -> Path:
    """Locate OneMore's bundled Markdig Markdown parser."""

    env_path = os.environ.get("LOCAL_ONENOTE_MARKDIG_DLL")
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path))
    addin_path = _registry_value(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\App Paths\River.OneMoreAddIn.dll",
        "Path",
    )
    if addin_path:
        candidates.append(Path(addin_path).parent / "Markdig.Signed.dll")
    candidates.extend(
        [
            Path(r"C:\Program Files\River\OneMoreAddIn\Markdig.Signed.dll"),
            Path(r"C:\Program Files (x86)\River\OneMoreAddIn\Markdig.Signed.dll"),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "Markdown conversion requires OneMore's Markdig.Signed.dll. "
        "Install OneMore or set LOCAL_ONENOTE_MARKDIG_DLL."
    )


MARKDOWN_POWERSHELL = r'''
$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($env:LOCAL_ONENOTE_MARKDIG_DLL)) {
    throw "LOCAL_ONENOTE_MARKDIG_DLL is not set."
}
if ([string]::IsNullOrWhiteSpace($env:LOCAL_ONENOTE_MARKDOWN_INPUT) -or [string]::IsNullOrWhiteSpace($env:LOCAL_ONENOTE_MARKDOWN_OUTPUT)) {
    throw "Markdown input/output paths are not set."
}
[Reflection.Assembly]::LoadFrom($env:LOCAL_ONENOTE_MARKDIG_DLL) | Out-Null
$builder = [Markdig.MarkdownPipelineBuilder]::new()
[Markdig.MarkdownExtensions]::UseAdvancedExtensions($builder) | Out-Null
$pipeline = $builder.Build()
$markdown = Get-Content -LiteralPath $env:LOCAL_ONENOTE_MARKDOWN_INPUT -Raw -Encoding UTF8
$writer = [System.IO.StringWriter]::new()
[Markdig.Markdown]::ToHtml($markdown, $writer, $pipeline, $null) | Out-Null
$html = $writer.ToString()
[System.IO.File]::WriteAllText($env:LOCAL_ONENOTE_MARKDOWN_OUTPUT, $html, [System.Text.UTF8Encoding]::new($false))
if (!(Test-Path -LiteralPath $env:LOCAL_ONENOTE_MARKDOWN_OUTPUT)) {
    throw "Markdown conversion did not write an HTML output file."
}
Write-Host "markdown-converted"
'''


def markdown_to_html(content: str) -> str:
    """Convert Markdown to HTML through OneMore's bundled Markdig parser."""

    markdig_dll = find_markdig_dll()
    script_path = _write_temp_text(MARKDOWN_POWERSHELL, ".ps1")
    input_path = _write_temp_text(content, ".md")
    output_path = _reserve_temp_path(".html")
    env = os.environ.copy()
    env["LOCAL_ONENOTE_MARKDIG_DLL"] = str(markdig_dll)
    env["LOCAL_ONENOTE_MARKDOWN_INPUT"] = str(input_path)
    env["LOCAL_ONENOTE_MARKDOWN_OUTPUT"] = str(output_path)
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
            text=True,
            capture_output=True,
            timeout=int(os.environ.get("LOCAL_ONENOTE_MARKDOWN_TIMEOUT", "30")),
            env=env,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "Markdown conversion failed."
            raise RuntimeError(message)
        if not output_path.exists():
            raise RuntimeError("Markdown conversion did not write an HTML output file.")
        return output_path.read_text(encoding="utf-8-sig")
    finally:
        _remove_quietly(script_path)
        _remove_quietly(input_path)
        _remove_quietly(output_path)


def _write_temp_text(value: str, suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="local-onenote-mcp-",
        suffix=suffix,
        delete=False,
    )
    with handle:
        handle.write(value)
    return Path(handle.name)


def _reserve_temp_path(suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="local-onenote-mcp-", suffix=suffix, delete=False)
    path = Path(handle.name)
    handle.close()
    path.unlink(missing_ok=True)
    return path


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
