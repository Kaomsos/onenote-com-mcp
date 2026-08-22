"""COM client adapters. This module does not import settings, services, or runtime."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .powershell_host import (
    FRAME_PREFIX_TEXT,
    MAX_DECODED_FRAME_BYTES,
    MAX_ENCODED_FRAME_BYTES,
    POWERSHELL_ONE_SHOT_SCRIPT,
    POWERSHELL_PERSISTENT_HOST_SCRIPT,
    assemble_persistent_host_script,
    encode_powershell_command,
)


ADAPTER_PERSISTENT_POWERSHELL = "persistent_powershell"
ADAPTER_ONE_SHOT_POWERSHELL = "one_shot_powershell"

DELIVERY_NOT_SUBMITTED = "not_submitted"
DELIVERY_POSSIBLY_DISPATCHED = "possibly_dispatched"
DELIVERY_RESPONDED = "responded"

PROTOCOL_VERSION = 1
FRAME_PREFIX = FRAME_PREFIX_TEXT.encode("ascii")
HANDSHAKE_GENERATION = 0
HANDSHAKE_SEQUENCE = 0
RESPONSE_ERROR_KEYS = (
    "message",
    "hresult",
    "wrapper_hresult",
    "exception_depth",
    "leaf_exception_type",
    "category",
)
MAX_COMMAND_LINE_CHARS = 32000
DEFAULT_CLOSE_WAIT_SECONDS = 5.0
STATE_NEW = "NEW"
STATE_STARTING = "STARTING"
STATE_READY = "READY"
STATE_BROKEN = "BROKEN"
STATE_CLOSING = "CLOSING"
STATE_CLOSED = "CLOSED"

KIND_REQUEST = "request"
KIND_REFRESH_COM = "refresh_com"
KIND_SHUTDOWN = "shutdown"

REFRESH_REFRESHED = "refreshed"
REFRESH_NOT_NEEDED = "not_needed"
REFRESH_REJECTED_CLOSED = "rejected_closed"
REFRESH_NOT_ATTEMPTED = "not_attempted"
REFRESH_HOST_DISCARDED = "host_discarded"
REFRESH_HOST_DISCARD_UNCONFIRMED = "host_discard_unconfirmed"

CLEANUP_OWNER_REFRESH = "refresh"
CLEANUP_OWNER_CLOSE = "close"
CLEANUP_OWNER_POISON = "poison"

NOT_ATTEMPTED_DISPATCH_LOCK_TIMEOUT = "dispatch_lock_timeout"
NOT_ATTEMPTED_PRE_SUBMIT_FAILURE = "pre_submit_failure"
NOT_ATTEMPTED_HOST_TRANSITION = "host_transition"


class ComClientError(Exception):
    """Transport-level failure with a required delivery state."""

    def __init__(
        self,
        message: str,
        *,
        delivery_state: str,
        operation: str,
        timed_out: bool = False,
        generation: int | None = None,
    ) -> None:
        super().__init__(message)
        self.delivery_state = delivery_state
        self.operation = operation
        self.timed_out = timed_out
        self.generation = generation


@dataclass(frozen=True)
class ComRefreshResult:
    """Content-free outcome of an in-host COM epoch refresh attempt."""

    outcome: str
    generation: int | None = None
    com_epoch: int | None = None
    discarded_generation: int | None = None
    reason: str | None = None

    def content_free_projection(self) -> dict[str, Any]:
        if self.outcome == REFRESH_REFRESHED:
            return {
                "outcome": self.outcome,
                "generation": self.generation,
                "com_epoch": self.com_epoch,
            }
        if self.outcome == REFRESH_HOST_DISCARDED:
            return {
                "outcome": self.outcome,
                "discarded_generation": self.discarded_generation,
            }
        if self.outcome == REFRESH_NOT_ATTEMPTED:
            return {"outcome": self.outcome, "reason": self.reason}
        return {"outcome": self.outcome}


class ComClient(Protocol):
    adapter_id: str
    generation: int | None

    def execute(
        self,
        operation: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]: ...

    def refresh_com(self, *, timeout_seconds: float) -> ComRefreshResult: ...

    def close(self) -> None: ...


class _Pending:
    __slots__ = ("generation", "sequence", "event", "response", "error")

    def __init__(self, generation: int, sequence: int) -> None:
        self.generation = generation
        self.sequence = sequence
        self.event = threading.Event()
        self.response: dict[str, Any] | None = None
        self.error: ComClientError | None = None


class _BoundedLineReader:
    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self._fd = raw.fileno()
        self._buf = bytearray()

    def readline(self, max_len: int) -> bytes | None:
        while True:
            index = self._buf.find(b"\n")
            if index >= 0:
                line = bytes(self._buf[:index])
                del self._buf[: index + 1]
                if line.endswith(b"\r"):
                    line = line[:-1]
                if len(line) > max_len:
                    raise ComClientError(
                        "Protocol frame exceeded the encoded size limit.",
                        delivery_state=DELIVERY_POSSIBLY_DISPATCHED,
                        operation="protocol",
                    )
                return line
            if len(self._buf) > max_len:
                raise ComClientError(
                    "Protocol frame exceeded the encoded size limit.",
                    delivery_state=DELIVERY_POSSIBLY_DISPATCHED,
                    operation="protocol",
                )
            try:
                # os.read returns available pipe data on Windows; stream.read(n)
                # can block until n bytes arrive.
                chunk = os.read(self._fd, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                if not self._buf:
                    return None
                raise ComClientError(
                    "Protocol frame was truncated.",
                    delivery_state=DELIVERY_POSSIBLY_DISPATCHED,
                    operation="protocol",
                )
            self._buf.extend(chunk)


def _exact_int(value: Any, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"invalid {name}")
    return value


def _exact_str(value: Any, name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"invalid {name}")
    return value


def _exact_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"invalid {name}")
    return value


def validate_protocol_envelope(payload: dict[str, Any]) -> tuple[int, int, int, str]:
    try:
        version = _exact_int(payload["protocol_version"], "protocol_version")
        generation = _exact_int(payload["generation"], "generation")
        sequence = _exact_int(payload["sequence"], "sequence")
        kind = _exact_str(payload["kind"], "kind")
    except KeyError as exc:
        raise ValueError("incomplete frame") from exc
    if version != PROTOCOL_VERSION:
        raise ValueError("unsupported protocol")
    return version, generation, sequence, kind


def validate_handshake_frame(payload: dict[str, Any], *, kind: str) -> None:
    _version, generation, sequence, actual_kind = validate_protocol_envelope(payload)
    if actual_kind != kind:
        raise ValueError("invalid handshake kind")
    if generation != HANDSHAKE_GENERATION or sequence != HANDSHAKE_SEQUENCE:
        raise ValueError("invalid handshake generation")


def validate_response_payload(
    payload: dict[str, Any],
    *,
    generation: int,
    sequence: int,
) -> dict[str, Any]:
    _version, frame_generation, frame_sequence, kind = validate_protocol_envelope(payload)
    if kind != "response":
        raise ValueError("invalid response kind")
    if frame_generation != generation or frame_sequence != sequence:
        raise ValueError("mismatched generation/sequence")
    try:
        ok = _exact_bool(payload["ok"], "ok")
        data = payload["data"]
        error = payload["error"]
    except KeyError as exc:
        raise ValueError("incomplete response") from exc
    if data is not None and not isinstance(data, dict):
        raise ValueError("invalid data")
    if ok:
        if error is not None:
            raise ValueError("invalid error")
    else:
        if not isinstance(error, dict):
            raise ValueError("invalid error")
        for key in RESPONSE_ERROR_KEYS:
            if key not in error:
                raise ValueError("incomplete error")
        if type(error["message"]) is not str:
            raise ValueError("invalid error")
        if type(error["hresult"]) is not int:
            raise ValueError("invalid error")
        if type(error["wrapper_hresult"]) is not int:
            raise ValueError("invalid error")
        if type(error["exception_depth"]) is not int:
            raise ValueError("invalid error")
        if type(error["leaf_exception_type"]) is not str:
            raise ValueError("invalid error")
        if type(error["category"]) is not str:
            raise ValueError("invalid error")
    return {"ok": ok, "data": data, "error": error}


def validate_success_epoch(data: Any, *, expected: int) -> int:
    """Require a successful refresh payload to carry the next COM epoch."""

    if not isinstance(data, dict):
        raise ValueError("invalid refresh data")
    epoch = data.get("com_epoch")
    if type(epoch) is not int:
        raise ValueError("invalid com_epoch")
    if epoch != expected:
        raise ValueError("unexpected com_epoch")
    return epoch


def encode_protocol_frame(payload: dict[str, Any], *, max_decoded: int, max_encoded: int) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > max_decoded:
        raise ValueError("decoded frame exceeds limit")
    encoded = base64.b64encode(raw)
    if len(encoded) > max_encoded:
        raise ValueError("encoded frame exceeds limit")
    return FRAME_PREFIX + encoded + b"\n"


def decode_protocol_frame(
    line: bytes, *, max_decoded: int, max_encoded: int
) -> dict[str, Any]:
    if len(line) > max_encoded + len(FRAME_PREFIX):
        raise ValueError("encoded frame exceeds limit")
    if not line.startswith(FRAME_PREFIX):
        raise ValueError("non-protocol frame")
    try:
        raw = base64.b64decode(line[len(FRAME_PREFIX) :], validate=True)
    except (ValueError, OSError) as exc:
        raise ValueError("invalid frame encoding") from exc
    if len(raw) > max_decoded:
        raise ValueError("decoded frame exceeds limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid frame json") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid frame object")
    validate_protocol_envelope(payload)
    return payload


class OneShotPowerShellClient:
    """Per-call ``powershell.exe`` adapter with explicit delivery states."""

    adapter_id = ADAPTER_ONE_SHOT_POWERSHELL

    def __init__(self) -> None:
        self.generation: int | None = None

    def execute(
        self,
        operation: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        request = {"operation": operation, "params": params}
        try:
            req_path = self._write_temp_json(request)
            resp_path = self._reserve_temp_path()
        except OSError as exc:
            raise ComClientError(
                "OneNote COM operation failed.",
                delivery_state=DELIVERY_NOT_SUBMITTED,
                operation=operation,
            ) from exc
        env = os.environ.copy()
        env["LOCAL_ONENOTE_MCP_REQUEST"] = str(req_path)
        env["LOCAL_ONENOTE_MCP_RESPONSE"] = str(resp_path)
        started = False
        try:
            try:
                completed = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "-"],
                    input=POWERSHELL_ONE_SHOT_SCRIPT,
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                    env=env,
                )
            except FileNotFoundError as exc:
                raise ComClientError(
                    "OneNote COM operation failed.",
                    delivery_state=DELIVERY_NOT_SUBMITTED,
                    operation=operation,
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise ComClientError(
                    f"OneNote COM operation timed out after {timeout_seconds:g} seconds.",
                    delivery_state=DELIVERY_POSSIBLY_DISPATCHED,
                    operation=operation,
                    timed_out=True,
                ) from exc
            except OSError as exc:
                raise ComClientError(
                    "OneNote COM operation failed.",
                    delivery_state=DELIVERY_NOT_SUBMITTED if not started else DELIVERY_POSSIBLY_DISPATCHED,
                    operation=operation,
                ) from exc
            started = True
            return self._read_structured_response(resp_path, completed, operation)
        finally:
            self._remove_quietly(req_path)
            self._remove_quietly(resp_path)

    def refresh_com(self, *, timeout_seconds: float) -> ComRefreshResult:
        return ComRefreshResult(outcome=REFRESH_NOT_NEEDED)

    def close(self) -> None:
        return None

    def _read_structured_response(
        self,
        resp_path: Path,
        completed: subprocess.CompletedProcess[str],
        operation: str,
    ) -> dict[str, Any]:
        if resp_path.exists():
            try:
                response = json.loads(resp_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ComClientError(
                    "OneNote COM operation failed.",
                    delivery_state=DELIVERY_POSSIBLY_DISPATCHED,
                    operation=operation,
                ) from exc
            if isinstance(response, dict) and "ok" in response:
                return response
        if completed.returncode != 0:
            raise ComClientError(
                "OneNote COM operation failed.",
                delivery_state=DELIVERY_POSSIBLY_DISPATCHED,
                operation=operation,
            )
        raise ComClientError(
            "PowerShell bridge did not write a response.",
            delivery_state=DELIVERY_POSSIBLY_DISPATCHED,
            operation=operation,
        )

    @staticmethod
    def _write_temp_json(payload: dict[str, Any]) -> Path:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="local-onenote-mcp-",
            suffix=".json",
            delete=False,
        )
        with handle:
            json.dump(payload, handle, ensure_ascii=False)
        return Path(handle.name)

    @staticmethod
    def _reserve_temp_path() -> Path:
        handle = tempfile.NamedTemporaryFile(
            prefix="local-onenote-mcp-",
            suffix=".response.json",
            delete=False,
        )
        path = Path(handle.name)
        handle.close()
        path.unlink(missing_ok=True)
        return path

    @staticmethod
    def _remove_quietly(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


class PersistentPowerShellClient:
    """Single STA PowerShell host owning one ``OneNote.Application`` client."""

    adapter_id = ADAPTER_PERSISTENT_POWERSHELL

    def __init__(
        self,
        *,
        host_command: list[str] | None = None,
        host_script: str | None = None,
        close_wait_seconds: float = DEFAULT_CLOSE_WAIT_SECONDS,
        max_encoded_frame_bytes: int = MAX_ENCODED_FRAME_BYTES,
        max_decoded_frame_bytes: int = MAX_DECODED_FRAME_BYTES,
        admission_hook: Any | None = None,
    ) -> None:
        """Create a persistent STA host client.

        ``host_command``, ``host_script``, non-default ``max_*_frame_bytes``,
        and ``admission_hook`` are test injection. Production uses the module
        defaults so Python and the host script share one encoded/decoded frame
        budget.
        """

        self.generation: int | None = None
        self._host_command = host_command
        self._close_wait_seconds = float(close_wait_seconds)
        self._max_encoded = int(max_encoded_frame_bytes)
        self._max_decoded = int(max_decoded_frame_bytes)
        if host_script is not None:
            self._host_script = host_script
        elif (
            self._max_decoded == MAX_DECODED_FRAME_BYTES
            and self._max_encoded == MAX_ENCODED_FRAME_BYTES
        ):
            self._host_script = POWERSHELL_PERSISTENT_HOST_SCRIPT
        else:
            self._host_script = assemble_persistent_host_script(
                fake_client=False,
                max_decoded_frame_bytes=self._max_decoded,
                max_encoded_frame_bytes=self._max_encoded,
            )
        self._state = STATE_NEW
        self._state_lock = threading.Lock()
        self._dispatch_lock = threading.Lock()
        self._cleanup_lock = threading.RLock()
        self._generation = 0
        self._sequence = 0
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._reader_io: _BoundedLineReader | None = None
        self._ready = threading.Event()
        self._ready_error: BaseException | None = None
        self._pending: _Pending | None = None
        self._closed = threading.Event()
        self._com_epoch: int | None = None
        self._admission_hook = admission_hook
        self._commit_refresh_hook = None
        self._broken_submitted_hook = None
        self._cleanup_hook = None
        self._cleanup_owner: str | None = None
        self._cleanup_done = threading.Event()
        self._cleanup_done.set()

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    @property
    def com_epoch(self) -> int | None:
        with self._state_lock:
            if self._state != STATE_READY:
                return None
            return self._com_epoch

    def execute(
        self,
        operation: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + float(timeout_seconds)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._dispatch_lock.acquire(timeout=max(0.0, remaining)):
            raise ComClientError(
                "OneNote COM operation failed.",
                delivery_state=DELIVERY_NOT_SUBMITTED,
                operation=operation,
            )
        write_started = False
        try:
            with self._state_lock:
                if self._state in {STATE_CLOSING, STATE_CLOSED}:
                    raise ComClientError(
                        "OneNote COM operation failed.",
                        delivery_state=DELIVERY_NOT_SUBMITTED,
                        operation=operation,
                    )
            self._ensure_ready(operation, deadline)
            try:
                frame = encode_protocol_frame(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "generation": self._generation,
                        "sequence": self._sequence + 1,
                        "kind": KIND_REQUEST,
                        "operation": operation,
                        "params": params,
                    },
                    max_decoded=self._max_decoded,
                    max_encoded=self._max_encoded,
                )
            except ValueError as exc:
                raise ComClientError(
                    "OneNote COM operation failed.",
                    delivery_state=DELIVERY_NOT_SUBMITTED,
                    operation=operation,
                    generation=self._generation,
                ) from exc
            pending = self._admit_pending()
            if pending is None:
                raise ComClientError(
                    "OneNote COM operation failed.",
                    delivery_state=DELIVERY_NOT_SUBMITTED,
                    operation=operation,
                    generation=self._generation,
                )
            write_started = True
            self._write_and_await_pending(
                pending,
                frame,
                operation=operation,
                timeout_seconds=timeout_seconds,
                deadline=deadline,
            )
            if pending.error is not None:
                self._converge_broken_host()
                raise pending.error
            assert pending.response is not None
            return pending.response
        except ComClientError:
            raise
        except Exception as exc:
            delivery = (
                DELIVERY_POSSIBLY_DISPATCHED if write_started else DELIVERY_NOT_SUBMITTED
            )
            raise ComClientError(
                "OneNote COM operation failed.",
                delivery_state=delivery,
                operation=operation,
                generation=self._generation or None,
            ) from exc
        finally:
            with self._state_lock:
                if self._pending is not None and self._pending.event.is_set():
                    self._pending = None
            self._dispatch_lock.release()

    def refresh_com(self, *, timeout_seconds: float) -> ComRefreshResult:
        deadline = time.monotonic() + float(timeout_seconds)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._dispatch_lock.acquire(timeout=max(0.0, remaining)):
            return ComRefreshResult(
                outcome=REFRESH_NOT_ATTEMPTED,
                reason=NOT_ATTEMPTED_DISPATCH_LOCK_TIMEOUT,
            )
        published = False
        pending: _Pending | None = None
        try:
            with self._state_lock:
                if self._state in {STATE_CLOSING, STATE_CLOSED}:
                    return ComRefreshResult(outcome=REFRESH_REJECTED_CLOSED)
                if self._state in {STATE_NEW, STATE_BROKEN}:
                    return ComRefreshResult(outcome=REFRESH_NOT_NEEDED)
            if self._admission_hook is not None:
                self._admission_hook()
            try:
                frame = encode_protocol_frame(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "generation": self._generation,
                        "sequence": self._sequence + 1,
                        "kind": KIND_REFRESH_COM,
                    },
                    max_decoded=self._max_decoded,
                    max_encoded=self._max_encoded,
                )
            except ValueError:
                return ComRefreshResult(
                    outcome=REFRESH_NOT_ATTEMPTED,
                    reason=NOT_ATTEMPTED_PRE_SUBMIT_FAILURE,
                )
            pending, expected_epoch = self._admit_refresh_pending()
            if pending is None:
                return self._refresh_second_admission_outcome()
            published = True
            self._write_and_await_pending(
                pending,
                frame,
                operation=KIND_REFRESH_COM,
                timeout_seconds=timeout_seconds,
                deadline=deadline,
            )
            return self._finalize_refresh(pending, expected_epoch)
        except Exception:
            if published and pending is not None:
                return self._finalize_refresh(pending, None)
            return ComRefreshResult(
                outcome=REFRESH_NOT_ATTEMPTED,
                reason=NOT_ATTEMPTED_PRE_SUBMIT_FAILURE,
            )
        finally:
            with self._state_lock:
                if self._pending is not None and self._pending.event.is_set():
                    self._pending = None
            self._dispatch_lock.release()

    def close(self) -> None:
        while True:
            wait_foreign = False
            with self._state_lock:
                if self._state == STATE_CLOSED:
                    return
                owner = self._cleanup_owner
                if self._state == STATE_BROKEN and owner not in {
                    None,
                    CLEANUP_OWNER_CLOSE,
                }:
                    wait_foreign = True
                else:
                    retry = self._state == STATE_CLOSING
                    self._state = STATE_CLOSING
                    self._claim_cleanup_owner_locked(CLEANUP_OWNER_CLOSE)
                    in_flight = self._pending
                    break
            if wait_foreign:
                self._cleanup_done.wait()
        if in_flight is not None:
            self._fail_pending(
                in_flight,
                ComClientError(
                    "OneNote COM bridge is closing.",
                    delivery_state=DELIVERY_POSSIBLY_DISPATCHED,
                    operation="close",
                    generation=self._generation,
                ),
            )
        confirmed = (
            self._reap(kill=True)
            if (in_flight is not None or retry)
            else self._shutdown_idle()
        )
        with self._state_lock:
            self._pending = None
            if not confirmed:
                return
            self._forget_host_handles()
            self._state = STATE_CLOSED
            self._finish_cleanup_owner_locked(CLEANUP_OWNER_CLOSE)
        self._closed.set()

    def _ensure_ready(self, operation: str, deadline: float) -> None:
        with self._state_lock:
            if self._state == STATE_READY and self._process is not None:
                return
            broken = self._state == STATE_BROKEN
            if not broken and self._state != STATE_NEW:
                raise ComClientError(
                    "OneNote COM operation failed.",
                    delivery_state=DELIVERY_NOT_SUBMITTED,
                    operation=operation,
                    generation=self._generation or None,
                )
        if broken:
            confirmed = self._reap(kill=True)
            with self._state_lock:
                if self._state in {STATE_CLOSING, STATE_CLOSED}:
                    raise ComClientError(
                        "OneNote COM operation failed.",
                        delivery_state=DELIVERY_NOT_SUBMITTED,
                        operation=operation,
                        generation=self._generation or None,
                    )
                if self._state == STATE_BROKEN:
                    if not confirmed:
                        raise ComClientError(
                            "OneNote COM operation failed.",
                            delivery_state=DELIVERY_NOT_SUBMITTED,
                            operation=operation,
                            generation=self._generation or None,
                        )
                    self._forget_host_handles()
                    self._state = STATE_NEW
        with self._state_lock:
            if self._state != STATE_NEW:
                raise ComClientError(
                    "OneNote COM operation failed.",
                    delivery_state=DELIVERY_NOT_SUBMITTED,
                    operation=operation,
                    generation=self._generation or None,
                )
            self._state = STATE_STARTING
            self._generation += 1
            self.generation = self._generation
            self._sequence = 0
            self._com_epoch = None
            self._ready.clear()
            self._ready_error = None
        try:
            command = self._host_command or self._encoded_host_command()
            if sum(len(part) + 1 for part in command) > MAX_COMMAND_LINE_CHARS:
                raise ComClientError(
                    "OneNote COM operation failed.",
                    delivery_state=DELIVERY_NOT_SUBMITTED,
                    operation=operation,
                    generation=self._generation,
                )
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except ComClientError:
            with self._state_lock:
                self._state = STATE_NEW
            raise
        except OSError as exc:
            with self._state_lock:
                self._state = STATE_NEW
            raise ComClientError(
                "OneNote COM operation failed.",
                delivery_state=DELIVERY_NOT_SUBMITTED,
                operation=operation,
                generation=self._generation,
            ) from exc
        assert process.stdout is not None
        self._process = process
        self._reader_io = _BoundedLineReader(process.stdout)
        thread = threading.Thread(
            target=self._reader_main,
            name=f"onenote-com-host-reader-{self._generation}",
            daemon=True,
        )
        self._reader = thread
        thread.start()
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._ready.wait(timeout=remaining):
            self._poison(kill=True)
            raise ComClientError(
                "OneNote COM operation failed.",
                delivery_state=DELIVERY_NOT_SUBMITTED,
                operation=operation,
                generation=self._generation,
            )
        if self._ready_error is not None:
            self._poison(kill=True)
            raise ComClientError(
                "OneNote COM operation failed.",
                delivery_state=DELIVERY_NOT_SUBMITTED,
                operation=operation,
                generation=self._generation,
            )
        with self._state_lock:
            if self._state == STATE_STARTING:
                self._state = STATE_READY
                self._com_epoch = 1

    def _encoded_host_command(self) -> list[str]:
        encoded = encode_powershell_command(self._host_script)
        return [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Sta",
            "-EncodedCommand",
            encoded,
        ]

    def _reader_main(self) -> None:
        generation = self._generation
        first = True
        try:
            while True:
                if self._reader_io is None:
                    break
                try:
                    line = self._reader_io.readline(self._max_encoded + len(FRAME_PREFIX))
                except ComClientError as exc:
                    self._on_protocol_failure(exc, first=first, generation=generation)
                    return
                if line is None:
                    self._on_protocol_failure(
                        ComClientError(
                            "OneNote COM host exited.",
                            delivery_state=(
                                DELIVERY_NOT_SUBMITTED
                                if first
                                else DELIVERY_POSSIBLY_DISPATCHED
                            ),
                            operation="protocol",
                            generation=self._generation,
                        ),
                        first=first,
                        generation=generation,
                    )
                    return
                try:
                    should_continue = self._accept_host_frame(line, first=first)
                except Exception as exc:
                    error = exc if isinstance(exc, ComClientError) else ComClientError(
                        "OneNote COM host protocol violation.",
                        delivery_state=(
                            DELIVERY_NOT_SUBMITTED
                            if first
                            else DELIVERY_POSSIBLY_DISPATCHED
                        ),
                        operation="protocol",
                        generation=self._generation,
                    )
                    self._on_protocol_failure(
                        error, first=first, generation=generation
                    )
                    return
                if first:
                    first = False
                if not should_continue:
                    return
        finally:
            self._mark_broken(generation=generation)

    def _accept_host_frame(self, line: bytes, *, first: bool) -> bool:
        frame = decode_protocol_frame(
            line,
            max_decoded=self._max_decoded,
            max_encoded=self._max_encoded,
        )
        kind = _exact_str(frame["kind"], "kind")
        if first:
            if kind == "ready":
                validate_handshake_frame(frame, kind="ready")
                self._ready.set()
                return True
            if kind == "fatal":
                validate_handshake_frame(frame, kind="fatal")
                self._ready_error = ComClientError(
                    "OneNote COM host failed to start.",
                    delivery_state=DELIVERY_NOT_SUBMITTED,
                    operation="initialize",
                    generation=self._generation,
                )
                self._ready.set()
                return False
            raise ValueError("expected ready or fatal")
        if kind != "response":
            raise ValueError("expected response")
        pending = self._pending
        if pending is None:
            raise ValueError("unexpected response")
        pending.response = validate_response_payload(
            frame,
            generation=pending.generation,
            sequence=pending.sequence,
        )
        pending.event.set()
        return True

    def _on_protocol_failure(
        self,
        error: ComClientError,
        *,
        first: bool,
        generation: int,
    ) -> None:
        if first:
            self._ready_error = error
            self._ready.set()
        pending = self._pending
        if pending is not None:
            self._fail_pending(pending, error)
        if not first:
            self._mark_broken(generation=generation)

    def _mark_broken(self, *, generation: int | None = None) -> None:
        with self._state_lock:
            if generation is not None and self._generation != generation:
                return
            if self._state not in {STATE_READY, STATE_STARTING}:
                return
            self._state = STATE_BROKEN

    def _converge_broken_host(self) -> None:
        with self._state_lock:
            if self._state != STATE_BROKEN:
                return
        self._poison(kill=True)

    def _fail_pending(self, pending: _Pending, error: ComClientError) -> None:
        if pending.error is None and pending.response is None:
            pending.error = error
        pending.event.set()

    def _admit_pending(self) -> _Pending | None:
        with self._state_lock:
            if self._state != STATE_READY:
                return None
            self._sequence += 1
            pending = _Pending(self._generation, self._sequence)
            self._pending = pending
            return pending

    def _admit_refresh_pending(self) -> tuple[_Pending | None, int | None]:
        with self._state_lock:
            if self._state != STATE_READY or self._com_epoch is None:
                return None, None
            self._sequence += 1
            pending = _Pending(self._generation, self._sequence)
            self._pending = pending
            return pending, self._com_epoch + 1

    def _refresh_second_admission_outcome(self) -> ComRefreshResult:
        with self._state_lock:
            if self._state in {STATE_CLOSING, STATE_CLOSED}:
                return ComRefreshResult(outcome=REFRESH_REJECTED_CLOSED)
            if self._state in {STATE_BROKEN, STATE_NEW}:
                return ComRefreshResult(
                    outcome=REFRESH_NOT_ATTEMPTED,
                    reason=NOT_ATTEMPTED_HOST_TRANSITION,
                )
        return ComRefreshResult(
            outcome=REFRESH_NOT_ATTEMPTED,
            reason=NOT_ATTEMPTED_PRE_SUBMIT_FAILURE,
        )

    def _write_and_await_pending(
        self,
        pending: _Pending,
        frame: bytes,
        *,
        operation: str,
        timeout_seconds: float,
        deadline: float,
    ) -> None:
        process = self._process
        if process is None or process.stdin is None:
            self._fail_pending(
                pending,
                ComClientError(
                    "OneNote COM operation failed.",
                    delivery_state=DELIVERY_POSSIBLY_DISPATCHED,
                    operation=operation,
                    generation=self._generation,
                ),
            )
            if operation != KIND_REFRESH_COM:
                self._poison(kill=True)
            return
        try:
            process.stdin.write(frame)
            process.stdin.flush()
        except OSError as exc:
            error = ComClientError(
                "OneNote COM operation failed.",
                delivery_state=DELIVERY_POSSIBLY_DISPATCHED,
                operation=operation,
                generation=self._generation,
            )
            self._fail_pending(pending, error)
            if operation != KIND_REFRESH_COM:
                self._poison(kill=True)
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not pending.event.wait(timeout=remaining):
            error = ComClientError(
                f"OneNote COM operation timed out after {timeout_seconds:g} seconds.",
                delivery_state=DELIVERY_POSSIBLY_DISPATCHED,
                operation=operation,
                timed_out=True,
                generation=self._generation,
            )
            self._fail_pending(pending, error)
            if operation != KIND_REFRESH_COM:
                self._poison(kill=True)

    def _finalize_refresh(
        self,
        pending: _Pending,
        expected_epoch: int | None,
    ) -> ComRefreshResult:
        if pending.error is None and pending.response is not None and expected_epoch is not None:
            if pending.response.get("ok") is True:
                try:
                    epoch = validate_success_epoch(
                        pending.response.get("data"),
                        expected=expected_epoch,
                    )
                except ValueError:
                    return self._commit_refresh_failure(pending)
                with self._state_lock:
                    if self._state == STATE_READY:
                        self._com_epoch = epoch
                        return ComRefreshResult(
                            outcome=REFRESH_REFRESHED,
                            generation=pending.generation,
                            com_epoch=epoch,
                        )
        return self._commit_refresh_failure(pending)

    def _commit_refresh_failure(self, pending: _Pending) -> ComRefreshResult:
        if self._commit_refresh_hook is not None:
            self._commit_refresh_hook()
        with self._state_lock:
            if self._state in {STATE_CLOSING, STATE_CLOSED}:
                return ComRefreshResult(outcome=REFRESH_REJECTED_CLOSED)
            if self._state == STATE_NEW:
                return ComRefreshResult(
                    outcome=REFRESH_HOST_DISCARDED,
                    discarded_generation=pending.generation,
                )
            if not self._claim_cleanup_owner_locked(CLEANUP_OWNER_REFRESH):
                return ComRefreshResult(
                    outcome=REFRESH_NOT_ATTEMPTED,
                    reason=NOT_ATTEMPTED_HOST_TRANSITION,
                )
            self._state = STATE_BROKEN
        if self._broken_submitted_hook is not None:
            self._broken_submitted_hook()
        confirmed = self._reap(kill=True)
        with self._state_lock:
            if confirmed:
                if self._state == STATE_BROKEN:
                    self._forget_host_handles()
                    self._state = STATE_NEW
                self._finish_cleanup_owner_locked(CLEANUP_OWNER_REFRESH)
                return ComRefreshResult(
                    outcome=REFRESH_HOST_DISCARDED,
                    discarded_generation=pending.generation,
                )
            self._finish_cleanup_owner_locked(CLEANUP_OWNER_REFRESH)
            return ComRefreshResult(outcome=REFRESH_HOST_DISCARD_UNCONFIRMED)

    def _forget_host_handles(self) -> None:
        self._process = None
        self._reader = None
        self._reader_io = None
        self._com_epoch = None

    def _claim_cleanup_owner_locked(self, owner: str) -> bool:
        current = self._cleanup_owner
        if current is None:
            self._cleanup_owner = owner
            self._cleanup_done.clear()
            return True
        return current == owner

    def _finish_cleanup_owner_locked(self, owner: str) -> None:
        if self._cleanup_owner == owner:
            self._cleanup_owner = None
            self._cleanup_done.set()

    def _poison(self, *, kill: bool) -> bool:
        with self._state_lock:
            if self._state in {STATE_CLOSING, STATE_CLOSED}:
                return False
            if self._cleanup_owner not in {None, CLEANUP_OWNER_POISON}:
                return False
            self._state = STATE_BROKEN
            self._claim_cleanup_owner_locked(CLEANUP_OWNER_POISON)
        confirmed = self._reap(kill=kill)
        with self._state_lock:
            if self._state == STATE_BROKEN and confirmed:
                self._forget_host_handles()
                self._state = STATE_NEW
            self._finish_cleanup_owner_locked(CLEANUP_OWNER_POISON)
        return confirmed

    def _shutdown_idle(self) -> bool:
        with self._cleanup_lock:
            process = self._process
            if process is None:
                return self._reap(kill=False)
            try:
                if process.stdin is not None:
                    frame = encode_protocol_frame(
                        {
                            "protocol_version": PROTOCOL_VERSION,
                            "generation": self._generation,
                            "sequence": self._sequence + 1,
                            "kind": KIND_SHUTDOWN,
                        },
                        max_decoded=self._max_decoded,
                        max_encoded=self._max_encoded,
                    )
                    process.stdin.write(frame)
                    process.stdin.flush()
                    process.stdin.close()
            except (OSError, ValueError):
                return self._reap(kill=True)
            try:
                process.wait(timeout=self._close_wait_seconds)
            except subprocess.TimeoutExpired:
                return self._reap(kill=True)
            return self._reap(kill=False)

    def _reap(self, *, kill: bool) -> bool:
        if self._reader is threading.current_thread():
            return False
        with self._cleanup_lock:
            if self._cleanup_hook is not None:
                self._cleanup_hook()
            process = self._process
            reader = self._reader
            if process is not None:
                if kill and process.poll() is None:
                    try:
                        process.kill()
                    except (OSError, ValueError):
                        pass
                if process.stdin is not None:
                    try:
                        process.stdin.close()
                    except (OSError, ValueError):
                        pass
                try:
                    process.wait(timeout=self._close_wait_seconds)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                        process.wait(timeout=1)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            if reader is not None and reader.is_alive():
                reader.join(timeout=self._close_wait_seconds)
            if process is not None and process.stdout is not None:
                try:
                    process.stdout.close()
                except (OSError, ValueError):
                    pass
            process_exited = process is None or process.poll() is not None
            reader_done = reader is None or not reader.is_alive()
            confirmed = process_exited and reader_done
            if confirmed:
                self._reader_io = None
            return confirmed


def create_com_client(adapter_id: str, **kwargs: Any) -> ComClient:
    if adapter_id == ADAPTER_PERSISTENT_POWERSHELL:
        return PersistentPowerShellClient(**kwargs)
    if adapter_id == ADAPTER_ONE_SHOT_POWERSHELL:
        return OneShotPowerShellClient()
    raise ValueError(f"Unknown COM client adapter: {adapter_id!r}.")
