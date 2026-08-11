"""Run-scoped local display identity and canonical validation Notebook names."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Iterable


_LABEL_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_MAX_NOTEBOOK_NAME_LENGTH = 120


@dataclass(frozen=True)
class RunIdentity:
    safe_timestamp: str
    local_iso: str
    utc_iso: str
    timezone_name: str

    def as_dict(self) -> dict[str, str]:
        return {
            "safe_timestamp": self.safe_timestamp,
            "local_iso": self.local_iso,
            "utc_iso": self.utc_iso,
            "timezone_name": self.timezone_name,
        }


def new_run_identity(now: datetime | None = None) -> RunIdentity:
    local = datetime.now().astimezone() if now is None else now
    if local.tzinfo is None or local.utcoffset() is None:
        raise ValueError("Run identity requires an aware local datetime.")
    safe_timestamp = local.strftime("%Y-%m-%d-%H-%M-%S")
    return RunIdentity(
        safe_timestamp=safe_timestamp,
        local_iso=local.isoformat(timespec="milliseconds"),
        utc_iso=local.astimezone(timezone.utc).isoformat(timespec="milliseconds"),
        timezone_name=str(local.tzname() or f"UTC{local.strftime('%z')}"),
    )


def validate_notebook_label(label: str) -> str:
    if _LABEL_PATTERN.fullmatch(label) is None:
        raise ValueError(
            "Notebook label must use lowercase kebab-case: [a-z0-9]+(-[a-z0-9]+)*."
        )
    return label


def validation_notebook_name(
    scenario_name: str,
    identity: RunIdentity,
    *,
    cached: bool,
    role: str | None = None,
    label: str | None = None,
) -> str:
    selected = validate_notebook_label(label or scenario_name)
    parts = [selected]
    if role is not None:
        parts.append(validate_notebook_label(role))
    if cached:
        parts.append("CACHED")
    parts.append(identity.safe_timestamp)
    name = f"__{'-'.join(parts)}__"
    if len(name) > _MAX_NOTEBOOK_NAME_LENGTH:
        raise ValueError(
            f"Generated validation Notebook name exceeds {_MAX_NOTEBOOK_NAME_LENGTH} characters."
        )
    return name


def validation_notebook_names(
    scenario_name: str,
    identity: RunIdentity,
    roles: Iterable[str],
    *,
    cached: bool,
    label: str | None = None,
) -> dict[str, str]:
    canonical_roles = tuple(roles)
    if not canonical_roles or len(set(canonical_roles)) != len(canonical_roles):
        raise ValueError("Validation Notebook roles must be non-empty and unique.")
    multi_role = len(canonical_roles) > 1
    return {
        role: validation_notebook_name(
            scenario_name,
            identity,
            cached=cached,
            role=role if multi_role else None,
            label=label,
        )
        for role in canonical_roles
    }


def run_safe_timestamp(args: argparse.Namespace) -> str:
    identity = getattr(args, "run_identity", None)
    if isinstance(identity, RunIdentity):
        return identity.safe_timestamp
    return new_run_identity().safe_timestamp


__all__ = [
    "RunIdentity",
    "new_run_identity",
    "run_safe_timestamp",
    "validate_notebook_label",
    "validation_notebook_name",
    "validation_notebook_names",
]
