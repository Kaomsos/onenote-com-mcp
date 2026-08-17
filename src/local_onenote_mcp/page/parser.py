"""Parse OneNote Page XML into visible text, title, and content objects."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html import escape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit


DELETABLE_PAGE_OBJECT_TYPES = {"Outline", "Image", "InkDrawing", "FileAttachment", "InsertedFile", "MediaFile"}

RICH_HTML_FORMAT = "sanitized_html_v1"
MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML"
RICH_HTML_MATHML_TAGS = {
    "math",
    "mfrac",
    "mi",
    "mn",
    "mo",
    "mrow",
    "msqrt",
    "msub",
    "msubsup",
    "msup",
}
RICH_HTML_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "font",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "i",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "span",
    "strike",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
} | RICH_HTML_MATHML_TAGS
RICH_HTML_VOID_TAGS = {"br"}
RICH_HTML_STYLE_PROPERTIES = {
    "background-color",
    "color",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "text-align",
    "text-decoration",
}
RICH_HTML_GLOBAL_ATTRIBUTES = {"lang", "style", "title"}
RICH_HTML_TAG_ATTRIBUTES = {
    "a": {"href"},
    "font": {"color", "face", "size"},
    "math": {"display", "xmlns"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}
RICH_HTML_SAFE_LINK_SCHEMES = {"", "http", "https", "mailto", "onenote"}
MATHML_CONDITIONAL_COMMENT_PATTERN = re.compile(
    r"^\s*\[if\s+mathML\]\s*>\s*"
    r"(?P<math><(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\b[^>]*>"
    r"(?:(?!<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\b).)*?"
    r"</(?:[A-Za-z_][A-Za-z0-9_.-]*:)?math\s*>)"
    r"\s*<!\s*\[endif\]\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)


def _render_safe_mathml_fragment(fragment: str) -> str | None:
    """Canonicalize one complete namespaced MathML root to safe HTML."""

    try:
        root = ET.fromstring(fragment)
    except ET.ParseError:
        return None

    def render(node: ET.Element, *, is_root: bool = False) -> str | None:
        namespace, separator, kind = node.tag.rpartition("}")
        if not separator:
            return None
        namespace = namespace.removeprefix("{")
        kind = kind.casefold()
        if namespace != MATHML_NAMESPACE or kind not in RICH_HTML_MATHML_TAGS:
            return None

        rendered_attributes = ""
        for name, value in node.attrib.items():
            attribute = local_name(name).casefold()
            if not is_root or kind != "math" or attribute != "display" or value != "block":
                return None
            rendered_attributes = ' display="block"'
        if is_root:
            rendered_attributes += f' xmlns="{MATHML_NAMESPACE}"'

        children: list[str] = []
        for child in list(node):
            rendered_child = render(child)
            if rendered_child is None:
                return None
            children.append(rendered_child)
            if child.tail:
                children.append(escape(child.tail))
        return (
            f"<{kind}{rendered_attributes}>"
            f"{escape(node.text or '')}{''.join(children)}"
            f"</{kind}>"
        )

    return render(root, is_root=True)


def _safe_style(value: str) -> str:
    declarations: list[str] = []
    for raw in value.split(";"):
        if ":" not in raw:
            continue
        name, declaration_value = raw.split(":", 1)
        name = name.strip().casefold()
        declaration_value = declaration_value.strip()
        lowered = declaration_value.casefold()
        if (
            name not in RICH_HTML_STYLE_PROPERTIES
            or not declaration_value
            or any(token in lowered for token in ("expression", "url(", "javascript:", "data:"))
            or any(character in declaration_value for character in "<>\x00\r\n")
        ):
            continue
        declarations.append(f"{name}: {declaration_value}")
    return "; ".join(sorted(declarations))


def _safe_rich_attribute(tag: str, name: str, value: str | None) -> tuple[str, str] | None:
    name = name.casefold()
    if value is None:
        return None
    allowed = RICH_HTML_GLOBAL_ATTRIBUTES | RICH_HTML_TAG_ATTRIBUTES.get(tag, set())
    if name not in allowed:
        return None
    if name == "style":
        value = _safe_style(value)
        if not value:
            return None
    elif name == "href":
        if urlsplit(value.strip()).scheme.casefold() not in RICH_HTML_SAFE_LINK_SCHEMES:
            return None
        value = value.strip()
    elif any(character in value for character in "<>\x00\r\n"):
        return None
    return name, value


class RichHTMLSanitizer(HTMLParser):
    """Allowlist OneNote inline HTML without exposing executable markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.open_tags: list[str] = []
        self.suppressed_depth = 0
        self.mathml_prefix_depths: dict[str, int] = {}

    def _normalize_mathml_start_tag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> tuple[str, list[tuple[str, str | None]]]:
        prefix, separator, kind = tag.partition(":")
        if not separator or kind not in RICH_HTML_MATHML_TAGS:
            return tag, attrs
        declaration = f"xmlns:{prefix}"
        if kind == "math" and any(
            name.casefold() == declaration and value == MATHML_NAMESPACE
            for name, value in attrs
        ):
            self.mathml_prefix_depths[prefix] = (
                self.mathml_prefix_depths.get(prefix, 0) + 1
            )
            attrs = [
                ("xmlns", value) if name.casefold() == declaration else (name, value)
                for name, value in attrs
            ]
        if self.mathml_prefix_depths.get(prefix, 0):
            return kind, attrs
        return tag, attrs

    def _normalize_mathml_end_tag(self, tag: str) -> tuple[str, str | None]:
        prefix, separator, kind = tag.partition(":")
        if (
            separator
            and kind in RICH_HTML_MATHML_TAGS
            and self.mathml_prefix_depths.get(prefix, 0)
        ):
            return kind, prefix if kind == "math" else None
        return tag, None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if self.suppressed_depth:
            self.suppressed_depth += 1
            return
        if tag in {"script", "style"}:
            self.suppressed_depth = 1
            return
        tag, attrs = self._normalize_mathml_start_tag(tag, attrs)
        if tag not in RICH_HTML_TAGS:
            return
        safe_attrs = sorted(
            candidate
            for candidate in (
                _safe_rich_attribute(tag, name, value) for name, value in attrs
            )
            if candidate is not None
        )
        rendered_attrs = "".join(
            f' {name}="{escape(value, quote=True)}"' for name, value in safe_attrs
        )
        self.parts.append(f"<{tag}{rendered_attrs}>")
        if tag not in RICH_HTML_VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in RICH_HTML_VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self.suppressed_depth:
            self.suppressed_depth -= 1
            return
        tag, closed_mathml_prefix = self._normalize_mathml_end_tag(tag)
        if tag not in RICH_HTML_TAGS or tag in RICH_HTML_VOID_TAGS:
            return
        if tag not in self.open_tags:
            return
        while self.open_tags:
            current = self.open_tags.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break
        if closed_mathml_prefix is not None:
            remaining = self.mathml_prefix_depths[closed_mathml_prefix] - 1
            if remaining:
                self.mathml_prefix_depths[closed_mathml_prefix] = remaining
            else:
                del self.mathml_prefix_depths[closed_mathml_prefix]

    def handle_comment(self, data: str) -> None:
        if self.suppressed_depth:
            return
        match = MATHML_CONDITIONAL_COMMENT_PATTERN.fullmatch(data)
        if match is None:
            return
        rendered = _render_safe_mathml_fragment(match.group("math"))
        if rendered is not None:
            self.parts.append(rendered)

    def handle_data(self, data: str) -> None:
        if not self.suppressed_depth and data:
            self.parts.append(escape(data))

    def close(self) -> None:
        super().close()
        while self.open_tags:
            self.parts.append(f"</{self.open_tags.pop()}>")

    def html(self) -> str:
        return "".join(self.parts)


