"""Secure local bridge to the Windows OneNote COM API.

Runtime, services, and tools call this module only. They never receive a COM
proxy. The selected adapter owns process, thread, and client lifetime.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .com_client import (
    DELIVERY_NOT_SUBMITTED,
    DELIVERY_RESPONDED,
    ComClient,
    ComClientError,
    create_com_client,
)
from .execution_context import current_correlation_id
from .onenote_errors import OneNoteBridgeError, OneNoteError, bridge_error
from .powershell_host import POWERSHELL_ONE_SHOT_SCRIPT
from .settings import parse_bridge_adapter_name


# Compatibility alias for existing script-contract tests.
POWERSHELL_BRIDGE = POWERSHELL_ONE_SHOT_SCRIPT


class OneNoteBridge:
    """Execute fixed OneNote COM operations through a selected COM client."""

    def __init__(
        self,
        timeout_seconds: int = 90,
        audit_path: Path | None = None,
        *,
        adapter: str | None = None,
        client: ComClient | None = None,
    ) -> None:
        self.timeout_seconds = int(timeout_seconds)
        self.audit_path = audit_path
        if client is not None:
            self._client = client
        else:
            name = (
                parse_bridge_adapter_name()
                if adapter is None
                else parse_bridge_adapter_name(adapter)
            )
            self._client = create_com_client(name)

    @property
    def adapter_id(self) -> str:
        return self._client.adapter_id

    def call(
        self,
        operation: str,
        *,
        _timeout_seconds: float | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        effective_timeout = float(self.timeout_seconds)
        if _timeout_seconds is not None:
            if _timeout_seconds <= 0:
                raise OneNoteBridgeError(
                    "OneNote COM operation timeout must be positive.",
                    operation=operation,
                    reconciliation="not_applied",
                    delivery_state=DELIVERY_NOT_SUBMITTED,
                )
            effective_timeout = min(effective_timeout, float(_timeout_seconds))
        started = time.perf_counter()
        succeeded = False
        failure: OneNoteError | None = None
        delivery_state = DELIVERY_NOT_SUBMITTED
        try:
            response = self._client.execute(
                operation,
                params,
                timeout_seconds=effective_timeout,
            )
            delivery_state = DELIVERY_RESPONDED
            if response.get("ok") is not True:
                err = response.get("error") or {}
                failure = bridge_error(
                    err.get("message") or "OneNote COM operation failed.",
                    operation=operation,
                    hresult=err.get("hresult"),
                    category=err.get("category"),
                    wrapper_hresult=err.get("wrapper_hresult"),
                    exception_depth=err.get("exception_depth"),
                    leaf_exception_type=err.get("leaf_exception_type"),
                    delivery_state=DELIVERY_RESPONDED,
                )
                raise failure
            data = response.get("data")
            succeeded = True
            return data if isinstance(data, dict) else {"value": data}
        except OneNoteError as exc:
            failure = exc
            if getattr(exc, "delivery_state", None):
                delivery_state = exc.delivery_state
            raise
        except ComClientError as exc:
            delivery_state = exc.delivery_state
            failure = bridge_error(
                str(exc) or "OneNote COM operation failed.",
                operation=operation,
                timed_out=exc.timed_out,
                delivery_state=exc.delivery_state,
            )
            raise failure from exc
        finally:
            self._append_audit(
                operation,
                succeeded=succeeded,
                elapsed_seconds=round(time.perf_counter() - started, 6),
                failure=failure,
                delivery_state=delivery_state,
            )

    def close(self) -> None:
        closer = getattr(self._client, "close", None)
        if callable(closer):
            closer()

    def _append_audit(
        self,
        operation: str,
        *,
        succeeded: bool,
        elapsed_seconds: float,
        failure: OneNoteError | None,
        delivery_state: str,
    ) -> None:
        configured = self.audit_path or os.environ.get("LOCAL_ONENOTE_BRIDGE_AUDIT_PATH")
        if not configured:
            return
        try:
            path = Path(configured)
            path.parent.mkdir(parents=True, exist_ok=True)
            record: dict[str, Any] = {
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": elapsed_seconds,
                "ok": succeeded,
                "operation": operation,
                "adapter": self._client.adapter_id,
                "delivery_state": delivery_state,
            }
            generation = getattr(self._client, "generation", None)
            if generation is not None:
                record["client_generation"] = generation
            correlation_id = current_correlation_id()
            if correlation_id is not None:
                record["correlation_id"] = correlation_id
            if failure is not None:
                record.update(
                    {
                        "error_type": type(failure).__name__,
                        "error_code": failure.code,
                        "hresult": failure.hresult,
                        "hresult_signed": failure.hresult_signed,
                        "wrapper_hresult": failure.wrapper_hresult,
                        "wrapper_hresult_signed": failure.wrapper_hresult_signed,
                        "exception_depth": failure.exception_depth,
                        "leaf_exception_type": failure.leaf_exception_type,
                        "backend_category": failure.category,
                        "retryability": failure.retryability,
                    }
                )
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            pass


# Re-export for existing imports of the typed error from this module.
__all__ = ["OneNoteBridge", "OneNoteBridgeError", "POWERSHELL_BRIDGE"]
