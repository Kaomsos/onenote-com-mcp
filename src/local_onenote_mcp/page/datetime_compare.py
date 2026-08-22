"""Pure Page-root dateTime helpers for Copy/Move fidelity."""

from __future__ import annotations

from datetime import datetime, timezone
import xml.etree.ElementTree as ET

from ..constants import ONE_NS


def page_root_datetime(xml: str) -> str | None:
    """Return the Page root ``dateTime`` attribute, if present."""

    if not isinstance(xml, str) or not xml.strip():
        return None
    root = ET.fromstring(xml)
    if root.tag.rsplit("}", 1)[-1] != "Page":
        return None
    value = root.attrib.get("dateTime")
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def utc_second(value: str | None) -> str | None:
    """Normalize an offset-aware RFC 3339 value to ``YYYY-MM-DDTHH:MM:SSZ``."""

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if "T" not in text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def same_utc_second(left: str | None, right: str | None) -> bool:
    """Return whether two values fall in the same UTC second."""

    left_second = utc_second(left)
    right_second = utc_second(right)
    return left_second is not None and left_second == right_second


def build_page_root_datetime_xml(page_id: str, date_time: str) -> str:
    """Build a minimal dateTime-only ``UpdatePageContent`` Page root."""

    if not page_id or utc_second(date_time) is None:
        raise ValueError("Page dateTime update payload is invalid.")
    return ET.tostring(
        ET.Element(f"{{{ONE_NS}}}Page", {"ID": page_id, "dateTime": date_time}),
        encoding="unicode",
    )
