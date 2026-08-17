"""Content-free evidence for the public ``get_page_text`` contract."""

from __future__ import annotations

from collections import Counter
import hashlib
import re
from typing import Any, Iterable
import xml.etree.ElementTree as ET

from ...runtime import InvariantFailure


RICH_FORMAT = "sanitized_html_v1"
BOUNDED_RICH_MAX_CHARS = 192
_SCENARIO_CLIENT_METADATA_KEYS = frozenset({"ok", "warnings", "execution"})
_SELECTED_TAGS = (
    "article",
    "h1",
    "section",
    "p",
    "table",
    "tr",
    "td",
    "ol",
    "ul",
    "li",
    "math",
    "strong",
    "b",
    "em",
    "i",
    "span",
)
_UNSAFE_TEXT_MARKERS = (
    "<one:",
    "schemas.microsoft.com/office/onenote",
    "base64,",
    "<script",
    "javascript:",
    "vbscript:",
)


def _local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1].casefold()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvariantFailure(message)


def _rich_projection(
    response: dict[str, Any],
    *,
    expected_features: Iterable[str] = (),
    expected_truncated: bool = False,
    max_chars: int | None = None,
) -> dict[str, Any]:
    html = response.get("html")
    _require(isinstance(html, str), "get_page_text rich mode returned no HTML string.")
    _require(response.get("mode") == "rich", "get_page_text default mode was not rich.")
    _require(
        response.get("format") == RICH_FORMAT,
        "get_page_text rich mode returned an unknown projection format.",
    )
    _require(
        response.get("truncated") is expected_truncated,
        "get_page_text rich truncation state differs from the scenario contract.",
    )
    chars = response.get("chars")
    _require(
        isinstance(chars, int) and not isinstance(chars, bool) and chars >= len(html),
        "get_page_text rich mode returned an invalid character count.",
    )
    if max_chars is not None:
        _require(
            len(html) <= max_chars,
            "get_page_text rich mode exceeded the requested max_chars budget.",
        )

    # The public projection is HTML rather than XHTML. ``br`` is its only
    # allowed void element, so normalize that one shape for a strict tree parse.
    xml_compatible_html = re.sub(
        r"<br(?P<attrs>\s[^>/]*)?>",
        lambda match: f"<br{match.group('attrs') or ''}/>",
        html,
        flags=re.IGNORECASE,
    )
    try:
        root = ET.fromstring(xml_compatible_html)
    except ET.ParseError as exc:
        raise InvariantFailure(
            "get_page_text rich mode returned a malformed HTML projection."
        ) from exc
    _require(
        _local_name(root.tag) == "article"
        and root.attrib.get("data-onenote-projection") == RICH_FORMAT,
        "get_page_text rich mode is missing its sanitized projection wrapper.",
    )

    lowered = html.casefold()
    text_markers_absent = all(marker not in lowered for marker in _UNSAFE_TEXT_MARKERS)
    event_attributes_absent = True
    unsafe_uri_attributes_absent = True
    object_counts: Counter[str] = Counter()
    list_kind_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    tag_semantic_elements = 0
    style_attribute_count = 0
    for node in root.iter():
        tag_counts[_local_name(node.tag)] += 1
        attributes = {str(name).casefold(): str(value) for name, value in node.attrib.items()}
        if any(name.startswith("on") for name in attributes):
            event_attributes_absent = False
        if any(
            name in {"href", "src"}
            and value.strip().casefold().startswith(("javascript:", "vbscript:", "data:"))
            for name, value in attributes.items()
        ):
            unsafe_uri_attributes_absent = False
        object_kind = attributes.get("data-onenote-object")
        if object_kind:
            object_counts[object_kind.casefold()] += 1
        list_kind = attributes.get("data-onenote-list-kind")
        if list_kind:
            list_kind_counts[list_kind.casefold()] += 1
        if any(name.startswith("data-onenote-tag-") for name in attributes):
            tag_semantic_elements += 1
        if "style" in attributes:
            style_attribute_count += 1

    safety = {
        "raw_onenote_xml_absent": text_markers_absent,
        "binary_payload_absent": "base64," not in lowered and "data:" not in lowered,
        "script_markup_absent": "<script" not in lowered,
        "event_attributes_absent": event_attributes_absent,
        "unsafe_uri_attributes_absent": unsafe_uri_attributes_absent,
    }
    _require(all(safety.values()), "get_page_text rich projection failed a safety check.")

    formatting_count = sum(tag_counts[tag] for tag in ("strong", "b", "em", "i"))
    features = {
        "title": tag_counts["h1"] > 0,
        "formatting": formatting_count > 0 or style_attribute_count > 0,
        "table": tag_counts["table"] > 0 and tag_counts["td"] > 0,
        "image": object_counts["image"] > 0,
        "math": tag_counts["math"] > 0,
        "list": tag_counts["li"] > 0 and bool(list_kind_counts),
        "tag": tag_semantic_elements > 0,
    }
    missing = sorted(set(expected_features) - {name for name, found in features.items() if found})
    _require(not missing, f"get_page_text rich projection is missing features: {', '.join(missing)}.")

    visible_text = re.sub(r"\s+", " ", "".join(root.itertext())).strip()
    semantic_signature = {
        "selected_tag_counts": {tag: tag_counts[tag] for tag in _SELECTED_TAGS},
        "object_counts": dict(sorted(object_counts.items())),
        "list_kind_counts": dict(sorted(list_kind_counts.items())),
        "tag_semantic_elements": tag_semantic_elements,
        "style_attribute_count": style_attribute_count,
        "visible_text_chars": len(visible_text),
        "visible_text_sha256": _sha256(visible_text),
    }
    return {
        "mode": "rich",
        "format": RICH_FORMAT,
        "truncated": expected_truncated,
        "source_chars": chars,
        "rendered_chars": len(html),
        "features": features,
        "safety": safety,
        "semantic_signature": semantic_signature,
    }


