from __future__ import annotations

import argparse
import asyncio
from contextlib import nullcontext
import json
from pathlib import Path
import string
from types import SimpleNamespace

import pytest

from tests.manual_validation.mcp_stdio_client import ClientFailure, summarize
from tests.manual_validation.runner import main
from tests.manual_validation.runtime import InvariantFailure, RunnerFailure, RuntimeOptions
from tests.manual_validation.scenarios.common.fixture_runtime import (
    prepare_fixture_bundle,
)
from tests.manual_validation.scenarios.common.fixture_models import (
    FixtureBuildResult,
    FixtureValidationContext,
)
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.fixture_recipes.search_all_open_notebooks import (
    SearchAllOpenNotebooksFixtureRecipe,
    generate_search_probe,
)
from tests.manual_validation.scenarios.fixture_recipes.recipe_base import (
    FixtureBundleObservation,
    FixtureRoleObservation,
    FixtureValidationReport,
)
from tests.manual_validation.scenarios.common.orchestrator import (
    _checkpoint_fresh_search_bundle,
)


def test_search_probe_is_exactly_32_safe_characters_with_both_typed_segments() -> None:
    allowed = set(string.ascii_letters + string.digits)
    probes = {generate_search_probe() for _ in range(100)}

    assert len(probes) == 100
    for probe in probes:
        assert len(probe) == 32
        assert probe[15] == "-"
        left, right = probe.split("-")
        assert len(left) == 15
        assert len(right) == 16
        assert set(left) <= allowed
        assert set(right) <= allowed
        for segment in (left, right):
            assert any(char.isupper() for char in segment)
            assert any(char.islower() for char in segment)
            assert any(char.isdigit() for char in segment)


def test_search_cache_snapshot_rehydrates_probes_without_an_extra_page_read() -> None:
    recipe = SearchAllOpenNotebooksFixtureRecipe()
    cached_probe = "Ab1cdefghijklmn-Zy9xwvutsrqponml"
    cached_budget = "Ab1cdefghijklmnopqrstuvw"
    cached_long = "Zy9xwvutsrqponmlkjihgfed"
    build = FixtureBuildResult({}, {})
    recipe.begin_snapshot_content_validation()

    def xml(body: str) -> str:
        return (
            '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">'
            "<one:Outline><one:OEChildren><one:OE><one:T><![CDATA["
            + body.replace("\n", "<br/>")
            + "]]></one:T></one:OE></one:OEChildren></one:Outline></one:Page>"
        )

    pages = [
        ("source", "a1", "Probe Page A1", f"SEARCH_PROBE:{cached_probe}\nBUDGET_MARKER:{cached_budget}"),
        ("source", "a2", "Probe Page A2", f"SEARCH_PROBE:{cached_probe}\nBUDGET_MARKER:{cached_budget}"),
        ("source", "a3", "Probe Page A3", f"SEARCH_PROBE:{cached_probe}\nBUDGET_MARKER:{cached_budget}"),
        ("search-b", "b1", "Probe Page B1", f"SEARCH_PROBE:{cached_probe}\nBUDGET_MARKER:{cached_budget}"),
        (
            "search-b",
            "b2",
            "Budget Long Text Page B2",
            f"BUDGET_MARKER:{cached_budget}\nLONG_TEXT_MARKER:{cached_long}",
        ),
    ]
    for role, page_id, title, body in pages:
        observer = recipe.snapshot_page_observer(role, build)
        assert observer is not None
        observer({"id": page_id, "title": title}, xml(body))

    recipe.complete_snapshot_content_validation()

    assert recipe.probe == cached_probe
    assert recipe.budget_marker == cached_budget
    assert recipe.long_text_marker == cached_long
    assert build.evidence == {}


