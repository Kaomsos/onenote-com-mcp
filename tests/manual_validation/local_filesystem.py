"""Fail-closed helpers for local manual-validation filesystem publication."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import time
from typing import NamedTuple

from .runtime import InvariantFailure


WINDOWS_REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)
TRANSIENT_WINDOWS_REPLACE_ERRORS = frozenset({5, 32})
_IS_WINDOWS = os.name == "nt"


class _PathState(NamedTuple):
    file_type: int
    device: int
    inode: int
    size: int
    modified_ns: int


def _path_state(path: Path, *, role: str) -> _PathState | None:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise InvariantFailure(
            f"Cannot prove atomic replace {role} state: {path}"
        ) from exc
    return _PathState(
        stat.S_IFMT(value.st_mode),
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
    )


def _assert_retry_state(
    source: Path,
    destination: Path,
    *,
    expected_source: _PathState,
    expected_destination: _PathState | None,
) -> None:
    current_source = _path_state(source, role="source")
    if current_source != expected_source:
        raise InvariantFailure(
            "Atomic replace source changed after a transient Windows failure; "
            f"refusing to retry: {source}"
        )
    current_destination = _path_state(destination, role="destination")
    if current_destination != expected_destination:
        raise InvariantFailure(
            "Atomic replace destination changed after a transient Windows failure; "
            f"refusing to retry: {destination}"
        )


def atomic_replace_with_retry(
    source: Path,
    destination: Path,
    *,
    destination_must_be_absent: bool = False,
) -> None:
    """Atomically publish a local path, retrying only transient Windows locks.

    The source and destination identities are frozen before the first attempt.
    A retry is allowed only while both states remain unchanged.
    """

    source = Path(source)
    destination = Path(destination)
    expected_source = _path_state(source, role="source")
    if expected_source is None:
        raise InvariantFailure(f"Atomic replace source does not exist: {source}")
    expected_destination = _path_state(destination, role="destination")
    if destination_must_be_absent and expected_destination is not None:
        raise InvariantFailure(
            f"Atomic replace destination must not already exist: {destination}"
        )

    for retry_index in range(len(WINDOWS_REPLACE_RETRY_DELAYS) + 1):
        if retry_index:
            _assert_retry_state(
                source,
                destination,
                expected_source=expected_source,
                expected_destination=expected_destination,
            )
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            retryable = (
                _IS_WINDOWS
                and getattr(exc, "winerror", None)
                in TRANSIENT_WINDOWS_REPLACE_ERRORS
            )
            if not retryable or retry_index == len(WINDOWS_REPLACE_RETRY_DELAYS):
                raise
            time.sleep(WINDOWS_REPLACE_RETRY_DELAYS[retry_index])


__all__ = ["atomic_replace_with_retry"]
