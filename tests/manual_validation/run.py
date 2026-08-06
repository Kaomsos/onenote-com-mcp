"""HUMAN-GATED scenario entry point; importing it never touches OneNote."""

from __future__ import annotations

from pathlib import Path
import sys


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from manual_validation.runner import main
else:
    from .runner import main


if __name__ == "__main__":
    raise SystemExit(main())
