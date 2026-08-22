"""Bounded target-page hierarchy stability for post-restart validation.

This helper is used only by ``com-refresh-mutation``. It observes exact-ID
``expand_page`` metadata (title, id, parent, section, modified) and never
reads Page XML or body text. Tests inject a virtual clock so dry-run and
pytest never sleep on the real clock.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from local_onenote_mcp.hierarchy import display_name


PageObserve = Callable[[], Awaitable[Any]]
Sleep = Callable[[float], Awaitable[None]]
Monotonic = Callable[[], float]

BASELINE_DEADLINE_SECONDS = 8.0
FORWARD_DEADLINE_SECONDS = 12.0
POLL_INTERVAL_SECONDS = 1.0
REQUIRED_STABLE_OBSERVATIONS = 3
FORWARD_LINGER_OBSERVATIONS = 1
MAX_OBSERVATIONS = 16

STATUS_STABLE = "stable"
STATUS_DURABLE = "durable"
STATUS_NOT_STABLE = "not_stable"
STATUS_FORWARD_NOT_DURABLE = "forward_not_durable"
STATUS_FORWARD_NOT_STABLE = "forward_not_stable"
STATUS_OBSERVE_FAILED = "observe_failed"


class PageStabilityError(RuntimeError):
    """A fail-closed page-stability result with durable evidence."""

    def __init__(self, message: str, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


@dataclass(frozen=True)
class PageIdentity:
    page_id: str
    title: str
    parent_id: str
    section_id: str
    modified: str

    def signature(self) -> tuple[str, str, str, str, str]:
        return (
            self.page_id,
            self.title,
            self.parent_id,
            self.section_id,
            self.modified,
        )

    def confirmation(self) -> dict[str, str]:
        return {
            "page_id": self.page_id,
            "title": self.title,
            "parent_id": self.parent_id,
            "section_id": self.section_id,
            "modified": self.modified,
        }


def dry_run_page_stability_projection(*, phase: str) -> dict[str, Any]:
    return {
        "bounded_target_page_stability": True,
        "phase": phase,
        "allowed_operations": ["expand_page"],
        "stdin_read_performed": False,
        "sleep_performed": False,
        "gui_state_read": False,
        "required_stable_observations": REQUIRED_STABLE_OBSERVATIONS,
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "linger_observations": (
            FORWARD_LINGER_OBSERVATIONS if phase == "forward_durability" else 0
        ),
        "xml_recorded": False,
    }


def page_identity_from_expand(result: Any, *, page_id: str) -> PageIdentity:
    tree = result.get("tree") if isinstance(result, dict) else None
    item = tree.get("item") if isinstance(tree, dict) else None
    if not isinstance(item, dict) or item.get("resource_type") != "page":
        raise ValueError("expand_page did not return an exact Page tree.")
    observed_id = str(item.get("id") or "")
    if observed_id != page_id:
        raise ValueError("expand_page returned a different Page ID.")
    title = display_name(item)
    parent_id = str(
        item.get("parent_id") or item.get("parent_page_id") or item.get("section_id") or ""
    )
    section_id = str(item.get("section_id") or "")
    modified = str(item.get("modified") or "")
    if not title or not parent_id or not section_id or not modified:
        raise ValueError("expand_page omitted a required Page identity field.")
    return PageIdentity(
        page_id=observed_id,
        title=title,
        parent_id=parent_id,
        section_id=section_id,
        modified=modified,
    )


def _project_observation(
    identity: PageIdentity,
    *,
    attempt: int,
    stable: int,
    expected_title: str,
    original_title: str | None,
    marker_title: str | None,
    expected_page_id: str,
    expected_parent_id: str,
    expected_section_id: str,
    previous_signature: tuple[str, str, str, str, str] | None,
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "id_matches": identity.page_id == expected_page_id,
        "title_matches_expected": identity.title == expected_title,
        "title_matches_original": (
            identity.title == original_title if original_title is not None else False
        ),
        "title_matches_marker": (
            identity.title == marker_title if marker_title is not None else False
        ),
        "parent_matches": identity.parent_id == expected_parent_id,
        "section_matches": identity.section_id == expected_section_id,
        "modified_present": bool(identity.modified),
        "identity_stable": (
            previous_signature == identity.signature()
            if previous_signature is not None
            else False
        ),
        "stable": stable,
        "xml_recorded": False,
    }


def _evidence(
    *,
    status: str,
    page_id: str,
    attempts: int,
    stable_observations: int,
    elapsed_seconds: float,
    observations: list[dict[str, Any]],
    seen_marker: bool = False,
    reverted_to_original: bool = False,
    last: PageIdentity | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "page_id": page_id,
        "attempts": attempts,
        "stable_observations": stable_observations,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "xml_recorded": False,
        "seen_marker": seen_marker,
        "reverted_to_original": reverted_to_original,
        "observations": observations,
    }
    if last is not None:
        payload.update(last.confirmation())
    if extra:
        payload.update(dict(extra))
    return payload


async def wait_for_stable_page_baseline(
    observe: PageObserve,
    *,
    page_id: str,
    expected_title: str,
    expected_parent_id: str,
    expected_section_id: str,
    timeout_seconds: float = BASELINE_DEADLINE_SECONDS,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    required_stable_observations: int = REQUIRED_STABLE_OBSERVATIONS,
    max_observations: int = MAX_OBSERVATIONS,
    sleep: Sleep | None = None,
    monotonic: Monotonic | None = None,
) -> dict[str, Any]:
    """Wait until the owned Page identity is consecutively stable before rename."""

    result = await _observe_until(
        observe,
        page_id=page_id,
        expected_title=expected_title,
        original_title=expected_title,
        marker_title=None,
        expected_parent_id=expected_parent_id,
        expected_section_id=expected_section_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        required_stable_observations=required_stable_observations,
        linger_observations=0,
        max_observations=max_observations,
        sleep=sleep,
        monotonic=monotonic,
        detect_marker_rollback=False,
        success_status=STATUS_STABLE,
        timeout_status=STATUS_NOT_STABLE,
        timeout_message=(
            "Owned Page identity did not stay stable after COM refresh; "
            "rename_page was not called."
        ),
    )
    return result


async def observe_forward_rename_durability(
    observe: PageObserve,
    *,
    page_id: str,
    marker_title: str,
    original_title: str,
    expected_parent_id: str,
    expected_section_id: str,
    timeout_seconds: float = FORWARD_DEADLINE_SECONDS,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    required_stable_observations: int = REQUIRED_STABLE_OBSERVATIONS,
    linger_observations: int = FORWARD_LINGER_OBSERVATIONS,
    max_observations: int = MAX_OBSERVATIONS,
    sleep: Sleep | None = None,
    monotonic: Monotonic | None = None,
) -> dict[str, Any]:
    """Observe the forward marker until it is durable, or detect rollback."""

    return await _observe_until(
        observe,
        page_id=page_id,
        expected_title=marker_title,
        original_title=original_title,
        marker_title=marker_title,
        expected_parent_id=expected_parent_id,
        expected_section_id=expected_section_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        required_stable_observations=required_stable_observations,
        linger_observations=linger_observations,
        max_observations=max_observations,
        sleep=sleep,
        monotonic=monotonic,
        detect_marker_rollback=True,
        success_status=STATUS_DURABLE,
        timeout_status=STATUS_FORWARD_NOT_STABLE,
        timeout_message=(
            "Forward rename marker did not remain stable; restore was not called."
        ),
    )


async def _observe_until(
    observe: PageObserve,
    *,
    page_id: str,
    expected_title: str,
    original_title: str | None,
    marker_title: str | None,
    expected_parent_id: str,
    expected_section_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    required_stable_observations: int,
    linger_observations: int,
    max_observations: int,
    sleep: Sleep | None,
    monotonic: Monotonic | None,
    detect_marker_rollback: bool,
    success_status: str,
    timeout_status: str,
    timeout_message: str,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive.")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive.")
    if required_stable_observations < 1:
        raise ValueError("required_stable_observations must be at least 1.")
    if linger_observations < 0:
        raise ValueError("linger_observations cannot be negative.")
    if max_observations < required_stable_observations + linger_observations:
        raise ValueError("max_observations is smaller than the stability requirement.")

    sleeper = sleep or asyncio.sleep
    clock = monotonic or time.monotonic
    started = clock()
    deadline = started + timeout_seconds
    attempts = 0
    stable = 0
    linger_seen = 0
    seen_marker = False
    last: PageIdentity | None = None
    last_signature: tuple[str, str, str, str, str] | None = None
    observations: list[dict[str, Any]] = []

    while attempts < max_observations:
        try:
            identity = page_identity_from_expand(await observe(), page_id=page_id)
        except Exception as exc:
            evidence = _evidence(
                status=STATUS_OBSERVE_FAILED,
                page_id=page_id,
                attempts=attempts + 1,
                stable_observations=stable,
                elapsed_seconds=max(0.0, clock() - started),
                observations=observations,
                seen_marker=seen_marker,
                extra={"error_type": type(exc).__name__},
            )
            raise PageStabilityError(
                "Target Page expand_page observation failed.",
                evidence,
            ) from exc

        attempts += 1
        if marker_title is not None and identity.title == marker_title:
            seen_marker = True
        if (
            detect_marker_rollback
            and seen_marker
            and original_title is not None
            and identity.title == original_title
        ):
            observations.append(
                _project_observation(
                    identity,
                    attempt=attempts,
                    stable=0,
                    expected_title=expected_title,
                    original_title=original_title,
                    marker_title=marker_title,
                    expected_page_id=page_id,
                    expected_parent_id=expected_parent_id,
                    expected_section_id=expected_section_id,
                    previous_signature=last_signature,
                )
            )
            evidence = _evidence(
                status=STATUS_FORWARD_NOT_DURABLE,
                page_id=page_id,
                attempts=attempts,
                stable_observations=0,
                elapsed_seconds=max(0.0, clock() - started),
                observations=observations,
                seen_marker=True,
                reverted_to_original=True,
                last=identity,
            )
            raise PageStabilityError(
                "Forward rename marker reverted to the original title; "
                "restore was not called.",
                evidence,
            )

        accepted = (
            identity.page_id == page_id
            and identity.title == expected_title
            and identity.parent_id == expected_parent_id
            and identity.section_id == expected_section_id
            and bool(identity.modified)
        )
        signature = identity.signature() if accepted else None
        if accepted and signature == last_signature:
            stable += 1
        elif accepted:
            stable = 1
            last_signature = signature
            linger_seen = 0
        else:
            stable = 0
            last_signature = None
            linger_seen = 0
        last = identity
        observations.append(
            _project_observation(
                identity,
                attempt=attempts,
                stable=stable,
                expected_title=expected_title,
                original_title=original_title,
                marker_title=marker_title,
                expected_page_id=page_id,
                expected_parent_id=expected_parent_id,
                expected_section_id=expected_section_id,
                previous_signature=(
                    last_signature if stable > 1 else None
                ),
            )
        )
        if stable >= required_stable_observations:
            if linger_seen >= linger_observations:
                return _evidence(
                    status=success_status,
                    page_id=page_id,
                    attempts=attempts,
                    stable_observations=stable,
                    elapsed_seconds=max(0.0, clock() - started),
                    observations=observations,
                    seen_marker=seen_marker,
                    last=identity,
                )
            linger_seen += 1

        remaining = deadline - clock()
        if remaining <= 0 or attempts >= max_observations:
            break
        await sleeper(min(poll_interval_seconds, remaining))

    evidence = _evidence(
        status=timeout_status,
        page_id=page_id,
        attempts=attempts,
        stable_observations=stable,
        elapsed_seconds=max(0.0, clock() - started),
        observations=observations,
        seen_marker=seen_marker,
        last=last,
    )
    raise PageStabilityError(timeout_message, evidence)


__all__ = [
    "BASELINE_DEADLINE_SECONDS",
    "FORWARD_DEADLINE_SECONDS",
    "FORWARD_LINGER_OBSERVATIONS",
    "MAX_OBSERVATIONS",
    "POLL_INTERVAL_SECONDS",
    "PageIdentity",
    "PageStabilityError",
    "REQUIRED_STABLE_OBSERVATIONS",
    "STATUS_DURABLE",
    "STATUS_FORWARD_NOT_DURABLE",
    "STATUS_FORWARD_NOT_STABLE",
    "STATUS_NOT_STABLE",
    "STATUS_OBSERVE_FAILED",
    "STATUS_STABLE",
    "dry_run_page_stability_projection",
    "observe_forward_rename_durability",
    "page_identity_from_expand",
    "wait_for_stable_page_baseline",
]
