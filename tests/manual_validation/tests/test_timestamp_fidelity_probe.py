"""Deterministic contracts for the verified Page ``dateTime`` smoke check."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from local_onenote_mcp.constants import ONE_NS

from tests.manual_validation import test_utils
from tests.manual_validation.mcp_stdio_client import (
    ClientFailure,
    MCPStdioClient,
    ScenarioPolicy,
    TIMESTAMP_FIDELITY_POLICY,
    is_mutation_tool,
)
from tests.manual_validation.runtime import RuntimeOptions
from tests.manual_validation.scenarios import timestamp_fidelity as scenario_module
from tests.manual_validation.scenarios.timestamp_fidelity import (
    PROBE_DATE_TIME,
    TimestampFidelityProbeScenario,
)
from tests.manual_validation.timestamp_fidelity import (
    build_hierarchy_page_datetime_xml,
    build_page_datetime_xml,
    compare_timestamp,
    validate_second_precision_timestamp,
)


def _items(date_times: dict[str, str] | None = None) -> list[dict]:
    dates = date_times or {
        "page-h-id": "2026-01-01T00:00:03Z",
        "page-p-id": "2026-01-01T00:00:04Z",
    }
    return [
        {
            "resource_type": "notebook",
            "id": "notebook-id",
            "name": "Notebook",
            "parent_id": None,
            "created": None,
            "modified": "2026-01-01T00:00:00Z",
        },
        {
            "resource_type": "section",
            "id": "section-id",
            "name": "Timestamp-Section",
            "parent_id": "notebook-id",
            "notebook_id": "notebook-id",
            "created": None,
            "modified": "2026-01-01T00:00:02Z",
        },
        {
            "resource_type": "page",
            "id": "page-h-id",
            "title": "Timestamp-Hierarchy-Page",
            "parent_id": "section-id",
            "notebook_id": "notebook-id",
            "section_id": "section-id",
            "parent_page_id": None,
            "page_level": 1,
            "order": 0,
            "created": dates["page-h-id"],
            "modified": "2026-01-01T00:00:03Z",
        },
        {
            "resource_type": "page",
            "id": "page-p-id",
            "title": "Timestamp-PageContent-Page",
            "parent_id": "section-id",
            "notebook_id": "notebook-id",
            "section_id": "section-id",
            "parent_page_id": None,
            "page_level": 1,
            "order": 1,
            "created": dates["page-p-id"],
            "modified": "2026-01-01T00:00:04Z",
        },
    ]


def _snapshot(date_times: dict[str, str]) -> dict:
    return {
        "notebook_id": "notebook-id",
        "items": _items(date_times),
        "page_body_hashes": {"page-h-id": "body-h", "page-p-id": "body-p"},
        "page_semantic_content_identities": {"page-h-id": {}, "page-p-id": {}},
        "page_objects": {"page-h-id": [], "page-p-id": []},
    }


def _manifest() -> dict:
    values = {item["id"]: item for item in _items()}
    return {
        "notebook": values["notebook-id"],
        "structure": {
            "section_target": values["section-id"],
            "page_hierarchy_target": values["page-h-id"],
            "page_content_target": values["page-p-id"],
        },
    }


def _args() -> SimpleNamespace:
    return SimpleNamespace(notebook_name=None, keep_worksite=False)


def test_second_precision_comparison_and_validation() -> None:
    assert compare_timestamp(PROBE_DATE_TIME, "2020-02-02T20:05:06.000Z")["status"] == "same_instant"
    validate_second_precision_timestamp(PROBE_DATE_TIME)
    with pytest.raises(ValueError, match="whole-second"):
        validate_second_precision_timestamp("2020-02-03T04:05:06.123456+08:00")


def test_page_datetime_xml_builders_bind_exact_page_and_one_attribute() -> None:
    hierarchy = ET.fromstring(
        build_hierarchy_page_datetime_xml(
            _items(), page_id="page-h-id", date_time=PROBE_DATE_TIME
        )
    )
    page = next(node for node in hierarchy.iter() if node.attrib.get("ID") == "page-h-id")
    assert page.attrib["dateTime"] == PROBE_DATE_TIME
    assert "lastModifiedTime" not in page.attrib
    content = ET.fromstring(
        build_page_datetime_xml(page_id="page-p-id", date_time=PROBE_DATE_TIME)
    )
    assert content.tag == f"{{{ONE_NS}}}Page"
    assert content.attrib == {"ID": "page-p-id", "dateTime": PROBE_DATE_TIME}


def test_verified_page_datetime_handlers_keep_xml_inside_the_bridge() -> None:
    hierarchy_xml = f'''<one:Notebooks xmlns:one="{ONE_NS}">
        <one:Notebook ID="notebook-id" name="Notebook">
          <one:Section ID="section-id" name="Section">
            <one:Page ID="page-id" name="Page" pageLevel="1" dateTime="2026-01-01T00:00:03Z" lastModifiedTime="2026-01-01T00:00:03Z"/>
          </one:Section>
        </one:Notebook>
      </one:Notebooks>'''

    class Bridge:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def call(self, operation: str, **kwargs):
            self.calls.append((operation, kwargs))
            if operation == "get_hierarchy":
                return {"xml": hierarchy_xml}
            if operation == "get_page_content":
                return {
                    "xml": f'''<one:Page xmlns:one="{ONE_NS}" ID="page-id"
                        name="Page" dateTime="2026-01-01T00:00:03Z"
                        lastModifiedTime="2026-01-01T00:00:03Z"/>'''
                }
            assert operation == "update_page_content"
            return {"updated": True}

    client = object.__new__(MCPStdioClient)
    client._internal_bridge = Bridge()
    read = client._run_internal_verified_page_datetime_read(
        {
            "notebook_id": "notebook-id",
            "page_id": "page-id",
            "route": "update_page_content",
        }
    )
    assert read == {
        "status": "observed",
        "source": "page_content",
        "attribute_name": "dateTime",
        "date_time": "2026-01-01T00:00:03Z",
    }
    result = client._run_internal_verified_page_datetime(
        {
            "notebook_id": "notebook-id",
            "page_id": "page-id",
            "expected_parent_id": "section-id",
            "expected_hierarchy_modified": "2026-01-01T00:00:03Z",
            "expected_date_time": "2026-01-01T00:00:03Z",
            "route": "update_page_content",
            "date_time": PROBE_DATE_TIME,
        }
    )
    assert result["status"] == "dispatched"
    operation, parameters = client._internal_bridge.calls[-1]
    assert operation == "update_page_content"
    assert ET.fromstring(parameters["xml"]).attrib == {
        "ID": "page-id",
        "dateTime": PROBE_DATE_TIME,
    }
    assert "xml" not in result


def test_timestamp_smoke_verifies_both_proven_page_routes(monkeypatch, tmp_path) -> None:
    state = {
        "page-h-id": "2026-01-01T00:00:03Z",
        "page-p-id": "2026-01-01T00:00:04Z",
    }
    calls: list[dict] = []

    class FakeClient:
        allowed_tools = set(scenario_module.TIMESTAMP_FIDELITY_TOOLS) | {"health_check"}
        policy = TIMESTAMP_FIDELITY_POLICY
        timeout_seconds = 10

        async def call_tool(self, name: str, arguments: dict, **_: object) -> dict:
            calls.append({"name": name, "arguments": deepcopy(arguments)})
            if name == "read_verified_page_datetime":
                return {
                    "status": "observed",
                    "source": (
                        "hierarchy"
                        if arguments["route"] == "update_hierarchy"
                        else "page_content"
                    ),
                    "attribute_name": "dateTime",
                    "date_time": state[arguments["page_id"]],
                }
            assert name == "set_verified_page_datetime"
            state[arguments["page_id"]] = arguments["date_time"]
            return {
                "status": "dispatched",
                "mutation_dispatched": True,
                "bridge_operation": arguments["route"],
                "mutation_attempts": 1,
                "mutation_replayed": False,
            }

    async def fake_capture(_client, _notebook_id):
        return deepcopy(_snapshot(state))

    monkeypatch.setattr(scenario_module, "_capture_timestamp_snapshot", fake_capture)
    monkeypatch.setattr(scenario_module, "render_report", lambda _run_dir: None)
    result = asyncio.run(
        TimestampFidelityProbeScenario().execute(
            _args(), RuntimeOptions(tmp_path, 10, False, False), _manifest(), client=FakeClient(), fixture_result={}
        )
    )

    assert result["status"] == "passed"
    writes = [call for call in calls if call["name"] == "set_verified_page_datetime"]
    assert [call["arguments"]["route"] for call in writes] == [
        "update_hierarchy",
        "update_page_content",
    ]
    assert [case["status"] for case in result["matrix"]["cases"]] == ["verified", "verified"]
    matrix = test_utils.read_json(
        tmp_path / "scenarios" / "timestamp-fidelity-probe" / "timestamp-capability-matrix.json"
    )
    assert matrix["verified_capability"]["supported_precision"] == "whole_seconds"


def test_verified_page_datetime_requires_dedicated_mutation_gate() -> None:
    client = object.__new__(MCPStdioClient)
    client.policy = ScenarioPolicy(writes_enabled=True)
    with pytest.raises(ClientFailure, match="dedicated validation gate"):
        asyncio.run(client._call_internal_verified_page_datetime({}))
    with pytest.raises(ClientFailure, match="dedicated validation gate"):
        asyncio.run(client._call_internal_verified_page_datetime_read({}))
    assert is_mutation_tool("set_verified_page_datetime") is True
    assert is_mutation_tool("read_verified_page_datetime") is False
