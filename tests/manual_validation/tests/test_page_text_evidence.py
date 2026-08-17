from __future__ import annotations

import asyncio
import json

import pytest

from tests.manual_validation.runtime import InvariantFailure
from tests.manual_validation.scenarios.common.page_text_evidence import (
    BOUNDED_RICH_MAX_CHARS,
    assert_page_text_pair_equivalent,
    capture_full_page_text_evidence,
    capture_page_text_pair,
    capture_rich_page_text_projection,
)


PARENT_HTML = (
    '<article data-onenote-projection="sanitized_html_v1">'
    '<h1>01-Source-Parent</h1><section><p>'
    '<span style="font-weight: bold">LOCAL_ONENOTE_MCP_COPY_FIXTURE_V1</span><br>'
    '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>'
    '</p><table><tbody><tr><td><p>Cell</p></td></tr></tbody></table>'
    '<p><span data-onenote-object="image"></span></p></section></article>'
)
CHILD_HTML = (
    '<article data-onenote-projection="sanitized_html_v1">'
    '<h1>02-Source-Child</h1><section>'
    '<ol data-onenote-list-kind="number"><li '
    'data-onenote-tag-completed="false" data-onenote-tag-type="0">Item</li></ol>'
    '</section></article>'
)
BOUNDED_HTML = (
    '<article data-onenote-projection="sanitized_html_v1">'
    '<h1>01-Source-Parent</h1><section><p>LOCAL_ONENOTE…</p></section></article>'
)


def rich(html: str, *, truncated: bool = False, chars: int | None = None) -> dict:
    return {
        "html": html,
        "chars": len(html) if chars is None else chars,
        "mode": "rich",
        "format": "sanitized_html_v1",
        "truncated": truncated,
    }


class FullClient:
    def __init__(self, *, include_runtime_metadata: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.include_runtime_metadata = include_runtime_metadata

    async def call_tool(self, tool: str, arguments: dict) -> dict:
        self.calls.append((tool, arguments))
        page_id = arguments["page_id"]
        if arguments.get("mode") == "plain":
            text = "01-Source-Parent LOCAL_ONENOTE_MCP_COPY_FIXTURE_V1 Cell"
            response = {"text": text, "chars": len(text)}
            if self.include_runtime_metadata:
                response.update({"ok": True, "warnings": [], "execution": {}})
            return response
        if "max_chars" in arguments:
            assert arguments["max_chars"] == BOUNDED_RICH_MAX_CHARS
            return rich(BOUNDED_HTML, truncated=True, chars=len(PARENT_HTML))
        return rich(PARENT_HTML if page_id == "parent-id" else CHILD_HTML)


def test_full_evidence_omits_mode_for_default_calls_and_persists_no_content() -> None:
    client = FullClient(include_runtime_metadata=True)

    evidence = asyncio.run(
        capture_full_page_text_evidence(client, "parent-id", "child-id")
    )

    assert client.calls == [
        ("get_page_text", {"page_id": "parent-id"}),
        ("get_page_text", {"page_id": "child-id"}),
        ("get_page_text", {"page_id": "parent-id", "mode": "plain"}),
        (
            "get_page_text",
            {"page_id": "parent-id", "max_chars": BOUNDED_RICH_MAX_CHARS},
        ),
    ]
    serialized = json.dumps(evidence, ensure_ascii=False)
    assert "01-Source-Parent" not in serialized
    assert "LOCAL_ONENOTE_MCP_COPY_FIXTURE_V1" not in serialized
    assert "<article" not in serialized
    assert "parent-id" not in serialized
    assert evidence["default_rich"]["parent"]["features"] == {
        "title": True,
        "formatting": True,
        "table": True,
        "image": True,
        "math": True,
        "list": False,
        "tag": False,
    }
    assert evidence["bounded_default_rich"]["rendered_chars"] <= BOUNDED_RICH_MAX_CHARS
    assert evidence["explicit_plain"]["response_keys"] == ["chars", "text"]
    assert evidence["explicit_plain"]["scenario_client_metadata_keys"] == [
        "execution",
        "ok",
        "warnings",
    ]


def test_full_evidence_rejects_unknown_plain_business_fields() -> None:
    class Client(FullClient):
        async def call_tool(self, tool: str, arguments: dict) -> dict:
            result = await super().call_tool(tool, arguments)
            if arguments.get("mode") == "plain":
                result["unexpected"] = True
            return result

    with pytest.raises(InvariantFailure, match="unexpected response shape"):
        asyncio.run(capture_full_page_text_evidence(Client(), "parent-id", "child-id"))


def test_pair_equivalence_uses_content_free_semantic_signatures() -> None:
    source = asyncio.run(capture_page_text_pair(FullClient(), "parent-id", "child-id"))
    target = asyncio.run(capture_page_text_pair(FullClient(), "parent-id", "child-id"))

    comparison = assert_page_text_pair_equivalent(
        source, target, label="copy-section/same-notebook"
    )

    assert comparison["passed"] is True


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"text": "plain", "chars": 5}, "no HTML"),
        (
            {**rich(PARENT_HTML), "format": "unknown"},
            "unknown projection format",
        ),
        (rich(PARENT_HTML.replace("</article>", "")), "malformed HTML"),
        (
            rich(PARENT_HTML.replace("Cell", '<script onclick="x()">bad</script>')),
            "safety check",
        ),
    ],
)
def test_default_rich_projection_fails_closed(response: dict, message: str) -> None:
    class Client:
        async def call_tool(self, _tool: str, _arguments: dict) -> dict:
            return response

    with pytest.raises(InvariantFailure, match=message):
        asyncio.run(capture_rich_page_text_projection(Client(), "page-id"))


def test_pair_equivalence_rejects_visible_text_drift() -> None:
    source = asyncio.run(capture_page_text_pair(FullClient(), "parent-id", "child-id"))

    class DriftClient(FullClient):
        async def call_tool(self, tool: str, arguments: dict) -> dict:
            result = await super().call_tool(tool, arguments)
            if arguments["page_id"] == "child-id":
                return rich(CHILD_HTML.replace("Item", "Changed"))
            return result

    target = asyncio.run(capture_page_text_pair(DriftClient(), "parent-id", "child-id"))
    with pytest.raises(InvariantFailure, match="changed the get_page_text"):
        assert_page_text_pair_equivalent(source, target, label="copy-section/cross-notebook")
