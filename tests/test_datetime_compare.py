"""Pure contracts for Copy/Move Page-root dateTime helpers."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from local_onenote_mcp.page.datetime_compare import (
    build_page_root_datetime_xml,
    page_root_datetime,
    same_utc_second,
    utc_second,
)


def test_utc_second_normalizes_offset_and_fractional_values_to_same_second():
    assert utc_second("2020-02-03T04:05:06.123456+08:00") == "2020-02-02T20:05:06Z"
    assert utc_second("2020-02-02T20:05:06.000Z") == "2020-02-02T20:05:06Z"
    assert utc_second("2020-02-02T20:05:06.999Z") == "2020-02-02T20:05:06Z"
    assert same_utc_second(
        "2020-02-03T04:05:06.5+08:00",
        "2020-02-02T20:05:06Z",
    )


def test_adjacent_utc_seconds_are_not_equal():
    assert same_utc_second("2020-02-02T20:05:06Z", "2020-02-02T20:05:07Z") is False
    assert utc_second("2020-02-02T20:05:06.999Z") != utc_second(
        "2020-02-02T20:05:07.000Z"
    )


@pytest.mark.parametrize(
    "value",
    [None, "", "old", "2020-02-02T20:05:06", "2020-02-02 20:05:06Z"],
)
def test_utc_second_rejects_missing_naive_or_invalid_values(value):
    assert utc_second(value) is None
    assert same_utc_second(value, "2020-02-02T20:05:06Z") is False


def test_page_root_datetime_reads_only_the_page_root():
    xml = (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
        'ID="page" dateTime="2020-02-02T20:05:06Z">'
        '<one:OE dateTime="2019-01-01T00:00:00Z"/></one:Page>'
    )
    assert page_root_datetime(xml) == "2020-02-02T20:05:06Z"
    assert page_root_datetime("<one:Page xmlns:one='http://schemas.microsoft.com/office/onenote/2013/onenote' ID='p'/>") is None


def test_build_page_root_datetime_xml_is_minimal_and_content_free():
    payload = build_page_root_datetime_xml("page-id", "2020-02-02T20:05:06Z")
    root = ET.fromstring(payload)
    assert root.tag.rsplit("}", 1)[-1] == "Page"
    assert root.attrib == {"ID": "page-id", "dateTime": "2020-02-02T20:05:06Z"}
    assert list(root) == []
    with pytest.raises(ValueError, match="invalid"):
        build_page_root_datetime_xml("page-id", "old")
    with pytest.raises(ValueError, match="invalid"):
        build_page_root_datetime_xml("", "2020-02-02T20:05:06Z")
