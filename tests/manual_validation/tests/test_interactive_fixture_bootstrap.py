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


def test_representative_move_bootstrap_captures_and_freezes_both_roles(
    monkeypatch, tmp_path
) -> None:
    scenario = SCENARIO_REGISTRY.get("bootstrap-move-page-content-fixture")
    run_dir = tmp_path / "run-move-content"
    source_structure = {
        "source_instructions_section": {
            "id": "instructions-section",
            "resource_type": "section",
            "parent_id": "source-notebook",
        },
        "source_instructions_page": {
            "id": "instructions-page",
            "resource_type": "page",
            "section_id": "instructions-section",
            "title": "00-Reserved-Marker-Do-Not-Edit",
            "page_level": 1,
        },
        "source_canvas_section": {
            "id": "canvas-section",
            "resource_type": "section",
            "parent_id": "source-notebook",
        },
        "source_canvas_page": {
            "id": "canvas-page",
            "resource_type": "page",
            "section_id": "canvas-section",
            "title": "01-Representative-Page",
            "page_level": 1,
        },
    }
    destination_structure = {
        "destination_section": {
            "id": "destination-section",
            "resource_type": "section",
            "parent_id": "destination-notebook",
        },
        "destination_anchor": {
            "id": "destination-anchor",
            "resource_type": "page",
            "section_id": "destination-section",
            "title": "99-Destination-Anchor",
            "page_level": 1,
        },
    }
    source_snapshot = {
        "notebook_id": "source-notebook",
        "items": [
            (
                {
                    **item,
                    "title": "Frozen representative title",
                    "path": "Source/01-Move-Source/Frozen representative title",
                }
                if item["id"] == "canvas-page"
                else item
            )
            for item in source_structure.values()
        ],
        "page_hashes": {
            "instructions-page": "marker-hash",
            "canvas-page": "representative-hash",
        },
        "page_objects": {
            "instructions-page": [{"kind": "Outline"}],
            "canvas-page": [{"kind": "Outline"}, {"kind": "Table"}],
        },
        "page_capability_projections": {
            "instructions-page": {
                "capabilities": ["Outline", "RichText"],
                "unknown_nodes": [],
                "unsupported_page_roots": [],
                "complete": True,
            },
            "canvas-page": {
                "capabilities": ["Outline", "RichText", "Table"],
                "unknown_nodes": [],
                "unsupported_page_roots": [],
                "complete": True,
            },
        },
    }
    destination_snapshot = {
        "notebook_id": "destination-notebook",
        "items": list(destination_structure.values()),
        "page_hashes": {"destination-anchor": "anchor-hash"},
        "page_objects": {"destination-anchor": [{"kind": "Outline"}]},
        "page_capability_projections": {
            "destination-anchor": {
                "capabilities": ["Outline", "RichText"],
                "unknown_nodes": [],
                "unsupported_page_roots": [],
                "complete": True,
            }
        },
    }
    answers = iter(
        (
            f"CONFIRM {run_dir.name} MovePageContent",
            f"ACCEPT {run_dir.name} MovePageContent",
        )
    )

    async def fake_input(_prompt: str, _timeout: int) -> str:
        return next(answers)

    async def fake_snapshot(_client, notebook_id: str) -> dict:
        return (
            source_snapshot
            if notebook_id == "source-notebook"
            else destination_snapshot
        )

    monkeypatch.setattr(interactive_bootstrap, "_bounded_input", fake_input)
    monkeypatch.setattr(interactive_bootstrap, "capture_snapshot", fake_snapshot)
    manifest = {
        "notebook": {"id": "source-notebook", "name": "Source"},
        "notebooks": {
            "source": {"id": "source-notebook", "name": "Source"},
            "destination": {
                "id": "destination-notebook",
                "name": "Destination",
            },
        },
        "notebook_paths": {
            "source": str(run_dir / "source"),
            "destination": str(run_dir / "destination"),
        },
        "structure": {**source_structure, **destination_structure},
        "role_structures": {
            "source": source_structure,
            "destination": destination_structure,
        },
        "fixture_validation": {
            "status": "passed",
            "role_checks": {"source": [], "destination": []},
            "bundle_checks": [],
        },
        "disposable_targets": {
            "source_notebook_path": str(run_dir / "source"),
            "destination_notebook_path": str(run_dir / "destination"),
        },
    }
    result = asyncio.run(
        scenario.execute(
            argparse.Namespace(interactive_timeout=60),
            RuntimeOptions(run_dir, 1_800, False, False),
            manifest,
            client=object(),
            fixture_result={"validation": {"role_checks": {}}},
        )
    )

    assert result["template_state"] == "ready"
    assert result["template_instance_id"].startswith("authored-")
    assert result["template_instance"]["move_source_deletion_allowed"] is True
    assert result["template_instance"]["observed_capabilities"] == (
        "Outline",
        "RichText",
        "Table",
    )
    assert read_json(run_dir / "fixture-snapshot-source.json")["notebook_id"] == (
        "source-notebook"
    )
    assert read_json(run_dir / "fixture-snapshot-destination.json")[
        "notebook_id"
    ] == "destination-notebook"
    assert read_json(run_dir / "interactive-validation.json")[
        "synthetic_content_only"
    ] is False
    assert manifest["role_structures"]["source"]["source_canvas_page"]["title"] == (
        "Frozen representative title"
    )
    assert manifest["structure"]["source_canvas_page"]["path"] == (
        "Source/01-Move-Source/Frozen representative title"
    )
