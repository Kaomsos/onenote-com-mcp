from __future__ import annotations

import os
from pathlib import Path
import sys

import conftest


def test_pytest_artifacts_are_outside_the_worktree(
    pytestconfig,
    tmp_path_factory,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    artifact_root = Path(os.environ["PYTEST_DEBUG_TEMPROOT"]).resolve()
    cache_root = Path(os.environ["PYTEST_CACHE_DIR"]).resolve()
    base_temp = tmp_path_factory.getbasetemp().resolve()

    assert not artifact_root.is_relative_to(repository_root)
    assert cache_root == artifact_root / "cache"
    assert pytestconfig.cache._cachedir.resolve() == cache_root
    assert base_temp.is_relative_to(artifact_root)


def test_pytest_tmp_retention_keeps_only_one_failed_session(pytestconfig) -> None:
    assert pytestconfig.getini("tmp_path_retention_policy") == "failed"
    assert pytestconfig.getini("tmp_path_retention_count") == "1"


def test_pytest_does_not_write_bytecode_into_the_worktree() -> None:
    assert sys.dont_write_bytecode is True


def test_root_conftest_bytecode_cleanup_is_exact(tmp_path) -> None:
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    generated = cache_dir / "conftest.cpython-313-pytest-9.pyc"
    unrelated = cache_dir / "other.cpython-313.pyc"
    generated.write_bytes(b"generated")
    unrelated.write_bytes(b"preserve")

    conftest._remove_root_conftest_bytecode(cache_dir)

    assert not generated.exists()
    assert unrelated.read_bytes() == b"preserve"
    assert cache_dir.is_dir()