async def capture_rich_page_text_projection(
    client: Any,
    page_id: str,
    *,
    expected_features: Iterable[str] = (),
) -> dict[str, Any]:
    """Call the public tool with ``mode`` omitted and return content-free evidence."""

    response = await client.call_tool("get_page_text", {"page_id": page_id})
    evidence = _rich_projection(response, expected_features=expected_features)
    evidence["default_mode_argument_omitted"] = True
    return evidence


async def capture_page_text_pair(
    client: Any,
    parent_page_id: str,
    semantic_page_id: str,
    *,
    parent_features: Iterable[str] = ("title", "formatting", "table", "image"),
) -> dict[str, Any]:
    """Capture default-rich evidence for one layered parent/semantic child pair."""

    return {
        "parent": await capture_rich_page_text_projection(
            client,
            parent_page_id,
            expected_features=parent_features,
        ),
        "semantic_child": await capture_rich_page_text_projection(
            client,
            semantic_page_id,
            expected_features=("title", "list", "tag"),
        ),
    }


async def capture_full_page_text_evidence(
    client: Any,
    parent_page_id: str,
    semantic_page_id: str,
) -> dict[str, Any]:
    """Exercise default rich, explicit plain, bounded rich, and safety behavior."""

    pair = await capture_page_text_pair(
        client,
        parent_page_id,
        semantic_page_id,
        parent_features=("title", "formatting", "table", "image", "math"),
    )
    plain = await client.call_tool(
        "get_page_text", {"page_id": parent_page_id, "mode": "plain"}
    )
    plain_business_keys = set(plain) - _SCENARIO_CLIENT_METADATA_KEYS
    _require(
        plain_business_keys == {"text", "chars"}
        and isinstance(plain.get("text"), str),
        "get_page_text explicit plain mode returned an unexpected response shape.",
    )
    plain_chars = plain.get("chars")
    _require(
        isinstance(plain_chars, int)
        and not isinstance(plain_chars, bool)
        and plain_chars == len(plain["text"])
        and plain_chars > 0,
        "get_page_text explicit plain mode returned an invalid character count.",
    )

    bounded = await client.call_tool(
        "get_page_text",
        {"page_id": parent_page_id, "max_chars": BOUNDED_RICH_MAX_CHARS},
    )
    bounded_evidence = _rich_projection(
        bounded,
        expected_truncated=True,
        max_chars=BOUNDED_RICH_MAX_CHARS,
    )
    bounded_evidence["default_mode_argument_omitted"] = True
    return {
        "schema_version": 1,
        "tool": "get_page_text",
        "default_rich": pair,
        "explicit_plain": {
            "response_keys": sorted(plain_business_keys),
            "scenario_client_metadata_keys": sorted(
                set(plain) & _SCENARIO_CLIENT_METADATA_KEYS
            ),
            "chars": plain_chars,
            "visible_text_sha256": _sha256(plain["text"]),
            "content_persisted": False,
        },
        "bounded_default_rich": {
            **bounded_evidence,
            "max_chars": BOUNDED_RICH_MAX_CHARS,
        },
        "content_persisted": False,
    }


def assert_page_text_pair_equivalent(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    """Require source/target semantic signatures to match without retaining content."""

    roles = ("parent", "semantic_child")
    matches = {
        role: source.get(role, {}).get("semantic_signature")
        == target.get(role, {}).get("semantic_signature")
        for role in roles
    }
    _require(
        all(matches.values()),
        f"{label} changed the get_page_text semantic projection.",
    )
    return {"label": label, "semantic_signatures_match": matches, "passed": True}


def assert_rich_page_text_equivalent(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    """Require two single-Page semantic signatures to match."""

    matched = source.get("semantic_signature") == target.get("semantic_signature")
    _require(matched, f"{label} changed the get_page_text semantic projection.")
    return {"label": label, "semantic_signature_match": True, "passed": True}


__all__ = [
    "BOUNDED_RICH_MAX_CHARS",
    "assert_page_text_pair_equivalent",
    "assert_rich_page_text_equivalent",
    "capture_full_page_text_evidence",
    "capture_page_text_pair",
    "capture_rich_page_text_projection",
]
