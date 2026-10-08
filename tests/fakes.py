"""In-memory board for tests."""
from __future__ import annotations

from dataclasses import replace

from unstrikeable.model import Comment, Item
from unstrikeable.status import parse_status


class FakeBoard:
    def __init__(self, items: list[Item], columns: dict[str, str] | None = None):
        self._items = {it.ref: it for it in items}
        self.columns = columns or {}        # column name -> state key, to mimic the board
        self.calls: list[tuple] = []
        self._next = 1000

    def items(self):
        return list(self._items.values())

    def item(self, ref):
        return self._items.get(ref)

    def close(self, ref):
        self._items.pop(ref)

    def set(self, ref, **kw):
        self._items[ref] = replace(self._items[ref], **kw)

    def move(self, ref, column):
        self.calls.append(("move", ref, column))
        self.set(ref, state=self.columns.get(column, column))

    def labels(self, ref, add=(), remove=()):
        self.calls.append(("labels", ref, tuple(add), tuple(remove)))
        it = self._items[ref]
        self.set(ref, labels=[l for l in it.labels if l not in remove] + [l for l in add if l not in it.labels])

    def comment(self, ref, body):
        self.calls.append(("comment", ref, body))
        self._next += 1
        it = self._items[ref]
        self.set(ref, comments=it.comments + [Comment(self._next, "bot", True, body=body)])

    def upsert_status(self, ref, agent, body):
        self.calls.append(("status", ref, agent))
        it = self._items[ref]
        kept = [c for c in it.comments if not (c.status and parse_status(c.body, agent))]
        self._next += 1
        self.set(ref, comments=kept + [Comment(self._next, agent, True, agent=agent, status=True, body=body)])
