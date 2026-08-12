"""Pure contracts for guarded local filesystem publication."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.manual_validation import local_filesystem
from tests.manual_validation.local_filesystem import atomic_replace_with_retry
from tests.manual_validation.runtime import InvariantFailure


def _windows_error(code: int) -> OSError:
    error = PermissionError(f"injected WinError {code}")
    error.winerror = code
    return error


@pytest.mark.parametrize("winerror", [5, 32])
def test_transient_windows_replace_retries_with_bounded_backoff(
    tmp_path: Path,
    monkeypatch,
    winerror: int,
) -> None:
    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.json"
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    original_replace = os.replace
    attempts = 0
    delays: list[float] = []

    def flaky_replace(current_source, current_destination) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _windows_error(winerror)
        original_replace(current_source, current_destination)

    monkeypatch.setattr(local_filesystem, "_IS_WINDOWS", True)
    monkeypatch.setattr(local_filesystem.os, "replace", flaky_replace)
    monkeypatch.setattr(local_filesystem.time, "sleep", delays.append)

    atomic_replace_with_retry(source, destination)

    assert attempts == 3
    assert delays == [0.05, 0.1]
    assert destination.read_text(encoding="utf-8") == "new"
    assert not source.exists()


def test_transient_windows_replace_exhaustion_reraises_last_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.json"
    source.write_text("new", encoding="utf-8")
    attempts = 0
    errors: list[OSError] = []
    delays: list[float] = []

    def always_locked(_source, _destination) -> None:
        nonlocal attempts
        attempts += 1
        error = _windows_error(32)
        errors.append(error)
        raise error

    monkeypatch.setattr(local_filesystem, "_IS_WINDOWS", True)
    monkeypatch.setattr(local_filesystem.os, "replace", always_locked)
    monkeypatch.setattr(local_filesystem.time, "sleep", delays.append)

    with pytest.raises(OSError) as captured:
        atomic_replace_with_retry(source, destination)

    assert attempts == 6
    assert delays == [0.05, 0.1, 0.2, 0.4, 0.8]
    assert captured.value is errors[-1]
    assert source.exists()
    assert not destination.exists()


@pytest.mark.parametrize(
    ("is_windows", "winerror"),
    [(False, 5), (True, 3)],
)
def test_non_retryable_replace_error_is_immediate(
    tmp_path: Path,
    monkeypatch,
    is_windows: bool,
    winerror: int,
) -> None:
    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.json"
    source.write_text("new", encoding="utf-8")
    attempts = 0
    delays: list[float] = []

    def fail(_source, _destination) -> None:
        nonlocal attempts
        attempts += 1
        raise _windows_error(winerror)

    monkeypatch.setattr(local_filesystem, "_IS_WINDOWS", is_windows)
    monkeypatch.setattr(local_filesystem.os, "replace", fail)
    monkeypatch.setattr(local_filesystem.time, "sleep", delays.append)

    with pytest.raises(OSError):
        atomic_replace_with_retry(source, destination)

    assert attempts == 1
    assert delays == []


def test_retry_refuses_changed_source(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.json"
    source.write_text("new", encoding="utf-8")
    attempts = 0

    def change_source_then_fail(_source, _destination) -> None:
        nonlocal attempts
        attempts += 1
        source.write_text("changed-and-longer", encoding="utf-8")
        raise _windows_error(5)

    monkeypatch.setattr(local_filesystem, "_IS_WINDOWS", True)
    monkeypatch.setattr(local_filesystem.os, "replace", change_source_then_fail)
    monkeypatch.setattr(local_filesystem.time, "sleep", lambda _delay: None)

    with pytest.raises(InvariantFailure, match="source changed"):
        atomic_replace_with_retry(source, destination)

    assert attempts == 1


def test_retry_refuses_source_disappearing(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.json"
    source.write_text("new", encoding="utf-8")
    attempts = 0

    def remove_source_then_fail(_source, _destination) -> None:
        nonlocal attempts
        attempts += 1
        source.unlink()
        raise _windows_error(5)

    monkeypatch.setattr(local_filesystem, "_IS_WINDOWS", True)
    monkeypatch.setattr(local_filesystem.os, "replace", remove_source_then_fail)
    monkeypatch.setattr(local_filesystem.time, "sleep", lambda _delay: None)

    with pytest.raises(InvariantFailure, match="source changed"):
        atomic_replace_with_retry(source, destination)

    assert attempts == 1


def test_retry_refuses_destination_appearing(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "owned.txt").write_text("owned", encoding="utf-8")
    attempts = 0

    def create_destination_then_fail(_source, _destination) -> None:
        nonlocal attempts
        attempts += 1
        destination.mkdir()
        (destination / "competitor.txt").write_text("competitor", encoding="utf-8")
        raise _windows_error(32)

    monkeypatch.setattr(local_filesystem, "_IS_WINDOWS", True)
    monkeypatch.setattr(local_filesystem.os, "replace", create_destination_then_fail)
    monkeypatch.setattr(local_filesystem.time, "sleep", lambda _delay: None)

    with pytest.raises(InvariantFailure, match="destination changed"):
        atomic_replace_with_retry(
            source,
            destination,
            destination_must_be_absent=True,
        )

    assert attempts == 1
    assert (destination / "competitor.txt").read_text(encoding="utf-8") == "competitor"
    assert source.exists()


def test_retry_refuses_existing_destination_replacement(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.json"
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    attempts = 0

    def replace_destination_then_fail(_source, _destination) -> None:
        nonlocal attempts
        attempts += 1
        destination.unlink()
        destination.write_text("competitor-longer", encoding="utf-8")
        raise _windows_error(5)

    monkeypatch.setattr(local_filesystem, "_IS_WINDOWS", True)
    monkeypatch.setattr(local_filesystem.os, "replace", replace_destination_then_fail)
    monkeypatch.setattr(local_filesystem.time, "sleep", lambda _delay: None)

    with pytest.raises(InvariantFailure, match="destination changed"):
        atomic_replace_with_retry(source, destination)

    assert attempts == 1
    assert destination.read_text(encoding="utf-8") == "competitor-longer"


def test_retry_refuses_existing_destination_disappearing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.json"
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    attempts = 0

    def remove_destination_then_fail(_source, _destination) -> None:
        nonlocal attempts
        attempts += 1
        destination.unlink()
        raise _windows_error(32)

    monkeypatch.setattr(local_filesystem, "_IS_WINDOWS", True)
    monkeypatch.setattr(local_filesystem.os, "replace", remove_destination_then_fail)
    monkeypatch.setattr(local_filesystem.time, "sleep", lambda _delay: None)

    with pytest.raises(InvariantFailure, match="destination changed"):
        atomic_replace_with_retry(source, destination)

    assert attempts == 1
    assert source.exists()
    assert not destination.exists()


def test_destination_must_be_absent_refuses_existing_directory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()

    with pytest.raises(InvariantFailure, match="must not already exist"):
        atomic_replace_with_retry(
            source,
            destination,
            destination_must_be_absent=True,
        )

    assert source.exists()
    assert destination.exists()
