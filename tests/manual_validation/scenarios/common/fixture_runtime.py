"""Branch-free orchestration for scenario-owned fixture recipes."""

from __future__ import annotations

import argparse
from typing import Any
import uuid

from ...mcp_stdio_client import MCPStdioClient
from ...runtime import InvariantFailure, RuntimeOptions
from ...test_utils import capture_snapshot, stable_item, write_json
from ..base import Scenario
from .fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureRecorder,
    FixtureValidationContext,
)
from .specs import ScenarioSpec


def _fixture_result(
    scenario_name: str,
    notebook: dict[str, Any],
    recorder: FixtureRecorder,
    *,
    passed: bool,
    checks: tuple[str, ...] = (),
    error: str | None = None,
) -> dict[str, Any]:
    validation: dict[str, Any] = {"passed": passed, "checks": list(checks)}
    if error is not None:
        validation["error"] = error
    return {
        "scenario": scenario_name,
        "notebook": stable_item(notebook),
        "structure_ids": {
            key: value["id"] for key, value in recorder.structure.items()
        },
        "fixture_profile": recorder.manifest("pending")["scenario_spec"][
            "fixture_profile"
        ],
        "validation": validation,
    }


def _persist_failure(
    scenario_name: str,
    notebook: dict[str, Any],
    recorder: FixtureRecorder,
    error: Exception,
) -> None:
    message = str(error)
    recorder.persist("failed", error=message)
    write_json(
        recorder.run_dir / "fixture-result.json",
        _fixture_result(
            scenario_name,
            notebook,
            recorder,
            passed=False,
            error=message,
        ),
    )


async def prepare_fixture(
    scenario: Scenario,
    args: argparse.Namespace,
    options: RuntimeOptions,
    client: MCPStdioClient,
    notebook: dict[str, Any],
    notebook_path: str,
    spec: ScenarioSpec,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build, snapshot, validate, and persist one selected Scenario recipe."""

    recipe = scenario.fixture_recipe
    if recipe.profile != spec.fixture or recipe.scenario_name != scenario.name:
        raise InvariantFailure("Scenario fixture recipe/profile ownership mismatch.")
    recorder = FixtureRecorder(
        run_dir=options.run_dir,
        notebook=notebook,
        notebook_path=notebook_path,
        spec=spec,
        allowed_keys=recipe.manifest_keys,
    )
    recorder.persist("pending")
    context = FixtureContext(
        args=args,
        options=options,
        client=client,
        notebook=notebook,
        notebook_path=notebook_path,
        spec=spec,
        token=str(uuid.uuid4()),
        recorder=recorder,
    )
    try:
        build = await recipe.build(context)
        if dict(build.structure) != recorder.structure:
            raise InvariantFailure("Fixture recipe returned structure outside its recorder.")
        if dict(build.evidence) != recorder.evidence:
            raise InvariantFailure("Fixture recipe returned evidence outside its recorder.")
        required = recipe.required_manifest_keys(args)
        if set(recorder.structure) != set(required):
            missing = sorted(set(required) - set(recorder.structure))
            extra = sorted(set(recorder.structure) - set(required))
            raise InvariantFailure(
                f"Fixture manifest keys do not match recipe declaration; missing={missing}, extra={extra}."
            )
        snapshot = await capture_snapshot(client, str(notebook["id"]))
        write_json(options.run_dir / "prepared.json", snapshot)
        write_json(options.run_dir / "fixture-snapshot.json", snapshot)
        write_json(options.run_dir / "page-hashes.json", snapshot.get("page_hashes", {}))
        pending_result = _fixture_result(
            scenario.name,
            notebook,
            recorder,
            passed=False,
        )
        write_json(options.run_dir / "fixture-result.json", pending_result)
        checks = recipe.validate(
            FixtureValidationContext(args=args, snapshot=snapshot),
            FixtureBuildResult(recorder.structure, recorder.evidence),
        )
    except Exception as exc:
        _persist_failure(scenario.name, notebook, recorder, exc)
        raise

    manifest = recorder.persist("passed")
    manifest["fixture_validation"] = {"status": "passed", "checks": list(checks)}
    write_json(options.run_dir / "manifest.json", manifest)
    result = _fixture_result(
        scenario.name,
        notebook,
        recorder,
        passed=True,
        checks=checks,
    )
    write_json(options.run_dir / "fixture-result.json", result)
    return manifest, result


__all__ = ["prepare_fixture"]
