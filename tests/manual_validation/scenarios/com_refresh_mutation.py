"""HUMAN-GATED recoverable mutation after same-MCP OneNote close and recover."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from ..lifecycle import NotebookLifecycleWrapper
from ..mcp_stdio_client import ClientFailure, MCPStdioClient
from ..onenote_exit_wait import (
    POLL_INTERVAL_SECONDS,
    OneNoteExitWaitError,
    is_fully_stopped_onenote_desktop,
    wait_for_onenote_fully_stopped,
)
from ..page_stability import (
    BASELINE_DEADLINE_SECONDS,
    FORWARD_DEADLINE_SECONDS,
    FORWARD_LINGER_OBSERVATIONS,
    MAX_OBSERVATIONS,
    POLL_INTERVAL_SECONDS as PAGE_STABILITY_POLL_INTERVAL_SECONDS,
    REQUIRED_STABLE_OBSERVATIONS,
    STATUS_FORWARD_NOT_DURABLE,
    PageStabilityError,
    observe_forward_rename_durability,
    wait_for_stable_page_baseline,
)
from ..runtime import InvariantFailure, RestoreFailure, RunnerFailure, RuntimeOptions
from ..run_identity import run_safe_timestamp
from ..test_utils import (
    capture_snapshot,
    display_name,
    find_snapshot_item,
    resolve_manifest_item,
    scenario_dir,
    snapshot_ids,
    validate_manifest_notebook,
    write_json,
)
from .base import Scenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.com_refresh_mutation import RECIPE

ConfirmationReader = Callable[[str], str]


def _require_attempt_contract(response: dict[str, Any], operation: str) -> dict[str, Any]:
    reconciliation = response.get("reconciliation")
    expected = {
        "state": "applied",
        "mutation_attempts": 1,
        "mutation_replayed": False,
        "observed_outcome": "applied",
    }
    if not isinstance(reconciliation, dict) or any(
        reconciliation.get(key) != value for key, value in expected.items()
    ):
        raise InvariantFailure(
            f"{operation} omitted or violated its mutation attempt evidence."
        )
    return dict(reconciliation)


def _named_pages(snapshot: dict[str, Any], title: str) -> list[dict[str, Any]]:
    return [
        item
        for item in snapshot.get("items", [])
        if item.get("resource_type") == "page" and display_name(item) == title
    ]


async def _wait_until_fully_stopped(
    client: MCPStdioClient,
    args: argparse.Namespace,
    options: RuntimeOptions,
) -> dict[str, Any]:
    wait_options = getattr(args, "onenote_exit_wait", {}) or {}

    async def probe() -> dict[str, Any]:
        return await client.call_health_preflight(allow_desktop_not_running=True)

    try:
        return await wait_for_onenote_fully_stopped(
            probe,
            timeout_seconds=float(wait_options.get("timeout_seconds", options.timeout)),
            poll_interval_seconds=float(
                wait_options.get("poll_interval_seconds", POLL_INTERVAL_SECONDS)
            ),
            sleep=wait_options.get("sleep"),
            monotonic=wait_options.get("monotonic"),
        )
    except OneNoteExitWaitError as exc:
        raise InvariantFailure(str(exc)) from exc


def _default_confirmation_reader(prompt: str) -> str:
    print(prompt, file=sys.stderr, flush=True)
    return sys.stdin.readline()


def _confirm_onenote_closed(args: argparse.Namespace, run_dir_name: str) -> None:
    expected = f"CLOSED {run_dir_name} ONENOTE CONTINUE"
    reader: ConfirmationReader = getattr(
        args, "confirmation_reader", _default_confirmation_reader
    )
    response = reader(
        "Fully exit OneNote Desktop now. Leave this MCP process running. "
        "After OneNote has fully exited, continue so the same process can recover "
        "the COM client and apply one recoverable Page rename.\n"
        f"Type exactly: {expected}"
    )
    if response.strip() != expected:
        raise InvariantFailure("Run-bound OneNote-closed confirmation was not provided.")


def _require_recovery_refresh(refresh: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(refresh, dict):
        raise InvariantFailure(
            "COM refresh mutation launch omitted com_client_refresh."
        )
    outcome = refresh.get("outcome")
    if outcome == "refreshed":
        generation = refresh.get("generation")
        epoch = refresh.get("com_epoch")
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
            or isinstance(epoch, bool)
            or not isinstance(epoch, int)
            or epoch < 1
        ):
            raise InvariantFailure(
                "refreshed recovery omitted a valid generation or com_epoch."
            )
        return refresh
    if outcome == "host_discarded":
        discarded = refresh.get("discarded_generation")
        if isinstance(discarded, bool) or not isinstance(discarded, int) or discarded < 1:
            raise InvariantFailure(
                "host_discarded recovery omitted a valid discarded_generation."
            )
        return refresh
    raise InvariantFailure(
        "COM refresh mutation recovery must be refreshed or host_discarded; "
        f"got {outcome!r}."
    )


def _require_harness_refresh(
    refresh: dict[str, Any],
    *,
    owner: str,
) -> dict[str, Any]:
    if not isinstance(refresh, dict):
        raise InvariantFailure(
            f"{owner} COM refresh omitted a content-free result."
        )
    outcome = refresh.get("outcome")
    if outcome == "refreshed":
        generation = refresh.get("generation")
        epoch = refresh.get("com_epoch")
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
            or isinstance(epoch, bool)
            or not isinstance(epoch, int)
            or epoch < 1
        ):
            raise InvariantFailure(
                f"{owner} refreshed recovery omitted a valid generation or com_epoch."
            )
        return refresh
    if outcome == "host_discarded":
        discarded = refresh.get("discarded_generation")
        if isinstance(discarded, bool) or not isinstance(discarded, int) or discarded < 1:
            raise InvariantFailure(
                f"{owner} host_discarded recovery omitted a valid discarded_generation."
            )
        return refresh
    if outcome == "not_needed":
        return refresh
    raise InvariantFailure(
        f"{owner} COM refresh must be refreshed, host_discarded, or "
        f"not_needed; got {outcome!r}."
    )


def _require_internal_refresh(refresh: dict[str, Any]) -> dict[str, Any]:
    return _require_harness_refresh(refresh, owner="Internal validation")


def _require_lifecycle_refresh(refresh: dict[str, Any]) -> dict[str, Any]:
    return _require_harness_refresh(refresh, owner="Lifecycle validation")


def _rpc_unavailable_hresult(message: str) -> str | None:
    normalized = message.upper()
    if "0X800706BA" in normalized or "800706BA" in normalized:
        return "0x800706BA"
    return None


async def _refresh_and_probe_internal_validation_com(
    client: MCPStdioClient,
    *,
    page_id: str,
    out: Path,
) -> dict[str, Any]:
    try:
        internal_refresh = client.refresh_internal_com_client()
    except ClientFailure as exc:
        write_json(
            out / "internal-bridge-refresh.json",
            {
                "status": "failed",
                "xml_recorded": False,
                "error_type": type(exc).__name__,
            },
        )
        raise InvariantFailure(
            "Internal validation COM refresh failed; rename_page was not called."
        ) from exc
    write_json(out / "internal-bridge-refresh.json", internal_refresh)
    _require_internal_refresh(internal_refresh)

    try:
        probe = await client.call_tool(
            "get_page_xml",
            {"page_id": page_id, "page_info": "all"},
            retry_read=False,
        )
    except ClientFailure as exc:
        failed: dict[str, Any] = {
            "status": "failed",
            "page_id": page_id,
            "xml_present": False,
            "xml_recorded": False,
            "error_type": type(exc).__name__,
        }
        hresult = _rpc_unavailable_hresult(str(exc))
        if hresult is not None:
            failed["hresult"] = hresult
        write_json(out / "internal-page-xml-probe.json", failed)
        raise InvariantFailure(
            "Internal validation COM probe failed after refresh; "
            "rename_page was not called."
        ) from exc
    xml = probe.get("xml") if isinstance(probe, dict) else None
    if not isinstance(xml, str) or not xml.strip():
        write_json(
            out / "internal-page-xml-probe.json",
            {
                "status": "failed",
                "page_id": page_id,
                "xml_present": False,
                "xml_recorded": False,
            },
        )
        raise InvariantFailure(
            "Internal validation COM probe did not return Page XML; "
            "rename_page was not called."
        )
    write_json(
        out / "internal-page-xml-probe.json",
        {
            "status": "ready",
            "page_id": page_id,
            "xml_present": True,
            "xml_recorded": False,
        },
    )
    return internal_refresh


def _refresh_and_probe_lifecycle_validation_com(
    wrapper: NotebookLifecycleWrapper,
    *,
    notebook_id: str,
    out: Path,
) -> dict[str, Any]:
    try:
        lifecycle_refresh = wrapper.refresh_com_client()
    except (ClientFailure, RestoreFailure, RunnerFailure) as exc:
        write_json(
            out / "lifecycle-bridge-refresh.json",
            {
                "status": "failed",
                "xml_recorded": False,
                "error_type": type(exc).__name__,
            },
        )
        raise InvariantFailure(
            "Lifecycle validation COM refresh failed; rename_page was not called."
        ) from exc
    write_json(out / "lifecycle-bridge-refresh.json", lifecycle_refresh)
    _require_lifecycle_refresh(lifecycle_refresh)

    try:
        current = wrapper.get_exact_notebook()
    except (RestoreFailure, RunnerFailure) as exc:
        failed: dict[str, Any] = {
            "status": "failed",
            "notebook_id": notebook_id,
            "xml_recorded": False,
            "error_type": type(exc).__name__,
        }
        hresult = _rpc_unavailable_hresult(str(exc))
        if hresult is not None:
            failed["hresult"] = hresult
        write_json(out / "lifecycle-notebook-probe.json", failed)
        raise InvariantFailure(
            "Lifecycle validation COM probe failed after refresh; "
            "rename_page was not called."
        ) from exc
    if not isinstance(current, dict) or str(current.get("id")) != notebook_id:
        write_json(
            out / "lifecycle-notebook-probe.json",
            {
                "status": "failed",
                "notebook_id": notebook_id,
                "xml_recorded": False,
            },
        )
        raise InvariantFailure(
            "Lifecycle validation COM probe did not return the leased Notebook; "
            "rename_page was not called."
        )
    write_json(
        out / "lifecycle-notebook-probe.json",
        {
            "status": "ready",
            "notebook_id": notebook_id,
            "xml_recorded": False,
        },
    )
    return lifecycle_refresh


def _page_stability_options(args: argparse.Namespace) -> dict[str, Any]:
    options = getattr(args, "page_stability", {}) or {}
    return {
        "timeout_seconds": float(
            options.get("baseline_timeout_seconds", BASELINE_DEADLINE_SECONDS)
        ),
        "forward_timeout_seconds": float(
            options.get("forward_timeout_seconds", FORWARD_DEADLINE_SECONDS)
        ),
        "poll_interval_seconds": float(
            options.get(
                "poll_interval_seconds", PAGE_STABILITY_POLL_INTERVAL_SECONDS
            )
        ),
        "required_stable_observations": int(
            options.get(
                "required_stable_observations", REQUIRED_STABLE_OBSERVATIONS
            )
        ),
        "linger_observations": int(
            options.get("linger_observations", FORWARD_LINGER_OBSERVATIONS)
        ),
        "max_observations": int(options.get("max_observations", MAX_OBSERVATIONS)),
        "sleep": options.get("sleep"),
        "monotonic": options.get("monotonic"),
    }


def _expand_owned_page(client: MCPStdioClient, page_id: str):
    async def observe() -> dict[str, Any]:
        return await client.call_tool(
            "expand_page",
            {"page_id": page_id},
            retry_read=False,
        )

    return observe


async def _stabilize_target_page_baseline(
    client: MCPStdioClient,
    *,
    page_id: str,
    expected_title: str,
    expected_parent_id: str,
    expected_section_id: str,
    args: argparse.Namespace,
    out: Path,
) -> dict[str, Any]:
    options = _page_stability_options(args)
    try:
        baseline = await wait_for_stable_page_baseline(
            _expand_owned_page(client, page_id),
            page_id=page_id,
            expected_title=expected_title,
            expected_parent_id=expected_parent_id,
            expected_section_id=expected_section_id,
            timeout_seconds=options["timeout_seconds"],
            poll_interval_seconds=options["poll_interval_seconds"],
            required_stable_observations=options["required_stable_observations"],
            max_observations=options["max_observations"],
            sleep=options["sleep"],
            monotonic=options["monotonic"],
        )
    except PageStabilityError as exc:
        write_json(out / "page-baseline-stability.json", exc.evidence)
        raise InvariantFailure(str(exc)) from exc
    write_json(out / "page-baseline-stability.json", baseline)
    return baseline


async def _observe_forward_durability(
    client: MCPStdioClient,
    *,
    page_id: str,
    marker_title: str,
    original_title: str,
    expected_parent_id: str,
    expected_section_id: str,
    args: argparse.Namespace,
    out: Path,
) -> dict[str, Any]:
    options = _page_stability_options(args)
    try:
        durability = await observe_forward_rename_durability(
            _expand_owned_page(client, page_id),
            page_id=page_id,
            marker_title=marker_title,
            original_title=original_title,
            expected_parent_id=expected_parent_id,
            expected_section_id=expected_section_id,
            timeout_seconds=options["forward_timeout_seconds"],
            poll_interval_seconds=options["poll_interval_seconds"],
            required_stable_observations=options["required_stable_observations"],
            linger_observations=options["linger_observations"],
            max_observations=options["max_observations"],
            sleep=options["sleep"],
            monotonic=options["monotonic"],
        )
    except PageStabilityError as exc:
        write_json(out / "forward-durability.json", exc.evidence)
        status = exc.evidence.get("status")
        if status == STATUS_FORWARD_NOT_DURABLE:
            raise InvariantFailure(
                "COM refresh mutation forward rename was not durable "
                "(forward_not_durable); restore was not called."
            ) from exc
        raise InvariantFailure(
            "COM refresh mutation forward rename did not remain stable; "
            "restore was not called."
        ) from exc
    write_json(out / "forward-durability.json", durability)
    return durability


def _require_unique_page_title(
    snapshot: dict[str, Any],
    *,
    page_id: str,
    title: str,
    phase: str,
) -> dict[str, Any]:
    matches = _named_pages(snapshot, title)
    if len(matches) != 1 or str(matches[0].get("id")) != page_id:
        raise InvariantFailure(
            f"{phase} did not leave exactly one Page titled {title!r} at the owned ID."
        )
    return matches[0]


@SCENARIO_REGISTRY.register
class ComRefreshMutationScenario(Scenario):
    name = "com-refresh-mutation"
    fixture_recipe = RECIPE
    included_in_all = False
    requires_lifecycle_wrappers = True
    close_source_before_mcp_exit = True
    help_text = (
        "HUMAN-GATED: after fixture COM is READY, prove OneNote fully stopped, "
        "recover on the same MCP via launch_onenote_gui, refresh the harness "
        "internal COM and lifecycle COM, probe exact Page XML and the leased "
        "Notebook, stabilize the owned Page identity, apply one unique Page "
        "rename only after a durable marker observation, then close the leased "
        "Notebook before MCP teardown."
    )
    worksite_dry_run_action = "preserve-unique-com-refresh-renamed-page"

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        raise RunnerFailure(
            "COM refresh mutation requires execute_with_lifecycle and its source wrapper."
        )

    async def execute_with_lifecycle(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
        wrappers: Mapping[str, NotebookLifecycleWrapper],
    ) -> dict[str, Any]:
        if client is None:
            raise RunnerFailure(
                "COM refresh mutation requires its single active scenario MCP client."
            )
        wrapper = wrappers.get("source") if isinstance(wrappers, Mapping) else None
        if wrapper is None:
            raise RunnerFailure(
                "COM refresh mutation requires its source Notebook lifecycle wrapper."
            )
        notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
        page = resolve_manifest_item(manifest, "page_target")
        out = scenario_dir(options.run_dir, self.name)
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        current = find_snapshot_item(before, str(page["id"]))
        if current is None or current.get("resource_type") != "page":
            raise RunnerFailure("Owned COM refresh mutation Page is not active.")
        original_title = display_name(current)
        marker = f"COM-REFRESH-{run_safe_timestamp(args)}"
        if marker == original_title:
            raise RunnerFailure("Unique refresh mutation marker collided with the fixture title.")

        _confirm_onenote_closed(args, options.run_dir.name)
        write_json(
            out / "onenote-closed-confirmation.json",
            {
                "accepted": True,
                "confirmation_mode": "interactive_stdin",
                "confirmation_value_recorded": False,
                "onenote_closed_by_user": True,
                "mcp_process_kept": True,
            },
        )
        try:
            stopped = await _wait_until_fully_stopped(client, args, options)
        except InvariantFailure as exc:
            evidence = getattr(exc.__cause__, "evidence", None)
            if isinstance(evidence, dict):
                write_json(out / "health-after-user-close.json", evidence)
            raise
        write_json(out / "health-after-user-close.json", stopped)

        launched = await client.call_tool(
            "launch_onenote_gui",
            {},
            retry_read=False,
        )
        write_json(out / "launch-refresh.json", launched)
        if (
            launched.get("status") != "started"
            or launched.get("launch_attempted") is not True
            or launched.get("launch_attempts") != 1
            or launched.get("ready") is not True
        ):
            raise InvariantFailure(
                "COM refresh mutation did not start OneNote after a fully stopped health proof."
            )
        refresh = _require_recovery_refresh(launched.get("com_client_refresh"))
        internal_refresh = await _refresh_and_probe_internal_validation_com(
            client,
            page_id=str(current["id"]),
            out=out,
        )
        lifecycle_refresh = _refresh_and_probe_lifecycle_validation_com(
            wrapper,
            notebook_id=str(notebook_id),
            out=out,
        )
        expected_parent_id = str(current.get("parent_id") or current["section_id"])
        baseline = await _stabilize_target_page_baseline(
            client,
            page_id=str(current["id"]),
            expected_title=original_title,
            expected_parent_id=expected_parent_id,
            expected_section_id=str(current["section_id"]),
            args=args,
            out=out,
        )

        forward = await client.call_tool(
            "rename_page",
            {
                "page_id": current["id"],
                "title": marker,
                "expected_title": original_title,
                "expected_section_id": baseline["section_id"],
                "expected_modified": baseline["modified"],
            },
        )
        write_json(out / "rename-forward.json", forward)
        _require_attempt_contract(forward, "rename_page")
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        if snapshot_ids(before) != snapshot_ids(after):
            raise InvariantFailure("COM refresh mutation changed hierarchy object IDs.")
        changed = find_snapshot_item(after, str(current["id"]))
        if changed is None:
            raise InvariantFailure("Forward rename left the owned Page unavailable.")
        after_title = display_name(changed)
        if after_title == original_title:
            write_json(
                out / "forward-durability.json",
                {
                    "status": STATUS_FORWARD_NOT_DURABLE,
                    "page_id": str(current["id"]),
                    "seen_marker": False,
                    "reverted_to_original": True,
                    "xml_recorded": False,
                    "source": "after_snapshot",
                },
            )
            raise InvariantFailure(
                "COM refresh mutation forward rename was not durable "
                "(forward_not_durable); restore was not called."
            )
        _require_unique_page_title(
            after,
            page_id=str(current["id"]),
            title=marker,
            phase="Forward rename",
        )
        if changed.get("parent_id") != current.get("parent_id"):
            raise InvariantFailure("COM refresh mutation changed the owned Page parent.")
        if before.get("page_body_hashes", {}).get(str(current["id"])) != after.get(
            "page_body_hashes", {}
        ).get(str(current["id"])):
            raise InvariantFailure("COM refresh mutation changed Page content outside the title.")
        await _observe_forward_durability(
            client,
            page_id=str(current["id"]),
            marker_title=marker,
            original_title=original_title,
            expected_parent_id=expected_parent_id,
            expected_section_id=str(current["section_id"]),
            args=args,
            out=out,
        )

        if getattr(args, "keep_worksite", False):
            worksite = {
                "status": "preserved_after_unique_com_refresh_rename",
                "target_ids": [str(current["id"])],
                "verified": True,
                "manual_cleanup_required": True,
                "cleanup": [
                    f"Rename Page {current['id']} back to {original_title!r} after inspection."
                ],
            }
            write_json(out / "worksite.json", worksite)
            result = {
                "scenario": self.name,
                "status": "passed",
                "target_ids": worksite["target_ids"],
                "marker": marker,
                "refresh_outcome": refresh.get("outcome"),
                "internal_refresh_outcome": internal_refresh.get("outcome"),
                "internal_page_xml_probe_ready": True,
                "lifecycle_refresh_outcome": lifecycle_refresh.get("outcome"),
                "lifecycle_notebook_probe_ready": True,
                "target_page_baseline_stable": True,
                "forward_rename_durable": True,
                "onenote_fully_stopped_after_user_close": True,
                "same_mcp_recovered_after_stop": True,
                "restored": False,
                "worksite_preserved": True,
                "remaining_state": worksite,
            }
            write_json(out / "result.json", result)
            return result

        restore_target = find_snapshot_item(after, str(current["id"]))
        if restore_target is None:
            raise RestoreFailure(
                "COM refresh mutation succeeded but the exact Page was unavailable for restore."
            )
        try:
            restore = await client.call_tool(
                "rename_page",
                {
                    "page_id": restore_target["id"],
                    "title": original_title,
                    "expected_title": marker,
                    "expected_section_id": restore_target["section_id"],
                    "expected_modified": restore_target.get("modified"),
                },
            )
            write_json(out / "rename-restore.json", restore)
            _require_attempt_contract(restore, "rename_page")
            restored = await capture_snapshot(client, notebook_id)
            write_json(out / "restored.json", restored)
            _require_unique_page_title(
                restored,
                page_id=str(current["id"]),
                title=original_title,
                phase="Restore rename",
            )
            if _named_pages(restored, marker):
                raise RestoreFailure("Unique refresh mutation marker remained after restore.")
            if snapshot_ids(before) != snapshot_ids(restored):
                raise RestoreFailure("Restore changed hierarchy object IDs.")
        except (InvariantFailure, RunnerFailure) as exc:
            raise RestoreFailure(
                f"COM refresh mutation succeeded but restoration failed: {exc}"
            ) from exc

        result = {
            "scenario": self.name,
            "status": "passed",
            "target_ids": [str(current["id"])],
            "marker": marker,
            "refresh_outcome": refresh.get("outcome"),
            "internal_refresh_outcome": internal_refresh.get("outcome"),
            "internal_page_xml_probe_ready": True,
            "lifecycle_refresh_outcome": lifecycle_refresh.get("outcome"),
            "lifecycle_notebook_probe_ready": True,
            "target_page_baseline_stable": True,
            "forward_rename_durable": True,
            "onenote_fully_stopped_after_user_close": True,
            "same_mcp_recovered_after_stop": True,
            "restored": True,
            "worksite_preserved": False,
        }
        write_json(out / "result.json", result)
        return result


__all__ = ["ComRefreshMutationScenario"]
