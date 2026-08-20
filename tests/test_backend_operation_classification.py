"""Closed allowlist contracts for backend operation classification."""

from __future__ import annotations

import re
from pathlib import Path

from local_onenote_mcp.services.backend_operation_classification import (
    BRIDGE_OPERATIONS,
    FILESYSTEM_OPERATIONS,
    READ_OPERATIONS,
    STATE_CHANGING_OPERATIONS,
    BackendOperationKind,
    advances_mutation_epoch,
    classify_backend_operation,
    current_mutation_epoch,
    notify_backend_operation,
    reset_mutation_epoch,
    restore_mutation_epoch,
)

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


def _bridge_switch_operations() -> set[str]:
    host_path = _SRC_ROOT / "local_onenote_mcp" / "powershell_host.py"
    text = host_path.read_text(encoding="utf-8")
    return set(re.findall(r'"([a-z_]+)"\s*\{', text))


def _recorded_filesystem_operations() -> set[str]:
    discovered: set[str] = set()
    pattern = re.compile(r'record_backend_call\(\s*"(filesystem:[^"]+)"\s*\)')
    for path in _SRC_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        discovered.update(pattern.findall(text))
    return discovered


def test_bridge_operation_allowlist_is_closed_and_complete():
    discovered = _bridge_switch_operations()
    assert discovered == BRIDGE_OPERATIONS
    assert READ_OPERATIONS.isdisjoint(STATE_CHANGING_OPERATIONS)


def test_filesystem_operations_allowlist_matches_source_literals():
    discovered = _recorded_filesystem_operations()
    assert discovered == FILESYSTEM_OPERATIONS
    assert FILESYSTEM_OPERATIONS.isdisjoint(BRIDGE_OPERATIONS)


def test_unknown_operation_is_fail_safe_state_changing():
    assert classify_backend_operation("future_operation") is BackendOperationKind.UNKNOWN
    assert advances_mutation_epoch("future_operation") is True
    assert advances_mutation_epoch("get_hierarchy") is False
    assert classify_backend_operation("filesystem:brand_new") is BackendOperationKind.UNKNOWN
    assert advances_mutation_epoch("filesystem:brand_new") is True


def test_mutation_epoch_advances_only_for_state_changing_operations():
    token = reset_mutation_epoch()
    try:
        assert current_mutation_epoch() == 0
        assert notify_backend_operation("get_hierarchy") == 0
        assert notify_backend_operation("create_new_page") == 1
        assert notify_backend_operation("get_page_content") == 1
        assert notify_backend_operation("filesystem:copy_notebook_target_exists") == 2
        assert notify_backend_operation("filesystem:brand_new") == 3
    finally:
        restore_mutation_epoch(token)


def test_classification_has_no_prefix_matching():
    source = (
        _SRC_ROOT
        / "local_onenote_mcp"
        / "services"
        / "backend_operation_classification.py"
    ).read_text(encoding="utf-8")
    assert "startswith(" not in source
    assert "endswith(" not in source
    assert "fnmatch" not in source
