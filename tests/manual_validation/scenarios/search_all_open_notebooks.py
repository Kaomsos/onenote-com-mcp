"""HUMAN-GATED live validation for index-only Search scope and pagination."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from ..mcp_stdio_client import ClientFailure, MCPStdioClient
from ..runtime import InvariantFailure, RunnerFailure, RuntimeOptions
from ..test_utils import scenario_dir, write_json
from .base import Scenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.search_all_open_notebooks import RECIPE


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _probe_evidence(probe: str) -> dict[str, Any]:
    left, separator, right = probe.partition("-")
    return {
        "sha256": hashlib.sha256(probe.encode("utf-8")).hexdigest(),
        "length": len(probe),
        "separator_index": 15 if separator == "-" else None,
        "left_length": len(left),
        "right_length": len(right),
        "left_classes": {
            "has_upper": any(char.isupper() for char in left),
            "has_lower": any(char.islower() for char in left),
            "has_digit": any(char.isdigit() for char in left),
        },
        "right_classes": {
            "has_upper": any(char.isupper() for char in right),
            "has_lower": any(char.islower() for char in right),
            "has_digit": any(char.isdigit() for char in right),
        },
        "has_upper": any(char.isupper() for char in probe),
        "has_lower": any(char.islower() for char in probe),
        "has_digit": any(char.isdigit() for char in probe),
        "has_symbol": separator == "-",
        "raw_persisted": False,
    }


def _bridge_find_pages_count(path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            count += record.get("operation") == "find_pages"
    return count


def _safe_page_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            field: page.get(field)
            for field in ("id", "title", "notebook_id", "section_id", "path")
        }
        for page in result.get("pages", [])
        if isinstance(page, dict)
    ]


@SCENARIO_REGISTRY.register
class SearchAllOpenNotebooksScenario(Scenario):
    name = "search-all-open-notebooks"
    fixture_recipe = RECIPE
    included_in_all = True
    requires_index_activation_checkpoint = True
    timeout_default = 300
    help_text = (
        "HUMAN-GATED: validate index-only Search at root, Notebook, SectionGroup, "
        "and Section scope over two fresh disposable Notebooks."
    )

    async def _search(
        self,
        client: MCPStdioClient,
        query: str,
        scope: dict[str, str],
        **arguments: Any,
    ) -> dict[str, Any]:
        audit_path = client.run_dir / "bridge-calls.jsonl"
        before = _bridge_find_pages_count(audit_path)
        try:
            return await client.call_tool(
                "search_pages",
                {"query": query, "scope": scope, **arguments},
                retry_read=False,
            )
        finally:
            after = _bridge_find_pages_count(audit_path)
            if after != before + 1:
                raise InvariantFailure(
                    "Each public Search call must produce exactly one bridge find_pages audit record."
                )

    @staticmethod
    def _assert_result(
        result: dict[str, Any],
        expected_ids: set[str],
        expected_items: dict[str, dict[str, Any]],
        expected_scope_type: str,
        expected_scope_id: str | None,
    ) -> None:
        pages = result.get("pages", [])
        actual_ids = {str(page.get("id", "")) for page in pages}
        if actual_ids != expected_ids or len(pages) != len(expected_ids):
            raise InvariantFailure(
                f"Search result IDs differ: expected {sorted(expected_ids)}, received {sorted(actual_ids)}."
            )
        if result.get("search_backend") != "onenote_index":
            raise InvariantFailure("Search did not report the fixed onenote_index backend.")
        if result.get("pagination_consistency") != "live_index":
            raise InvariantFailure("Search did not report live_index pagination consistency.")
        for page in pages:
            expected = expected_items[str(page["id"])]
            for field in ("notebook_id", "section_id", "path"):
                if page.get(field) != expected.get(field):
                    raise InvariantFailure(
                        f"Search Page {page['id']} has incorrect {field} metadata."
                    )
        scope = result.get("scope", {})
        if scope.get("resource_type") != expected_scope_type:
            raise InvariantFailure("Search response scope type differs from the requested start.")
        if expected_scope_id is not None and scope.get("id") != expected_scope_id:
            raise InvariantFailure("Search response scope ID differs from the requested start ID.")

    async def _wait_for_stable_root(
        self,
        client: MCPStdioClient,
        query: str,
        expected_ids: set[str],
        out,
        *,
        use_cache: bool,
        max_attempts: int = 20,
    ) -> list[dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        previous: set[str] | None = None
        stable_count = 0
        for ordinal in range(1, max_attempts + 1):
            attempt: dict[str, Any] = {"attempt": ordinal, "at": _utc_now()}
            try:
                result = await self._search(
                    client,
                    query,
                    {"mode": "root"},
                    include_snippets=False,
                    page_size=200,
                )
                ids = {str(page.get("id", "")) for page in result.get("pages", [])}
                attempt.update(status="ok", count=len(ids), hit_ids=sorted(ids))
                stable_count = stable_count + 1 if ids == expected_ids and ids == previous else 1 if ids == expected_ids else 0
                previous = ids
            except ClientFailure as exc:
                attempt.update(
                    status="error",
                    error_category=exc.error_code or "client_failure",
                )
                stable_count = 0
                previous = None
            attempts.append(attempt)
            write_json(out / "readiness-attempts.json", {"attempts": attempts})
            if stable_count >= 2:
                return attempts
            if (
                previous is not None
                and expected_ids < previous
                and len(attempts) >= 2
                and attempts[-2].get("hit_ids") == attempt.get("hit_ids")
            ):
                write_json(
                    out / "probe-collision-warning.json",
                    {
                        "schema_version": 1,
                        "use_cache": use_cache,
                        "expected_ids": sorted(expected_ids),
                        "extra_hit_ids": sorted(previous - expected_ids),
                        "warning": (
                            "The Search probe matched another open working copy."
                        ),
                        "query_text_persisted": False,
                    },
                )
                raise RunnerFailure(
                    "search_probe_collision: another open working copy matched the "
                    "fixture probe; close the retained copy before retrying."
                )
            await asyncio.sleep(1)
        raise RunnerFailure("index_not_ready_or_failed: root Search never stabilized at four exact IDs.")

    async def _wait_for_expected_budget_failure(
        self,
        client: MCPStdioClient,
        query: str,
        scope: dict[str, str],
        out,
        *,
        search_arguments: dict[str, Any],
        expected_code: str,
        expected_message_fragment: str,
        passed_status: str,
        evidence_filename: str,
        exhausted_message: str,
        max_attempts: int = 20,
    ) -> list[dict[str, Any]]:
        """Retry only while the index is not ready; reject other errors immediately."""

        attempts: list[dict[str, Any]] = []
        evidence_path = out / evidence_filename
        for ordinal in range(1, max_attempts + 1):
            try:
                await self._search(
                    client,
                    query,
                    scope,
                    **search_arguments,
                )
                attempts.append({"attempt": ordinal, "status": "index_not_ready"})
            except ClientFailure as exc:
                expected = (
                    exc.error_code == expected_code
                    and expected_message_fragment in exc.error_message
                )
                attempts.append(
                    {
                        "attempt": ordinal,
                        "status": passed_status if expected else "unexpected_error",
                        "error_category": exc.error_code,
                    }
                )
                write_json(evidence_path, {"attempts": attempts})
                if expected:
                    return attempts
                raise RunnerFailure(
                    "Unexpected Search budget probe failure: "
                    f"{exc.error_code or 'client_failure'}: {exc.error_message}"
                ) from exc
            write_json(evidence_path, {"attempts": attempts})
            await asyncio.sleep(1)
        raise RunnerFailure(exhausted_message)

    async def execute(
        self,
        args,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        if client is None:
            raise RunnerFailure("Search scenario requires its single active scenario MCP client.")
        out = scenario_dir(options.run_dir, self.name)
        structure = {key: dict(value) for key, value in manifest["structure"].items()}

        a_pages = ["probe_page_a1", "probe_page_a2", "probe_page_a3"]
        primary_keys = [*a_pages, "probe_page_b1"]
        probe = self.fixture_recipe.probe
        probe_evidence = _probe_evidence(probe)
        if not all(
            probe_evidence[field]
            for field in ("has_upper", "has_lower", "has_digit", "has_symbol")
        ) or not all(probe_evidence["left_classes"].values()) or not all(
            probe_evidence["right_classes"].values()
        ) or probe_evidence["length"] != 32:
            raise InvariantFailure("Generated Search probe violates its 32-character contract.")
        budget_marker = self.fixture_recipe.budget_marker
        long_text_marker = self.fixture_recipe.long_text_marker
        query = f"{probe[:15]} AND {probe[16:]}"
        expected_items = {
            str(structure[key]["id"]): structure[key] for key in primary_keys
        }
        expected = {
            "root": {str(structure[key]["id"]) for key in primary_keys},
            "notebook": {str(structure[key]["id"]) for key in a_pages},
            "section_group": {
                str(structure["probe_page_a1"]["id"]),
                str(structure["probe_page_a2"]["id"]),
            },
            "section": {str(structure["probe_page_a1"]["id"])},
        }
        await self._wait_for_stable_root(
            client,
            query,
            expected["root"],
            out,
            use_cache=options.use_cache,
        )

        scope_cases = (
            ("root", {"mode": "root"}, "root", None),
            (
                "notebook",
                {"mode": "start_node", "start_node_id": str(manifest["notebooks"]["source"]["id"])},
                "notebook",
                str(manifest["notebooks"]["source"]["id"]),
            ),
            (
                "section_group",
                {"mode": "start_node", "start_node_id": str(structure["probe_group"]["id"])},
                "section_group",
                str(structure["probe_group"]["id"]),
            ),
            (
                "section",
                {"mode": "start_node", "start_node_id": str(structure["probe_section_1"]["id"])},
                "section",
                str(structure["probe_section_1"]["id"]),
            ),
        )
        scope_evidence: dict[str, Any] = {}
        for name, scope, scope_type, scope_id in scope_cases:
            result = await self._search(
                client, query, scope, include_snippets=False, page_size=200
            )
            self._assert_result(result, expected[name], expected_items, scope_type, scope_id)
            scope_evidence[name] = {
                "count": result["count"],
                "total_matches": result["total_matches"],
                "hit_ids": sorted(expected[name]),
                "pages": _safe_page_rows(result),
                "scope": result["scope"],
                "search_backend": result["search_backend"],
            }
        write_json(out / "scope-results.json", scope_evidence)

        pagination_attempts: list[dict[str, Any]] = []
        pagination_passed = False
        for ordinal in range(1, 4):
            before = await self._search(
                client, query, {"mode": "root"}, include_snippets=False, page_size=200
            )
            first = await self._search(
                client,
                query,
                {"mode": "root"},
                offset=0,
                page_size=2,
                include_snippets=False,
            )
            second = await self._search(
                client,
                query,
                {"mode": "root"},
                offset=2,
                page_size=2,
                include_snippets=False,
            )
            after = await self._search(
                client, query, {"mode": "root"}, include_snippets=False, page_size=200
            )
            before_ids = {str(page["id"]) for page in before["pages"]}
            after_ids = {str(page["id"]) for page in after["pages"]}
            union_ids = {str(page["id"]) for page in [*first["pages"], *second["pages"]]}
            passed = (
                before_ids == expected["root"]
                and after_ids == expected["root"]
                and union_ids == expected["root"]
                and first.get("next_offset") == 2
                and first.get("has_more") is True
                and second.get("next_offset") is None
                and second.get("has_more") is False
            )
            pagination_attempts.append(
                {
                    "attempt": ordinal,
                    "status": "passed" if passed else "index_changed_during_pagination",
                    "before_ids": sorted(before_ids),
                    "first_ids": sorted(str(page["id"]) for page in first["pages"]),
                    "second_ids": sorted(str(page["id"]) for page in second["pages"]),
                    "after_ids": sorted(after_ids),
                }
            )
            write_json(out / "pagination-attempts.json", {"attempts": pagination_attempts})
            if passed:
                pagination_passed = True
                break
            await asyncio.sleep(1)
        if not pagination_passed:
            raise RunnerFailure("index_changed_during_pagination: bounded pagination retries exhausted.")

        await self._wait_for_expected_budget_failure(
            client,
            budget_marker,
            {"mode": "root"},
            out,
            search_arguments={
                "offset": 99,
                "page_size": 1,
                "include_snippets": False,
            },
            expected_code="validation_error",
            expected_message_fragment="LOCAL_ONENOTE_MAX_SEARCH_PAGES=4",
            passed_status="candidate_budget_exceeded",
            evidence_filename="candidate-budget-attempts.json",
            exhausted_message=(
                "Candidate budget probe never produced five indexed candidates."
            ),
        )

        snippet_result = await self._search(
            client,
            probe[:15],
            {"mode": "start_node", "start_node_id": str(structure["probe_section_1"]["id"])},
            page_size=1,
            include_snippets=True,
        )
        if (
            {str(page["id"]) for page in snippet_result["pages"]} != expected["section"]
            or not isinstance(snippet_result["pages"][0].get("snippet"), str)
        ):
            raise InvariantFailure("Section snippet hydration did not return the exact A1 Page and snippet.")

        await self._wait_for_expected_budget_failure(
            client,
            long_text_marker,
            {
                "mode": "start_node",
                "start_node_id": str(structure["probe_section_b"]["id"]),
            },
            out,
            search_arguments={"page_size": 1, "include_snippets": True},
            expected_code="backend_error",
            expected_message_fragment="LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS=512",
            passed_status="total_char_budget_exceeded",
            evidence_filename="total-char-budget-attempts.json",
            exhausted_message=(
                "Long-text marker never produced the expected total character budget error."
            ),
        )

        evidence = {
            "scenario": self.name,
            "status": "passed",
            "fixture": fixture_result,
            "probe": probe_evidence,
            "scope_counts": {name: len(ids) for name, ids in expected.items()},
            "scope_hit_ids": {name: sorted(ids) for name, ids in expected.items()},
            "pagination_passed": True,
            "candidate_budget_before_slice_passed": True,
            "snippet_hydration_passed": True,
            "total_character_budget_passed": True,
            "search_backend": "onenote_index",
            "find_pages_calls_per_search": 1,
            "local_scan_or_fallback_observed": False,
            "raw_probe_persisted": False,
        }
        write_json(out / "result.json", evidence)
        return evidence


__all__ = ["SearchAllOpenNotebooksScenario"]