def sanitize_rich_html_fragment(fragment: str) -> str:
    parser = RichHTMLSanitizer()
    parser.feed(fragment or "")
    parser.close()
    return parser.html()


class _RichHTMLTruncator(HTMLParser):
    def __init__(self, max_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_chars = max(0, max_chars)
        self.parts: list[str] = []
        self.open_tags: list[str] = []
        self.stopped = False
        self.current_length = 0

    def _length(self) -> int:
        return self.current_length

    def _append(self, token: str) -> None:
        self.parts.append(token)
        self.current_length += len(token)

    @staticmethod
    def _closing_length(tags: list[str]) -> int:
        return sum(len(tag) + 3 for tag in tags)

    def _fits(self, token: str, stack: list[str], *, marker: bool = True) -> bool:
        return (
            self._length()
            + len(token)
            + self._closing_length(stack)
            + (1 if marker else 0)
            <= self.max_chars
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.stopped:
            return
        tag = tag.casefold()
        rendered_attrs = "".join(
            f' {name}="{escape(value or "", quote=True)}"'
            for name, value in attrs
        )
        token = f"<{tag}{rendered_attrs}>"
        next_stack = self.open_tags if tag in RICH_HTML_VOID_TAGS else [*self.open_tags, tag]
        if not self._fits(token, next_stack):
            self.stopped = True
            return
        self._append(token)
        if tag not in RICH_HTML_VOID_TAGS:
            self.open_tags.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.stopped:
            return
        tag = tag.casefold()
        if not self.open_tags or self.open_tags[-1] != tag:
            self.stopped = True
            return
        self.open_tags.pop()
        self._append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.stopped or not data:
            return
        rendered = escape(data)
        if self._fits(rendered, self.open_tags):
            self._append(rendered)
            return
        low, high = 0, len(data)
        while low < high:
            middle = (low + high + 1) // 2
            if self._fits(escape(data[:middle]), self.open_tags):
                low = middle
            else:
                high = middle - 1
        if low:
            self._append(escape(data[:low]))
        self.stopped = True

    def html(self) -> str:
        if self.stopped and self._length() + self._closing_length(self.open_tags) < self.max_chars:
            self._append("…")
        while self.open_tags:
            self._append(f"</{self.open_tags.pop()}>")
        return "".join(self.parts)


def truncate_rich_html(html: str, max_chars: int) -> tuple[str, bool]:
    if len(html) <= max_chars:
        return html, False
    parser = _RichHTMLTruncator(max_chars)
    parser.feed(html)
    parser.close()
    return parser.html(), True


class HTMLTextExtractor(HTMLParser):
    """Extract readable text from a OneNote T element's inline HTML fragment."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._newline()

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts)
        value = value.replace("\x00", "")
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    def _newline(self) -> None:
        if not self.parts or not self.parts[-1].endswith("\n"):
            self.parts.append("\n")


def html_fragment_to_text(fragment: str) -> str:
    parser = HTMLTextExtractor()
    parser.feed(fragment or "")
    parser.close()
    return parser.text()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_xml(xml: str) -> ET.Element:
    return ET.fromstring(xml.encode("utf-8"))


def rich_html_from_page_xml(xml: str) -> str:
    """Project Page text structure as bounded, sanitized HTML rather than raw XML."""

    root = parse_xml(xml)
    tag_definitions = {
        node.attrib["index"]: {
            "type": node.attrib.get("type", ""),
            "symbol": node.attrib.get("symbol", ""),
            "name": node.attrib.get("name", ""),
        }
        for node in root.iter()
        if local_name(node.tag) == "TagDef" and "index" in node.attrib
    }

    def render_text_nodes(node: ET.Element) -> str:
        return "".join(
            sanitize_rich_html_fragment(candidate.text or "")
            for candidate in node.iter()
            if local_name(candidate.tag) == "T" and candidate.text
        )

    def render_tag_attributes(node: ET.Element) -> str:
        tag = next(
            (child for child in list(node) if local_name(child.tag) == "Tag"),
            None,
        )
        if tag is None:
            return ""
        definition = tag_definitions.get(tag.attrib.get("index", ""), {})
        attributes = {
            "data-onenote-tag-completed": tag.attrib.get("completed", "false").casefold(),
            "data-onenote-tag-disabled": tag.attrib.get("disabled", "false").casefold(),
            "data-onenote-tag-name": definition.get("name", ""),
            "data-onenote-tag-symbol": definition.get("symbol", ""),
            "data-onenote-tag-type": definition.get("type", ""),
        }
        return "".join(
            f' {name}="{escape(value, quote=True)}"'
            for name, value in sorted(attributes.items())
            if value
        )

    def render_table(table: ET.Element) -> str:
        rows: list[str] = []
        for row in (node for node in list(table) if local_name(node.tag) == "Row"):
            cells = [
                f"<td>{render_children(cell)}</td>"
                for cell in list(row)
                if local_name(cell.tag) == "Cell"
            ]
            rows.append(f"<tr>{''.join(cells)}</tr>")
        return f"<table><tbody>{''.join(rows)}</tbody></table>"

    def render_oe(oe: ET.Element) -> str:
        content: list[str] = []
        nested: list[str] = []
        list_kind = ""
        has_inline_content = False
        has_block_content = False
        for child in list(oe):
            kind = local_name(child.tag)
            if kind == "T":
                content.append(sanitize_rich_html_fragment(child.text or ""))
                has_inline_content = True
            elif kind == "Table":
                content.append(render_table(child))
                has_block_content = True
            elif kind == "List":
                marker = next(iter(child), None)
                list_kind = local_name(marker.tag).casefold() if marker is not None else "list"
            elif kind == "OEChildren":
                nested.append(render_children(child))
                has_block_content = True
            elif kind in {"Image", "InkDrawing", "FileAttachment", "InsertedFile", "MediaFile"}:
                content.append(
                    f'<span data-onenote-object="{escape(kind.casefold(), quote=True)}"></span>'
                )
        attributes = render_tag_attributes(oe)
        body = f"{''.join(content)}{''.join(nested)}"
        if list_kind:
            list_tag = "ol" if list_kind == "number" else "ul"
            return (
                f'<{list_tag} data-onenote-list-kind="{escape(list_kind, quote=True)}">'
                f"<li{attributes}>{body}</li></{list_tag}>"
            )
        if has_block_content and not has_inline_content and not attributes:
            return body
        return f"<p{attributes}>{body}</p>"

    def render_children(node: ET.Element) -> str:
        rendered: list[str] = []
        for child in list(node):
            kind = local_name(child.tag)
            if kind == "OE":
                rendered.append(render_oe(child))
            elif kind == "OEChildren":
                rendered.append(render_children(child))
            elif kind == "Table":
                rendered.append(render_table(child))
            elif kind == "T":
                rendered.append(sanitize_rich_html_fragment(child.text or ""))
            elif kind in {"Image", "InkDrawing", "FileAttachment", "InsertedFile", "MediaFile"}:
                rendered.append(
                    f'<span data-onenote-object="{escape(kind.casefold(), quote=True)}"></span>'
                )
        return "".join(rendered)

    body: list[str] = []
    for child in list(root):
        kind = local_name(child.tag)
        if kind == "Title":
            title = render_text_nodes(child)
            if title:
                body.append(f"<h1>{title}</h1>")
        elif kind == "Outline":
            body.append(f"<section>{render_children(child)}</section>")
        elif kind in {"Image", "InkDrawing", "FileAttachment", "InsertedFile", "MediaFile"}:
            body.append(
                f'<section><span data-onenote-object="{escape(kind.casefold(), quote=True)}"></span></section>'
            )
    return (
        f'<article data-onenote-projection="{RICH_HTML_FORMAT}">'
        f"{''.join(body)}</article>"
    )


def text_from_page_xml(xml: str) -> str:
    root = parse_xml(xml)
    texts = []
    for node in root.iter():
        if local_name(node.tag) == "T" and node.text:
            texts.append(html_fragment_to_text(node.text))
    return "\n\n".join(text for text in texts if text).strip()


def title_from_page_xml(xml: str) -> str | None:
    root = parse_xml(xml)
    for title in root.iter():
        if local_name(title.tag) != "Title":
            continue
        for node in title.iter():
            if local_name(node.tag) == "T" and node.text:
                value = html_fragment_to_text(node.text)
                if value:
                    return value
    return None


def collect_page_objects(xml: str) -> list[dict[str, Any]]:
    root = parse_xml(xml)
    objects = []
    content_without_own_id = {"Image", "FileAttachment", "InsertedFile", "MediaFile"}

    def walk(
        node: ET.Element,
        container_object_id: str | None = None,
        deletable_container_id: str | None = None,
        in_title: bool = False,
    ) -> None:
        kind = local_name(node.tag)
        next_in_title = in_title or kind == "Title"
        object_id = node.attrib.get("objectID") or node.attrib.get("ID")
        next_container_id = object_id or container_object_id
        delete_supported = kind in DELETABLE_PAGE_OBJECT_TYPES and bool(object_id)
        next_deletable_container_id = object_id if delete_supported else deletable_container_id

        if not next_in_title and kind != "Page" and (object_id or kind in content_without_own_id):
            record: dict[str, Any] = {"type": kind}
            if object_id:
                record["object_id"] = object_id
            elif container_object_id:
                record["container_object_id"] = container_object_id
            if container_object_id and object_id != container_object_id:
                record["parent_object_id"] = container_object_id
            record["delete_supported"] = delete_supported
            if delete_supported and object_id:
                record["delete_object_id"] = object_id
            elif deletable_container_id:
                record["delete_object_id"] = deletable_container_id
            callback_id = node.attrib.get("callbackID")
            if not callback_id:
                # OneNote 2013 XML commonly represents the binary handle as a
                # direct child, for example
                # ``<Image><CallbackID callbackID="..."/></Image>``.  Some
                # inputs and older shapes put the same attribute directly on
                # the content element, so accept both without treating the
                # CallbackID metadata node as a separate public object.
                callback_id = next(
                    (
                        child.attrib.get("callbackID")
                        for child in list(node)
                        if local_name(child.tag) == "CallbackID"
                        and child.attrib.get("callbackID")
                    ),
                    None,
                )
            if callback_id:
                record["callback_id"] = callback_id
            if "format" in node.attrib:
                record["format"] = node.attrib["format"]
            objects.append(record)

        for child in list(node):
            walk(child, next_container_id, next_deletable_container_id, next_in_title)

    walk(root)
    return objects
