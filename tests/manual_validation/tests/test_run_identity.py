"""Pure contracts for local display time and canonical validation Notebook names."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from tests.manual_validation.run_identity import (
    new_run_identity,
    validate_notebook_label,
    validation_notebook_name,
    validation_notebook_names,
)
from tests.manual_validation.runner import main


def test_local_run_identity_uses_readable_windows_safe_local_seconds() -> None:
    local = datetime(
        2026,
        8,
        11,
        11,
        5,
        49,
        123_456,
        tzinfo=timezone(timedelta(hours=8), "China Standard Time"),
    )

    identity = new_run_identity(local)

    assert identity.safe_timestamp == "2026-08-11-11-05-49"
    assert identity.local_iso == "2026-08-11T11:05:49.123+08:00"
    assert identity.utc_iso == "2026-08-11T03:05:49.123+00:00"
    assert identity.timezone_name == "China Standard Time"
    assert not set('<>:"/\\|?*') & set(identity.safe_timestamp)


def test_local_run_identity_records_negative_and_fractional_offsets_in_evidence() -> None:
    local = datetime(
        2026,
        1,
        2,
        3,
        4,
        5,
        tzinfo=timezone(-timedelta(hours=3, minutes=30), "UTC-03:30"),
    )

    identity = new_run_identity(local)

    assert identity.safe_timestamp == "2026-01-02-03-04-05"
    assert identity.local_iso.endswith("-03:30")


def test_canonical_single_and_multi_role_notebook_names() -> None:
    identity = new_run_identity(
        datetime(2026, 8, 11, 11, 5, 49, 123_000, tzinfo=timezone(timedelta(hours=8)))
    )

    assert validation_notebook_name(
        "copy-page", identity, cached=False
    ) == "__copy-page-2026-08-11-11-05-49__"
    assert validation_notebook_name(
        "copy-page", identity, cached=True
    ) == "__copy-page-CACHED-2026-08-11-11-05-49__"
    names = validation_notebook_names(
        "cache-two-notebook-copy",
        identity,
        ("dest", "source"),
        cached=True,
    )
    assert "-dest-CACHED-" in names["dest"]
    assert "-source-CACHED-" in names["source"]
    assert names["dest"] != names["source"]


@pytest.mark.parametrize("label", ("UPPER", "has space", "unsafe/name", "_wrapped_"))
def test_notebook_label_rejects_noncanonical_values(label: str) -> None:
    with pytest.raises(ValueError, match="lowercase kebab-case"):
        validate_notebook_label(label)


def test_cache_dry_run_uses_canonical_cached_name_and_path(capsys) -> None:
    assert main(["copy-page", "--use-cache", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    fresh = payload["notebook_names"]["fresh"]["source"]
    cached = payload["notebook_names"]["cached"]["source"]
    assert "-CACHED-" not in fresh
    assert cached.count("-CACHED-") == 1
    assert payload["notebook_name"] == cached
    assert Path(payload["cache"]["roles"]["source"]["working_path"]).name == cached
    assert payload["cache"]["roles"]["source"]["working_name"] == cached
    assert not Path(payload["run_dir"]).exists()
