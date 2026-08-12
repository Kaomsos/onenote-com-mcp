from __future__ import annotations

import json
import string

from tests.manual_validation.mcp_stdio_client import summarize
from tests.manual_validation.runner import main
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.fixture_recipes.search_all_open_notebooks import (
    generate_search_probe,
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


def test_search_scenario_is_two_role_fresh_only_and_least_privilege() -> None:
    scenario = SCENARIO_REGISTRY.get("search-all-open-notebooks")
    spec = scenario.spec

    assert scenario.included_in_all is False
    assert scenario.fixture_recipe.supports_cache is False
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
    assert {"get_page_text", "search_pages"} <= spec.tool_allowlist
    assert spec.policy.writes_enabled is True
    assert spec.policy.deletes_enabled is False
    assert spec.policy.experimental_copy_enabled is False
    assert spec.policy.move_page_enabled is False
    assert spec.policy.move_containers_enabled is False
    assert spec.policy.raw_xml_enabled is False


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


def test_search_use_cache_dry_run_is_rejected_before_side_effects(capsys) -> None:
    assert main(
        ["search-all-open-notebooks", "--use-cache", "--dry-run", "--json"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["cache"]["decision"] == "rejected_fresh_only"
    assert payload["cache"]["enabled"] is False
    assert payload["expected_mcp_process_starts"] == 0
    assert payload["ordered_steps"][0]["step"] == "preflight-fresh-only-rejects-cache"
    assert payload["search_budget"]["max_pages"] == 4
