"""Fake persistent COM host that speaks the ONB1 frame protocol."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path


PREFIX = "ONB1 "


def write_frame(payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.write(PREFIX + base64.b64encode(raw).decode("ascii") + "\n")
    sys.stdout.flush()


def read_frame() -> dict | None:
    line = sys.stdin.readline()
    if line == "":
        return None
    line = line.rstrip("\r\n")
    if not line.startswith(PREFIX):
        return {"kind": "noise", "raw": line}
    payload = json.loads(base64.b64decode(line[len(PREFIX) :]).decode("utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(2)
    return payload


def ready() -> None:
    write_frame(
        {
            "protocol_version": 1,
            "generation": 0,
            "sequence": 0,
            "kind": "ready",
            "adapter": "persistent_powershell",
            "pid": 0,
            "apartment": "STA",
        }
    )


def respond(request: dict, *, ok: bool = True, data=None, error=None) -> None:
    write_frame(
        {
            "protocol_version": 1,
            "generation": request["generation"],
            "sequence": request["sequence"],
            "kind": "response",
            "ok": ok,
            "data": data if data is not None else {"xml": "<one:Notebooks/>"},
            "error": error,
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="ok")
    parser.add_argument("--ready-delay", type=float, default=0.0)
    parser.add_argument("--once-file")
    args = parser.parse_args()
    if args.ready_delay:
        time.sleep(args.ready_delay)
    if args.mode == "fatal":
        write_frame(
            {
                "protocol_version": 1,
                "generation": 0,
                "sequence": 0,
                "kind": "fatal",
            }
        )
        return 1
    if args.mode == "ready-bad-generation":
        write_frame(
            {
                "protocol_version": 1,
                "generation": 2,
                "sequence": 0,
                "kind": "ready",
                "adapter": "persistent_powershell",
                "pid": 0,
                "apartment": "STA",
            }
        )
        time.sleep(30)
        return 1
    if args.mode == "ready-missing-field":
        write_frame({"protocol_version": 1, "generation": 0, "sequence": 0})
        time.sleep(30)
        return 1
    if args.mode == "ready-string-version":
        write_frame(
            {
                "protocol_version": "1",
                "generation": 0,
                "sequence": 0,
                "kind": "ready",
            }
        )
        time.sleep(30)
        return 1
    ready()
    while True:
        request = read_frame()
        if request is None:
            return 0
        if request.get("kind") == "shutdown":
            return 0
        if request.get("kind") == "noise":
            sys.stdout.write("not-a-frame\n")
            sys.stdout.flush()
            continue
        if args.mode == "noise":
            sys.stdout.write("incidental stdout\n")
            sys.stdout.flush()
            return 1
        if args.mode == "hang":
            time.sleep(30)
            return 0
        if args.mode == "hang-once":
            marker = args.once_file
            if not marker:
                time.sleep(30)
                return 1
            path = Path(marker)
            if not path.exists():
                path.write_text("1", encoding="ascii")
                time.sleep(30)
                return 0
            respond(request)
            continue
        if args.mode == "response-missing-data":
            write_frame(
                {
                    "protocol_version": 1,
                    "generation": request["generation"],
                    "sequence": request["sequence"],
                    "kind": "response",
                    "ok": True,
                    "error": None,
                }
            )
            continue
        if args.mode == "response-ok-string":
            write_frame(
                {
                    "protocol_version": 1,
                    "generation": request["generation"],
                    "sequence": request["sequence"],
                    "kind": "response",
                    "ok": "false",
                    "data": None,
                    "error": None,
                }
            )
            continue
        if args.mode == "response-generation-string":
            write_frame(
                {
                    "protocol_version": 1,
                    "generation": str(request["generation"]),
                    "sequence": request["sequence"],
                    "kind": "response",
                    "ok": True,
                    "data": {"xml": "<one:Notebooks/>"},
                    "error": None,
                }
            )
            continue
        if args.mode == "crash":
            return 3
        if args.mode == "mismatch":
            write_frame(
                {
                    "protocol_version": 1,
                    "generation": request["generation"],
                    "sequence": int(request["sequence"]) + 99,
                    "kind": "response",
                    "ok": True,
                    "data": {},
                    "error": None,
                }
            )
            continue
        if args.mode == "no-newline":
            sys.stdout.write("ONB1 " + ("A" * 400))
            sys.stdout.flush()
            time.sleep(30)
            return 1
        if args.mode == "oversized":
            huge = "x" * 2000
            write_frame(
                {
                    "protocol_version": 1,
                    "generation": request["generation"],
                    "sequence": request["sequence"],
                    "kind": "response",
                    "ok": True,
                    "data": {"xml": huge},
                    "error": None,
                }
            )
            continue
        if args.mode == "ok-false":
            respond(
                request,
                ok=False,
                data=None,
                error={
                    "message": "secret",
                    "hresult": -2147213299,
                    "wrapper_hresult": -2147213299,
                    "exception_depth": 0,
                    "leaf_exception_type": "System.Runtime.InteropServices.COMException",
                    "category": "OperationStopped",
                },
            )
            continue
        if args.mode == "non-ascii":
            respond(
                request,
                data={"xml": "<one:Notebooks>测</one:Notebooks>"},
            )
            continue
        if request.get("operation") == "create_new_page":
            respond(request, data={"page_id": "page-1"})
            continue
        respond(request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
