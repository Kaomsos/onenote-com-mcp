"""Ownership, least-privilege, and partial-failure fixture recipe contracts."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
from pathlib import Path

import pytest

from tests.manual_validation import test_utils
from tests.manual_validation.runtime import InvariantFailure, RuntimeOptions
from tests.manual_validation.scenarios.common.fixture_models import FixtureBuildResult, FixtureContext, FixtureRecorder
from tests.manual_validation.scenarios.common.fixture_runtime import prepare_fixture
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY


def _args(tmp_path: Path, scenario: str) -> argparse.Namespace:
    values = {
        "command": scenario,
        "scenario": scenario,
        "notebook_name": "Notebook",
        "run_dir": tmp_path,
        "timeout": 180,
        "dry_run": False,
        "json_output": False,
        "keep_notebook": False,
        "keep_worksite": False,
    }
    if scenario == "rename":
        values.update(target="content_section", new_name=None)
    if scenario == "reorder-page":
        values["page_level"] = 2
    return argparse.Namespace(**values)


def test_every_public_scenario_uniquely_owns_a_static_recipe() -> None:
    recipes = [scenario.fixture_recipe for scenario in SCENARIO_REGISTRY.values()]
    assert len({id(recipe) for recipe in recipes}) == len(recipes)
    for scenario in SCENARIO_REGISTRY.values():
        recipe = scenario.fixture_recipe
        assert recipe.scenario_name == scenario.name
        assert recipe.profile == scenario.spec.fixture == scenario.fixture_profile
        assert recipe.profile.creation_tools <= scenario.spec.tool_allowlist
        assert recipe.manifest_keys


def test_common_fixture_runtime_has_no_scenario_dispatch_or_second_registry() -> None:
    common = Path(__file__).parents[1] / "scenarios" / "common"
    source = "\n".join(
        (common / name).read_text(encoding="utf-8")
        for name in ("fixture_models.py", "fixture_runtime.py", "fixture_builders.py")
    )
    assert "args.scenario" not in source
    assert "SCENARIO_REGISTRY" not in source
    for name in SCENARIO_REGISTRY.public_names:
        assert f'"{name}"' not in source
    assert not (common / "fixtures.py").exists()


@pytest.mark.parametrize("scenario_name", SCENARIO_REGISTRY.public_names)
def test_recording_fixture_build_never_exceeds_declared_tools(
    scenario_name, monkeypatch, tmp_path
) -> None:
    scenario = SCENARIO_REGISTRY.get(scenario_name)
    recipe = scenario.fixture_recipe
    module = importlib.import_module(inspect.getmodule(recipe.build).__name__)
    calls: list[str] = []
    sequence = iter(range(1, 1000))

    def item(kind: str, parent_id: str, name: str) -> dict:
        number = next(sequence)
        value = {
            "id": f"{kind}-{number}",
            "resource_type": kind,
            "name": name,
            "parent_id": parent_id,
        }
        if kind == "page":
            value.update(title=name, section_id=parent_id, page_level=1, parent_page_id=None)
        return value

    async def ensure_group(_client, parent_id, name):
        calls.append("create_section_group")
        return item("section_group", parent_id, name)

    async def ensure_section(_client, parent_id, name):
        calls.append("create_section")
        return item("section", parent_id, name)

    async def ensure_page(_client, section_id, title, _content):
        calls.append("create_page")
        return item("page", section_id, title)

    async def enforce(_client, section_id, _page_id, after_page_id, page_level):
        calls.append("reorder_page")
        return {
            "id": _page_id,
            "resource_type": "page",
            "title": "Page",
            "section_id": section_id,
            "parent_id": section_id,
            "page_level": page_level,
            "parent_page_id": after_page_id if page_level > 1 else None,
        }

    async def rich(_client, page, _run_dir):
        calls.extend(["append_to_page", "add_image_to_page"])
        return page, {
            "page_id": page["id"],
            "automated_content": ["rich_text", "table", "image"],
        }

    async def list_tag(_client, page):
        calls.append("append_to_page")
        return page, {
            "page_id": page["id"],
            "observed_capabilities": ["List", "Tag"],
            "observed_counts": {"List": 3, "Tag": 3, "TagDef": 1},
        }

    for name, replacement in {
        "ensure_group": ensure_group,
        "ensure_section": ensure_section,
        "ensure_page": ensure_page,
        "enforce_page_position": enforce,
        "ensure_copy_rich_fixture": rich,
        "ensure_reparent_page_rich_fixture": rich,
        "ensure_copy_list_tag_fixture": list_tag,
    }.items():
        if hasattr(module, name):
            monkeypatch.setattr(module, name, replacement)

    descriptions = "\n".join(
        importlib.import_module(f"tests.manual_validation.scenarios.fixture_recipes.{name}").DESCRIPTION
        for name in (
            "reorder_page",
            "reorder_section",
            "reorder_section_group",
            "reparent_section",
            "reparent_page",
            "reparent_section_group",
        )
    )

    class Client:
        async def call_tool(self, name, _arguments):
            calls.append(name)
            return {"text": descriptions}

    args = _args(tmp_path, scenario_name)
    recorder = FixtureRecorder(
        run_dir=tmp_path,
        notebook={"id": "notebook", "name": "Notebook"},
        notebook_path=str(tmp_path / "notebooks" / "Notebook"),
        spec=scenario.spec,
        allowed_keys=recipe.manifest_keys,
    )
    context = FixtureContext(
        args=args,
        options=RuntimeOptions(tmp_path, 180, False, False),
        client=Client(),
        notebook={"id": "notebook", "name": "Notebook"},
        notebook_path=str(tmp_path / "notebooks" / "Notebook"),
        spec=scenario.spec,
        token="token",
        recorder=recorder,
    )
    result = asyncio.run(recipe.build(context))
    assert set(result.structure) == set(recipe.required_manifest_keys(args))
    assert set(calls) <= set(scenario.spec.tool_allowlist)
    mutation_calls = {name for name in calls if name not in {"get_page_text"}}
    assert mutation_calls <= set(recipe.profile.creation_tools)


def test_build_failure_preserves_incremental_ids_and_failed_handoff(
    monkeypatch, tmp_path
) -> None:
    scenario = SCENARIO_REGISTRY.get("delete")

    async def partial_build(context):
        context.recorder.record_structure(
            "delete_sandbox",
            {"id": "sandbox-id", "name": "Delete-Sandbox", "resource_type": "section_group"},
        )
        raise InvariantFailure("injected build failure")

    monkeypatch.setattr(scenario.fixture_recipe, "build", partial_build)
    args = _args(tmp_path, "delete")
    with pytest.raises(InvariantFailure, match="injected build failure"):
        asyncio.run(
            prepare_fixture(
                scenario,
                args,
                RuntimeOptions(tmp_path, 180, False, False),
                object(),
                {"id": "notebook", "name": "Notebook"},
                str(tmp_path / "notebooks" / "Notebook"),
                scenario.spec,
            )
        )
    manifest = test_utils.read_json(tmp_path / "manifest.json")
    result = test_utils.read_json(tmp_path / "fixture-result.json")
    assert manifest["structure"]["delete_sandbox"]["id"] == "sandbox-id"
    assert manifest["fixture_validation"]["status"] == "failed"
    assert manifest["lifecycle_lease"].endswith("lifecycle-lease.json")
    assert manifest["disposable_targets"]["source_notebook_path"].endswith("Notebook")
    assert result["structure_ids"] == {"delete_sandbox": "sandbox-id"}
    assert result["validation"]["passed"] is False
