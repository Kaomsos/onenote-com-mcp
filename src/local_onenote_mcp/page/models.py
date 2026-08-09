"""Internal models used while converting formatted Page content."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TextBlock:
    html: str


@dataclass
class TableCell:
    html: str
    header: bool = False


@dataclass
class TableBlock:
    rows: list[list[TableCell]]


@dataclass
class NoteTag:
    kind: str
    completed: bool = False


@dataclass
class ListItem:
    html: str
    tag: NoteTag | None = None


@dataclass
class ListBlock:
    ordered: bool
    items: list[ListItem]


ContentBlock = TextBlock | TableBlock | ListBlock