def test_search_scenario_is_two_role_fresh_only_and_least_privilege() -> None:
    scenario = SCENARIO_REGISTRY.get("search-all-open-notebooks")
    spec = scenario.spec

    assert scenario.included_in_all is True
    assert scenario.requires_index_activation_checkpoint is True
    assert scenario.fixture_recipe.supports_cache is False
    assert "Section below a SectionGroup" in scenario.fixture_recipe.fresh_only_reason
    assert tuple(
        role.role for role in scenario.fixture_recipe.cache_identity.notebook_roles
    ) == ("search-b", "source")
    assert spec.search_budget == {
        "max_pages": 4,
        "max_page_chars": 2048,
        "max_total_chars": 512,
        "max_seconds": 60,
        "snippet_chars": 200,
    }
    assert "search_pages" in spec.tool_allowlist
    assert "get_page_text" not in spec.tool_allowlist
    assert spec.execution_contract["fresh_index_activation_checkpoint"] == (
        "close_false_reopen_exact_paths"
    )
    assert spec.policy.writes_enabled is True
    assert spec.policy.deletes_enabled is False
    assert spec.policy.create_enabled is True
    assert spec.policy.organize_enabled is False
    assert spec.policy.local_file_io_enabled is False
    assert spec.policy.ui_control_enabled is False
    assert spec.policy.notebook_lifecycle_enabled is False


def test_search_audit_redacts_query_content_text_and_snippet() -> None:
    probe = generate_search_probe()
    value = summarize(
        {
            "query": probe,
            "content": probe,
            "text": probe,
            "snippet": probe,
            "id": "safe-page-id",
        }
    )
    rendered = json.dumps(value, sort_keys=True)

    assert probe not in rendered
    assert rendered.count('"redacted": true') == 4
    assert value["id"] == "safe-page-id"


