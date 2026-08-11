"""Pure contracts for interactive authored-content evidence and detection."""

from __future__ import annotations

import argparse
import asyncio

import pytest

from tests.manual_validation.runtime import InvariantFailure, RuntimeOptions
from tests.manual_validation.scenarios.common import interactive_bootstrap
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.test_utils import read_json, write_json


def _recorded_unexpected_content_snapshot() -> dict:
    page_id = "canvas-page"
    return {
        "captured_at": "recorded",
        "notebook_id": "notebook-id",
        "items": [
            {
                "id": page_id,
                "resource_type": "page",
                "section_id": "canvas-section",
            }
        ],
        "page_hashes": {page_id: "authored-hash"},
        "page_objects": {
            page_id: [
                {"kind": "Outline"},
                {"kind": "OE"},
                {"kind": "InsertedFile"},
                {"kind": "Outline"},
                {"kind": "OE"},
            ]
        },
        "page_capability_projections": {
            page_id: {
                "schema_version": 1,
                "capabilities": ["InsertedFile", "Outline"],
                "object_kind_counts": {"InsertedFile": 1, "OE": 2, "Outline": 2},
                "unknown_nodes": [],
                "unsupported_page_roots": [],
                "complete": True,
            }
        },
    }


def test_detection_failure_persists_authored_snapshot_before_validator(
    monkeypatch, tmp_path
) -> None:
    scenario = SCENARIO_REGISTRY.get("bootstrap-ink-drawing-fixture")
    run_dir = tmp_path / "run-recorded"
    baseline = {"captured_at": "baseline", "page_objects": {"canvas-page": []}}
    write_json(run_dir / "fixture-snapshot.json", baseline)

    async def fake_input(_prompt: str, _timeout: int) -> str:
        return f"CONFIRM {run_dir.name} InkDrawing"

    async def fake_snapshot(_client, _notebook_id: str) -> dict:
        return _recorded_unexpected_content_snapshot()

    monkeypatch.setattr(interactive_bootstrap, "_bounded_input", fake_input)
    monkeypatch.setattr(interactive_bootstrap, "capture_snapshot", fake_snapshot)
    args = argparse.Namespace(interactive_timeout=60)
    manifest = {
        "notebook": {"id": "notebook-id", "name": "Disposable"},
        "structure": {
            "canvas_section": {"id": "canvas-section"},
            "canvas_page": {"id": "canvas-page"},
        },
        "disposable_targets": {"source_notebook_path": str(run_dir / "notebook")},
    }

    with pytest.raises(
        InvariantFailure,
        match=(
            r"requested InkDrawing=1; observed=0; "
            r"missing=InkDrawing; unexpected=InsertedFile=1"
        ),
    ) as exc_info:
        asyncio.run(
            scenario.execute(
                args,
                RuntimeOptions(run_dir, 1_800, False, False),
                manifest,
                client=object(),
                fixture_result={"validation": {"checks": []}},
            )
        )

    assert "Interactive detector mismatch" in str(exc_info.value)

    assert read_json(run_dir / "fixture-snapshot.json") == baseline
    authored = read_json(run_dir / "interactive-authored-snapshot.json")
    assert authored["page_objects"]["canvas-page"][2]["kind"] == "InsertedFile"
    detection = read_json(run_dir / "interactive-detection.json")
    assert detection["passed"] is False
    assert detection["schema_version"] == 3
    assert detection["missing"] == ["InkDrawing"]
    assert detection["unexpected"] == ["InsertedFile"]
    assert detection["unexpected_counts"] == {"InsertedFile": 1}
    assert detection["representation_status"] == "mismatch"
    assert detection["template_publish_allowed"] is False
    assert not (run_dir / "interactive-validation.json").exists()
