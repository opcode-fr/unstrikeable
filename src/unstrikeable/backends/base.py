"""Board backend interface. The core only talks to this; GitHub is one implementation."""
from __future__ import annotations

from typing import Protocol, Sequence

from ..config import Department
from ..model import Item


class Board(Protocol):
    def items(self) -> list[Item]:
        """Open items of the board, with state, labels, comments and linked PRs."""
        ...

    def item(self, ref: str) -> Item | None:
        """One item (None if closed or gone)."""
        ...

    def move(self, ref: str, column: str) -> None: ...

    def set_field(self, ref: str, name: str, value: str) -> None:
        """Set a single-select field (Size, Priority…) by option name."""
        ...

    def labels(self, ref: str, add: Sequence[str] = (), remove: Sequence[str] = ()) -> None: ...

    def comment(self, ref: str, body: str) -> None: ...

    def upsert_status(self, ref: str, agent: str, body: str) -> None:
        """Create or edit in place the single status comment of `agent` on the item."""
        ...

    def ensure_layout(self, dept: Department, apply: bool = False, prune: bool = False) -> list[str]:
        """Plan (and with apply, create) the columns and labels the department needs. Deletes columns only with prune."""
        ...
