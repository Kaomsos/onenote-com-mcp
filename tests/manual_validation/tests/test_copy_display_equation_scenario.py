"""Pure execution contracts for the non-interactive DisplayEquation scenario."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager

from tests.manual_validation.runtime import RuntimeOptions
from tests.manual_validation.scenarios.common import copy_runtime


def _page_xml() -> str:
    one = "http://schemas.microsoft.com/office/onenote/2013/onenote"
    math = "http://www.w3.org/1998/Math/MathML"
    return (
        f'<one:Page xmlns:one="{one}"><one:Title><one:OE><one:T>Display</one:T>'
        "</one:OE></one:Title><one:Outline><one:OEChildren>"
        "<one:OE><one:T>base</one:T></one:OE><one:OE><one:T>"
        "&lt;span style='font-family:Calibri' lang='zh-CN'&gt;&lt;br /&gt;&lt;/span&gt;"
        f'&lt;math xmlns="{math}" display="block"&gt;&lt;mi&gt;x&lt;/mi&gt;'
        "&lt;/math&gt;</one:T></one:OE></one:OEChildren></one:Outline></one:Page>"
    )


def _snapshot(page_ids: list[str]) -> dict:
    items = [
        {
            "id": "section",
            "resource_type": "section",
            "name": "Source",
            "parent_id": "notebook",
            "notebook_id": "notebook",
        }
    ]
    for order, page_id in enumerate(page_ids):
        items.append(
            {
                "id": page_id,
                "resource_type": "page",
                "title": "Source" if page_id == "source" else page_id,
                "name": "Source" if page_id == "source" else page_id,
                "section_id": "section",
                "parent_id": "section",
                "parent_page_id": None,
                "page_level": 1,
                "order": order,
                "modified": f"modified-{page_id}",
            }
        )
    return {
        "notebook_id": "notebook",
        "items": items,
        "page_hashes": {page_id: f"hash-{page_id}" for page_id in page_ids},
        "page_objects": {
            page_id: [{"kind": "Outline", "can_delete": True}]
            for page_id in page_ids
        },
        "page_capability_projections": {
            page_id: {
                "capabilities": [
                    "DisplayEquation",
                    "Image",
                    "Outline",
                    "RichText",
                    "Table",
                ],
                "unknown_nodes": [],
                "unsupported_page_roots": [],
                "complete": True,
            }
            for page_id in page_ids
        },
    }


def test_programmatic_display_equation_runs_three_verified_hops_and_restores(
    monkeypatch,
    tmp_path,
) -> None:
    snapshots = iter(
        [
            _snapshot(["source"]),
            _snapshot(["source", "target-1"]),
            _snapshot(["source", "target-1", "target-2"]),
            _snapshot(["source", "target-1", "target-2", "target-3"]),
            _snapshot(["source"]),
        ]
    )
    copy_index = 0
    deleted: list[str] = []

    @asynccontextmanager
    async def fake_scenario_client(client, **_kwargs):
        yield client

    async def fake_capture(_client, _notebook_id):
        return next(snapshots)

    async def fake_copy(_client, _tool, arguments, evidence_path):
        nonlocal copy_index
        copy_index += 1
        source_id = str(arguments["page_id"])
        target_id = f"target-{copy_index}"
        result = {
            "item": {
                "id": target_id,
                "resource_type": "page",
                "title": arguments["destination_title"],
                "section_id": "section",
            },
            "copy_report": {
                "planning": {
                    "include_descendants": False,
                    "content_capabilities": [
                        "DisplayEquation",
                        "Image",
                        "Outline",
                        "RichText",
                        "Table",
                    ],
                },
                "id_map": {source_id: target_id},
                "verified": True,
                "lossless": True,
                "copy_contract_satisfied": True,
                "page_results": [
                    {
                        "normalizations": {
                            "display_equation_empty_spans_removed": 1,
                            "redundant_breaks_before_display_mathml_removed": 1,
                        },
                        "equivalence": {
                            "equivalent": True,
                            "verification_tier": "semantic_display_equation",
                            "display_equation_comparison": {"passed": True},
                        },
                    }
                ],
            },
        }
        copy_runtime.write_json(evidence_path, result)
        return result

    async def fake_cleanup(_client, _snapshot_value, copied):
        target_id = next(iter(copied["copy_report"]["id_map"].values()))
        deleted.append(target_id)
        return [target_id]

    class Client:
        async def call_tool(self, name, _arguments):
            assert name == "get_page_xml"
            return {"xml": _page_xml()}

    monkeypatch.setattr(copy_runtime, "scenario_client", fake_scenario_client)
    monkeypatch.setattr(copy_runtime, "capture_snapshot", fake_capture)
    monkeypatch.setattr(copy_runtime, "call_with_result_evidence", fake_copy)
    monkeypatch.setattr(copy_runtime, "cleanup_copy", fake_cleanup)
    monkeypatch.setattr(copy_runtime, "assert_copy_mapping", lambda *_a, **_k: None)
    monkeypatch.setattr(copy_runtime, "assert_pages_unchanged", lambda *_a, **_k: None)
    monkeypatch.setattr(copy_runtime, "render_report", lambda *_a, **_k: None)
    monkeypatch.setattr(copy_runtime, "run_safe_timestamp", lambda _args: "recorded")

    result = asyncio.run(
        copy_runtime.execute_copy_display_equation(
            argparse.Namespace(
                notebook_name="Disposable",
                keep_worksite=False,
            ),
            RuntimeOptions(tmp_path, 1_800, False, False),
            {
                "notebook": {"id": "notebook", "name": "Disposable"},
                "structure": {
                    "canvas_section": {"id": "section"},
                    "canvas_page": {"id": "source"},
                },
            },
            client=Client(),
        )
    )

    assert result["status"] == "passed"
    assert result["restored"] is True
    assert result["target_ids"] == ["target-1", "target-2", "target-3"]
    assert [hop["target_break_count"] for hop in result["hops"]] == [1, 1, 1]
    assert deleted == ["target-3", "target-2", "target-1"]
