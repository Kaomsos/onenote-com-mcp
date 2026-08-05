"""Path-friendly command entry point; importing it has no OneNote side effects."""

from __future__ import annotations

from pathlib import Path
import sys


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.manual_isolated.runner import main


if __name__ == "__main__":
    raise SystemExit(main())
