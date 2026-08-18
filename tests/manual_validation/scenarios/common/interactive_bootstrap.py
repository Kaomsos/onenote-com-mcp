"""Human-gated execution shared by statically bound interactive bootstrap Scenarios."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from typing import Any

from ...runtime import InvariantFailure, RuntimeOptions
from ...test_utils import capture_snapshot, write_json
from ..base import Scenario
from ..fixture_recipes.interactive import UserAuthoredRecipe
from ..fixture_recipes.recipe_base import (
    FixtureBundleObservation,
    FixtureRoleObservation,
)
from .fixture_models import FixtureBuildResult


MAX_INTERACTIVE_TIMEOUT = 1_800


async def _bounded_input(prompt: str, timeout: int) -> str:
    try:
        return await asyncio.wait_for(asyncio.to_thread(input, prompt), timeout=timeout)
    except (asyncio.TimeoutError, EOFError) as exc:
        raise InvariantFailure("Interactive checkpoint timed out or stdin reached EOF.") from exc


class InteractiveBootstrapScenario(Scenario):
    included_in_all = False
    timeout_default = 1_800
    worksite_dry_run_action = "preserve-unpublished-interactive-bootstrap"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--interactive-timeout",
            type=int,
            default=900,
            help="Bounded seconds for each exact run-bound user confirmation (max 1800).",
        )

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        if client is None:
            raise InvariantFailure("Interactive bootstrap requires its one scenario MCP client.")
        if args.interactive_timeout < 1 or args.interactive_timeout > MAX_INTERACTIVE_TIMEOUT:
            raise InvariantFailure("--interactive-timeout must be between 1 and 1800 seconds.")
        recipe = self.fixture_recipe
        run_id = options.run_dir.name
        confirmation = f"CONFIRM {run_id} {recipe.capability}"
        verdict = f"ACCEPT {run_id} {recipe.capability}"
        synthetic_content_only = bool(
            getattr(recipe, "synthetic_content_only", True)
        )
        source_structure = (
            manifest.get("role_structures", {}).get("source")
            if isinstance(manifest.get("role_structures"), dict)
            else None
        )
        if not isinstance(source_structure, dict):
            source_structure = manifest["structure"]
        canvas = source_structure.get("canvas_page") or source_structure.get(
            "source_canvas_page"
        )
        checkpoint = {
            "schema_version": 1,
            "scenario": self.name,
            "recipe_class": type(recipe).__name__,
            "capability": recipe.capability,
            "run_id": run_id,
            "role": "source",
            "canvas_page_id": canvas.get("id") if isinstance(canvas, dict) else None,
            "authoring_zones": [
                asdict(zone) for zone in getattr(recipe, "authoring_zones", ())
            ],
            "synthetic_content_only": synthetic_content_only,
            "authoring_instruction": recipe.authoring_instruction,
            "confirmation_phrase": confirmation,
            "timeout_seconds": args.interactive_timeout,
            "state": "waiting_for_authored_content",
        }
        write_json(options.run_dir / "checkpoint.json", checkpoint)
        response = (await _bounded_input(f"Type {confirmation!r} after editing the exact Canvas: ", args.interactive_timeout)).strip()
        if response != confirmation:
            raise InvariantFailure("Interactive checkpoint confirmation phrase did not match this run.")
        declared_roles = tuple(
            role.role for role in recipe.cache_identity.notebook_roles
        )
        notebooks = manifest.get("notebooks")
        if not isinstance(notebooks, dict):
            notebooks = {"source": manifest["notebook"]}
        role_structures = manifest.get("role_structures")
        if not isinstance(role_structures, dict):
            role_structures = {"source": manifest["structure"]}
        notebook_paths = manifest.get("notebook_paths")
        if not isinstance(notebook_paths, dict):
            notebook_paths = {
                "source": manifest["disposable_targets"]["source_notebook_path"]
            }
        if not (
            set(notebooks) == set(declared_roles)
            and set(role_structures) == set(declared_roles)
            and set(notebook_paths) == set(declared_roles)
        ):
            raise InvariantFailure(
                "Interactive bootstrap manifest does not cover every Recipe role."
            )
        observations: dict[str, FixtureRoleObservation] = {}
        snapshots: dict[str, dict[str, Any]] = {}
        for role in declared_roles:
            snapshot = await capture_snapshot(
                client,
                str(notebooks[role]["id"]),
            )
            snapshots[role] = snapshot
            write_json(
                options.run_dir / f"interactive-authored-snapshot-{role}.json",
                snapshot,
            )
            write_json(
                options.run_dir / f"fixture-snapshot-{role}.json",
                snapshot,
            )
            build = FixtureBuildResult(
                role_structures[role],
                (
                    {
                        key: manifest[key]
                        for key in ("copy_fixture", "reparent_page_fixture")
                        if isinstance(manifest.get(key), dict)
                    }
                    if role == "source"
                    else {}
                ),
            )
            observations[role] = FixtureRoleObservation(
                role=role,
                args=args,
                notebook=notebooks[role],
                notebook_path=notebook_paths[role],
                snapshot=snapshot,
                build=build,
            )
        snapshot = snapshots["source"]
        write_json(options.run_dir / "interactive-authored-snapshot.json", snapshot)
        observation = FixtureBundleObservation(roles=observations)
        detection = recipe.authored_content_report(observation)
        write_json(options.run_dir / "interactive-detection.json", detection)
        authored = recipe.validate_authored_content(observation, detection)
        instance = recipe.freeze_authored_instance(observation) if isinstance(
            recipe, UserAuthoredRecipe
        ) else None
        response = (await _bounded_input(f"Type {verdict!r} to record the human UI verdict: ", args.interactive_timeout)).strip()
        if response != verdict:
            raise InvariantFailure("Interactive human verdict was not an exact positive run-bound verdict.")
        evidence = {
            "schema_version": 1,
            "scenario": self.name,
            "recipe_class": type(recipe).__name__,
            "capability": recipe.capability,
            "requested_observed": authored,
            "human_verdict": "accepted",
            "confirmation_bound_to_run": True,
            "template_instance": asdict(instance) if instance is not None else None,
            "synthetic_content_only": synthetic_content_only,
            "passed": True,
        }
        write_json(options.run_dir / "interactive-validation.json", evidence)
        write_json(options.run_dir / "fixture-snapshot.json", snapshot)
        manifest["interactive_fixture"] = evidence
        fixture_validation = dict(manifest.get("fixture_validation", {}))
        fixture_validation.update(
            status="passed",
            authored_content_validated=True,
            human_verdict="accepted",
        )
        if len(declared_roles) == 1:
            fixture_validation["checks"] = list(
                fixture_result["validation"].get("checks", [])
            )
        manifest["fixture_validation"] = fixture_validation
        write_json(options.run_dir / "manifest.json", manifest)
        return {
            "scenario": self.name,
            "status": "passed",
            "interactive_bootstrap": True,
            "template_instance_id": (
                instance.template_instance_id
                if instance is not None
                else recipe.default_template_instance_id
            ),
            "template_state": instance.state if instance is not None else "ready",
            "template_instance": asdict(instance) if instance is not None else None,
            "human_verdict": "accepted",
        }


__all__ = ["InteractiveBootstrapScenario", "MAX_INTERACTIVE_TIMEOUT"]
