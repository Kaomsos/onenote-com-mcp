"""Closed allowlist classification for OneNote bridge backend operations."""

from __future__ import annotations

from contextvars import ContextVar, Token
from enum import StrEnum

# Authoritative read-only bridge operations from bridge.py switch cases.
READ_OPERATIONS: frozenset[str] = frozenset(
    {
        "get_hierarchy",
        "get_hierarchy_parent",
        "get_special_location",
        "get_page_content",
        "get_binary_page_content",
        "find_pages",
        "find_meta",
        "get_hyperlink",
        "get_web_hyperlink",
    }
)

# Authoritative state-changing bridge operations from bridge.py switch cases.
STATE_CHANGING_OPERATIONS: frozenset[str] = frozenset(
    {
        "open_hierarchy",
        "open_hierarchy_batch",
        "update_hierarchy",
        "delete_hierarchy",
        "close_notebook",
        "create_new_page",
        "update_page_content",
        "delete_page_content",
        "publish",
        "navigate_to",
        "navigate_to_url",
        "sync_hierarchy",
        "merge_sections",
        "set_filing_location",
    }
)

# Exact internal filesystem effect operations recorded via record_backend_call().
# Conservatively treated as state-changing for mutation-epoch invalidation.
FILESYSTEM_OPERATIONS: frozenset[str] = frozenset(
    {
        "filesystem:copy_notebook_target_exists",
        "filesystem:image_source_is_file",
        "filesystem:image_dimension_read",
        "filesystem:image_source_read",
        "filesystem:publish_target_exists",
        "filesystem:publish_parent_mkdir",
        "filesystem:publish_target_is_file",
    }
)

BRIDGE_OPERATIONS: frozenset[str] = READ_OPERATIONS | STATE_CHANGING_OPERATIONS


class BackendOperationKind(StrEnum):
    READ = "read"
    STATE_CHANGING = "state_changing"
    UNKNOWN = "unknown"


_MUTATION_EPOCH: ContextVar[int] = ContextVar("local_onenote_mutation_epoch", default=0)


def current_mutation_epoch() -> int:
    return _MUTATION_EPOCH.get()


def reset_mutation_epoch() -> Token:
    return _MUTATION_EPOCH.set(0)


def restore_mutation_epoch(token: Token) -> None:
    _MUTATION_EPOCH.reset(token)


def classify_backend_operation(operation: str) -> BackendOperationKind:
    if operation in READ_OPERATIONS:
        return BackendOperationKind.READ
    if operation in STATE_CHANGING_OPERATIONS or operation in FILESYSTEM_OPERATIONS:
        return BackendOperationKind.STATE_CHANGING
    return BackendOperationKind.UNKNOWN


def advances_mutation_epoch(operation: str) -> bool:
    kind = classify_backend_operation(operation)
    return kind is not BackendOperationKind.READ


def advance_mutation_epoch() -> int:
    epoch = current_mutation_epoch() + 1
    _MUTATION_EPOCH.set(epoch)
    return epoch


def notify_backend_operation(operation: str) -> int:
    """Advance task-local mutation epoch before a state-changing backend call."""

    if not advances_mutation_epoch(operation):
        return current_mutation_epoch()
    epoch = current_mutation_epoch() + 1
    _MUTATION_EPOCH.set(epoch)
    return epoch