def test_search_use_cache_dry_run_fails_fast(capsys) -> None:
    assert main(
        ["search-all-open-notebooks", "--use-cache", "--dry-run", "--json"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["cache"]["decision"] == "rejected_fresh_only"
    assert payload["cache"]["enabled"] is False
    assert payload["expected_mcp_process_starts"] == 0
    assert payload["ordered_steps"][0]["step"] == "preflight-fresh-only-rejects-cache"
    assert payload["ordered_steps"][0]["allowed_operations"] == []
    assert "activate-search-index-fixture" not in {
        step["step"] for step in payload["ordered_steps"]
    }
    assert payload["search_budget"]["max_pages"] == 4


def test_search_warns_after_an_actual_stable_probe_collision(tmp_path, monkeypatch) -> None:
    scenario = SCENARIO_REGISTRY.get("search-all-open-notebooks")
    expected = {"a1", "a2", "a3", "b1"}

    async def fake_search(client, query, scope, **kwargs):
        del client, query, scope, kwargs
        return {"pages": [{"id": value} for value in sorted(expected | {"extra"})]}

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(scenario, "_search", fake_search)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    with pytest.raises(RunnerFailure, match="search_probe_collision"):
        asyncio.run(
            scenario._wait_for_stable_root(
                object(),
                "redacted",
                expected,
                tmp_path,
                use_cache=True,
                max_attempts=2,
            )
        )

    warning = json.loads(
        (tmp_path / "probe-collision-warning.json").read_text(encoding="utf-8")
    )
    assert warning["extra_hit_ids"] == ["extra"]
    assert warning["query_text_persisted"] is False


@pytest.mark.parametrize(
    ("code", "message_fragment", "passed_status", "filename"),
    [
        (
            "validation_error",
            "LOCAL_ONENOTE_MAX_SEARCH_PAGES=4",
            "candidate_budget_exceeded",
            "candidate.json",
        ),
        (
            "backend_error",
            "LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS=512",
            "total_char_budget_exceeded",
            "total.json",
        ),
    ],
)
def test_search_budget_probe_parses_nested_failure_after_index_retry(
    code,
    message_fragment,
    passed_status,
    filename,
    monkeypatch,
    tmp_path,
) -> None:
    scenario = SCENARIO_REGISTRY.get("search-all-open-notebooks")
    calls = 0

    async def fake_search(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"pages": []}
        raise ClientFailure(
            "expected budget failure",
            envelope={
                "ok": False,
                "error": {
                    "code": code,
                    "message": f"budget exceeded: {message_fragment}",
                },
            },
        )

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(scenario, "_search", fake_search)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    attempts = asyncio.run(
        scenario._wait_for_expected_budget_failure(
            object(),
            "redacted",
            {"mode": "root"},
            tmp_path,
            search_arguments={"page_size": 1},
            expected_code=code,
            expected_message_fragment=message_fragment,
            passed_status=passed_status,
            evidence_filename=filename,
            exhausted_message="not ready",
            max_attempts=2,
        )
    )

    assert [attempt["status"] for attempt in attempts] == [
        "index_not_ready",
        passed_status,
    ]
    assert json.loads((tmp_path / filename).read_text(encoding="utf-8")) == {
        "attempts": attempts
    }


def test_search_budget_probe_fails_fast_on_unexpected_nested_error(
    monkeypatch,
    tmp_path,
) -> None:
    scenario = SCENARIO_REGISTRY.get("search-all-open-notebooks")
    calls = 0

    async def fake_search(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise ClientFailure(
            "unexpected",
            envelope={
                "ok": False,
                "error": {"code": "policy_disabled", "message": "denied"},
            },
        )

    monkeypatch.setattr(scenario, "_search", fake_search)
    with pytest.raises(
        RunnerFailure,
        match="Unexpected Search budget probe failure: policy_disabled: denied",
    ):
        asyncio.run(
            scenario._wait_for_expected_budget_failure(
                object(),
                "redacted",
                {"mode": "root"},
                tmp_path,
                search_arguments={"page_size": 1},
                expected_code="validation_error",
                expected_message_fragment="expected budget",
                passed_status="passed",
                evidence_filename="unexpected.json",
                exhausted_message="not ready",
                max_attempts=20,
            )
        )

    assert calls == 1
    attempts = json.loads(
        (tmp_path / "unexpected.json").read_text(encoding="utf-8")
    )["attempts"]
    assert attempts == [
        {
            "attempt": 1,
            "error_category": "policy_disabled",
            "status": "unexpected_error",
        }
    ]


def test_search_fresh_dry_run_has_exclusive_index_activation_checkpoint(capsys) -> None:
    assert main(["search-all-open-notebooks", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    steps = [step["step"] for step in payload["ordered_steps"]]
    assert steps.count("activate-search-index-fixture") == 1
    checkpoint = next(
        step
        for step in payload["ordered_steps"]
        if step["step"] == "activate-search-index-fixture"
    )
    assert checkpoint["target"] == "fresh Search fixture bundle only"
    assert "CloseNotebook(force=false)" in checkpoint["allowed_operations"]


def test_non_index_query_dry_run_does_not_gain_search_checkpoint(capsys) -> None:
    assert main(["query", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert "activate-search-index-fixture" not in {
        step["step"] for step in payload["ordered_steps"]
    }


class _CheckpointWrapper:
    def __init__(self, role: str, path: Path) -> None:
        self.role = role
        self.path = path
        self.closed = 0
        self.opened = 0

    def close_exact_notebook(self, *, sync_to_disk=False):
        self.closed += 1
        return {"closed": True, "source_notebook_id": f"old-{self.role}"}

    def working_notebook_open_lock(self):
        return nullcontext()

    def snapshot_open_notebooks(self):
        return {}

    def assert_no_active_working_conflict(self, **_kwargs):
        return None

    def open_working_notebook(self, name, path, **kwargs):
        self.opened += 1
        assert name == self.path.name
        assert path == self.path
        assert kwargs["template_paths"] == ()
        assert kwargs["lease_archive_kind"] == "index-checkpoint"
        notebook = {
            "id": f"live-{self.role}",
            "resource_type": "notebook",
            "name": name,
            "path": name,
        }
        lease = {
            "notebook_id": notebook["id"],
            "expected_local_path": str(path),
            "actual_local_path": str(path),
            "hierarchy_open_status": "passed",
        }
        return notebook, lease


def test_fresh_search_checkpoint_closes_and_reopens_exact_paths_only(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    roles = ("search-b", "source")
    paths = {role: (run_dir / "notebooks" / role).resolve() for role in roles}
    wrappers = {role: _CheckpointWrapper(role, paths[role]) for role in roles}
    notebooks = {
        role: {
            "id": f"old-{role}",
            "resource_type": "notebook",
            "name": role,
            "path": role,
        }
        for role in roles
    }
    leases = {
        role: {
            "notebook_id": f"old-{role}",
            "expected_local_path": str(paths[role]),
        }
        for role in roles
    }

    result = _checkpoint_fresh_search_bundle(
        scenario_name="search-all-open-notebooks",
        wrappers=wrappers,
        roles=roles,
        notebooks=notebooks,
        leases=leases,
        run_dir=run_dir,
    )

    assert result["status"] == "passed"
    assert result["force"] is False
    assert {role: notebooks[role]["id"] for role in roles} == {
        "search-b": "live-search-b",
        "source": "live-source",
    }
    assert all(wrapper.closed == 1 and wrapper.opened == 1 for wrapper in wrappers.values())
    persisted = json.loads(
        (run_dir / "fresh-index-activation-checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["templates_opened"] is False


class _FreshIndexRecipe:
    build_mode = SimpleNamespace(value="programmatic")
    bootstrap_scenario_name = None

    def __init__(self) -> None:
        self.cache_identity = SimpleNamespace(
            notebook_roles=(
                SimpleNamespace(role="search-b"),
                SimpleNamespace(role="source"),
            )
        )

    def manifest_keys_for_role(self, role, _args=None):
        return frozenset({f"page_{role}"})

    async def build(self, context):
        page = context.recorder.record_structure(
            f"page_{context.role}",
            {
                "id": f"old-page-{context.role}",
                "resource_type": "page",
                "title": context.role,
                "path": f"{context.notebook['path']}/{context.role}",
                "notebook_id": context.notebook_id,
                "section_id": f"section-{context.role}",
                "parent_id": f"section-{context.role}",
                "parent_page_id": None,
                "page_level": 1,
                "order": 0,
            },
        )
        return FixtureBuildResult({f"page_{context.role}": page}, {})

    def validate_live(self, observation):
        for role, current in observation.roles.items():
            assert current.notebook["id"] == f"live-{role}"
            assert current.build.structure[f"page_{role}"]["id"] == f"live-page-{role}"
            assert set(current.snapshot["page_hashes"]) == {f"live-page-{role}"}
        return FixtureValidationReport(
            passed=True,
            role_checks={role: ("validated",) for role in observation.roles},
            bundle_checks=("checkpointed",),
        )


class _FreshIndexClient:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def call_tool(self, name, arguments, retry_read=True):
        if name == "expand_hierarchy":
            notebook_id = str(arguments["root_id"])
            role = notebook_id.removeprefix("live-")
            notebook = {
                "id": notebook_id,
                "resource_type": "notebook",
                "name": role,
                "path": role,
                "parent_id": None,
            }
            page = {
                "id": f"live-page-{role}",
                "resource_type": "page",
                "title": role,
                "path": f"{role}/{role}",
                "notebook_id": notebook_id,
                "section_id": f"section-{role}",
                "parent_id": f"section-{role}",
                "parent_page_id": None,
                "page_level": 1,
                "order": 0,
            }
            self.events.append(f"tree:{role}")
            return {
                "tree": {
                    "item": notebook,
                    "children": [{"item": page, "children": []}],
                }
            }
        if name == "get_page_xml":
            page_id = str(arguments["page_id"])
            self.events.append(f"xml:{page_id}")
            return {
                "xml": (
                    '<one:Page xmlns:one="http://schemas.microsoft.com/office/'
                    'onenote/2013/onenote"><one:Title><one:OE><one:T>Page</one:T>'
                    "</one:OE></one:Title></one:Page>"
                )
            }
        raise AssertionError(name)


def test_fresh_index_fixture_checkpoints_before_one_full_page_read_per_role(tmp_path) -> None:
    recipe = _FreshIndexRecipe()
    scenario = SimpleNamespace(
        name="search-all-open-notebooks",
        fixture_recipe=recipe,
        requires_index_activation_checkpoint=True,
    )
    spec = SCENARIO_REGISTRY.get("search-all-open-notebooks").spec
    notebooks = {
        role: {
            "id": f"old-{role}",
            "resource_type": "notebook",
            "name": role,
            "path": role,
        }
        for role in ("search-b", "source")
    }
    checkpoint_calls = 0
    client = _FreshIndexClient()

    def checkpoint():
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        client.events.append("checkpoint")
        for role in notebooks:
            notebooks[role] = {**notebooks[role], "id": f"live-{role}"}
        return {"status": "passed"}

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest, result = asyncio.run(
        prepare_fixture_bundle(
            scenario,
            argparse.Namespace(),
            RuntimeOptions(run_dir, 300, False, False),
            client,
            notebooks,
            {role: str(run_dir / "notebooks" / role) for role in notebooks},
            spec,
            post_build_index_checkpoint=checkpoint,
        )
    )

    assert checkpoint_calls == 1
    assert client.events.index("checkpoint") < min(
        index for index, event in enumerate(client.events) if event.startswith("xml:")
    )
    assert [event for event in client.events if event.startswith("xml:")] == [
        "xml:live-page-search-b",
        "xml:live-page-source",
    ]
    assert manifest["index_activation_checkpoint"]["full_snapshot_per_role"] == 1
    assert result["index_activation_checkpoint"]["status"] == "passed"


def _search_fixture_observation() -> FixtureBundleObservation:
    source_notebook = {"id": "ns", "name": "source"}
    search_b_notebook = {"id": "nb", "name": "search-b"}
    source = {
        "probe_group": {
            "id": "g",
            "resource_type": "section_group",
            "parent_id": "ns",
        },
        "probe_section_1": {
            "id": "s1",
            "resource_type": "section",
            "parent_id": "g",
        },
        "probe_page_a1": {
            "id": "p1",
            "resource_type": "page",
            "section_id": "s1",
            "parent_page_id": None,
            "page_level": 1,
        },
        "probe_section_2": {
            "id": "s2",
            "resource_type": "section",
            "parent_id": "g",
        },
        "probe_page_a2": {
            "id": "p2",
            "resource_type": "page",
            "section_id": "s2",
            "parent_page_id": None,
            "page_level": 1,
        },
        "root_section": {
            "id": "sr",
            "resource_type": "section",
            "parent_id": "ns",
        },
        "probe_page_a3": {
            "id": "p3",
            "resource_type": "page",
            "section_id": "sr",
            "parent_page_id": None,
            "page_level": 1,
        },
    }
    search_b = {
        "probe_section_b": {
            "id": "sb",
            "resource_type": "section",
            "parent_id": "nb",
        },
        "probe_page_b1": {
            "id": "pb1",
            "resource_type": "page",
            "section_id": "sb",
            "parent_page_id": None,
            "page_level": 1,
        },
        "budget_page_b2": {
            "id": "pb2",
            "resource_type": "page",
            "section_id": "sb",
            "parent_page_id": None,
            "page_level": 1,
        },
    }

    def role_observation(role, notebook, structure, path):
        page_ids = {
            str(item["id"])
            for item in structure.values()
            if item["resource_type"] == "page"
        }
        return FixtureRoleObservation(
            role=role,
            args=argparse.Namespace(),
            notebook=notebook,
            notebook_path=path,
            snapshot={
                "notebook_id": notebook["id"],
                "items": list(structure.values()),
                "page_hashes": {page_id: f"hash-{page_id}" for page_id in page_ids},
            },
            build=FixtureBuildResult(structure, {}),
        )

    return FixtureBundleObservation(
        roles={
            "search-b": role_observation(
                "search-b", search_b_notebook, search_b, "C:/run/search-b"
            ),
            "source": role_observation(
                "source", source_notebook, source, "C:/run/source"
            ),
        }
    )


def test_search_recipe_uses_role_aware_complete_bundle_validation() -> None:
    recipe = SCENARIO_REGISTRY.get("search-all-open-notebooks").fixture_recipe
    observation = _search_fixture_observation()

    report = recipe.validate_live(observation)

    assert report.passed is True
    assert "every Search Page was read once during fixture snapshot validation" in (
        report.role_checks["source"]
    )
    assert "raw Search probes were not persisted in fixture evidence" in (
        report.bundle_checks
    )
    with pytest.raises(InvariantFailure, match="another role's structure"):
        recipe.validate(
            FixtureValidationContext(
                args=argparse.Namespace(),
                snapshot=observation.roles["source"].snapshot,
                role="search-b",
            ),
            observation.roles["source"].build,
        )


def test_search_recipe_fails_closed_without_complete_page_content_evidence() -> None:
    recipe = SCENARIO_REGISTRY.get("search-all-open-notebooks").fixture_recipe
    observation = _search_fixture_observation()
    source = observation.roles["source"]
    snapshot = dict(source.snapshot)
    snapshot["page_hashes"] = {}
    broken = FixtureBundleObservation(
        roles={
            **observation.roles,
            "source": FixtureRoleObservation(
                role=source.role,
                args=source.args,
                notebook=source.notebook,
                notebook_path=source.notebook_path,
                snapshot=snapshot,
                build=source.build,
            ),
        }
    )

    with pytest.raises(InvariantFailure, match="complete Page content evidence"):
        recipe.validate_live(broken)
