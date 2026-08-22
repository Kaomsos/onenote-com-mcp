"""Content-free helpers for the verified Page ``dateTime`` smoke check."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import xml.etree.ElementTree as ET

from local_onenote_mcp.constants import ONE_NS
from local_onenote_mcp.hierarchy import display_name


def normalize_timestamp(value: str | None) -> str | None:
    """Return an exact UTC instant without a time tolerance."""

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def compare_timestamp(requested: str, observed: str | None) -> dict[str, Any]:
    """Compare semantic instants while retaining original, content-free evidence."""

    requested_utc = normalize_timestamp(requested)
    observed_utc = normalize_timestamp(observed)
    if requested_utc is None:
        raise ValueError("Requested timestamp must be an offset-aware RFC 3339 value.")
    if observed is None:
        status = "missing"
    elif observed_utc is None:
        status = "invalid"
    elif observed_utc == requested_utc:
        status = "same_instant"
    else:
        status = "mismatch"
    return {
        "requested": requested,
        "requested_utc": requested_utc,
        "observed": observed,
        "observed_utc": observed_utc,
        "status": status,
    }


def validate_second_precision_timestamp(value: str) -> None:
    """Admit only the exact second precision proven by the human-gated run."""

    normalized = normalize_timestamp(value)
    if normalized is None:
        raise ValueError("Page dateTime must be an offset-aware RFC 3339 value.")
    if not normalized.endswith(".000000Z"):
        raise ValueError("Verified Page dateTime smoke checks require whole-second values.")


def _chain(catalog: list[dict[str, Any]], target_id: str) -> list[dict[str, Any]]:
    by_id = {str(item.get("id", "")): item for item in catalog}
    target = by_id.get(target_id)
    if target is None:
        raise ValueError("Verified Page dateTime target is missing from the hierarchy catalog.")
    chain = [target]
    seen = {target_id}
    parent_id = target.get("parent_id")
    while parent_id:
        parent_key = str(parent_id)
        if parent_key in seen:
            raise ValueError("Verified Page dateTime hierarchy contains an ancestor cycle.")
        parent = by_id.get(parent_key)
        if parent is None:
            raise ValueError("Verified Page dateTime hierarchy is missing a target ancestor.")
        chain.append(parent)
        seen.add(parent_key)
        parent_id = parent.get("parent_id")
    chain.reverse()
    if not chain or chain[0].get("resource_type") != "notebook":
        raise ValueError("Verified Page dateTime target is not rooted in an exact Notebook.")
    return chain


def build_hierarchy_page_datetime_xml(
    catalog: list[dict[str, Any]],
    *,
    page_id: str,
    date_time: str,
) -> str:
    """Build the minimal ancestor-complete Page ``dateTime`` hierarchy update."""

    validate_second_precision_timestamp(date_time)
    tags = {
        "notebook": "Notebook",
        "section_group": "SectionGroup",
        "section": "Section",
        "page": "Page",
    }
    root = ET.Element(f"{{{ONE_NS}}}Notebooks")
    current = root
    for item in _chain(catalog, page_id):
        resource_type = str(item.get("resource_type", ""))
        tag = tags.get(resource_type)
        object_id = str(item.get("id", ""))
        name = display_name(item)
        if tag is None or not object_id or not name:
            raise ValueError("Verified Page dateTime hierarchy item is incomplete.")
        attributes = {"ID": object_id, "name": name}
        if resource_type == "page":
            attributes["pageLevel"] = str(max(1, int(item.get("page_level") or 1)))
        if object_id == page_id:
            attributes["dateTime"] = date_time
        current = ET.SubElement(current, f"{{{ONE_NS}}}{tag}", attributes)
    return ET.tostring(root, encoding="unicode")


def build_page_datetime_xml(*, page_id: str, date_time: str) -> str:
    """Build the minimal Page-content ``dateTime`` update."""

    if not page_id:
        raise ValueError("Verified Page dateTime target is invalid.")
    validate_second_precision_timestamp(date_time)
    return ET.tostring(
        ET.Element(
            f"{{{ONE_NS}}}Page",
            {"ID": page_id, "dateTime": date_time},
        ),
        encoding="unicode",
    )


__all__ = [
    "build_hierarchy_page_datetime_xml",
    "build_page_datetime_xml",
    "compare_timestamp",
    "normalize_timestamp",
    "validate_second_precision_timestamp",
]
