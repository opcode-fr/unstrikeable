"""Events an agent should handle, computed from the board. Pure: no I/O."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Department, Role
from .model import BLOCKING, SPEC_QUESTION, Comment, Item

URGENT_STATES = ("doing", "approved")      # a comment here is work in progress, not new work


@dataclass(frozen=True)
class Event:
    key: str                   # dedup key: same key = already delivered
    agent: str
    department: str
    role: str
    trigger: str
    item: Item
    comment: Comment | None = None

    @property
    def priority(self) -> int:
        t = self.trigger
        if t in ("pr_conflict", "ci_failed", "new_comment"):
            return 0
        if t == "human_comment":
            return 0 if self.item.state in URGENT_STATES else 2
        if t == "pr_updated":
            return 1
        if t == "item_new":
            return 2
        return 3                              # assigned: new work last


# ---------------------------------------------------------------- helpers
def _named(role: Role, item: Item) -> list[str]:
    return [a for a in (role.agent_of(l) for l in item.labels) if a]


def _assignees(dept: Department, item: Item, except_role: str) -> set[str]:
    out = set()
    for name, role in dept.flow.roles.items():
        if name != except_role:
            out.update(_named(role, item))
    return out


def busy(agent: str, dept: Department, items: list[Item]) -> bool:
    """The agent already holds an item in progress (named on it, not silenced)."""
    for it in items:
        if it.state != "doing" or BLOCKING & set(it.labels):
            continue
        if any(agent in _named(r, it) for r in dept.flow.roles.values()):
            return True
    return False


def owner(role: Role, dept: Department, item: Item, items: list[Item]) -> str | None:
    """Which staff member holds this role on this item (None = nobody / ambiguous)."""
    named = _named(role, item)
    if len(named) > 1:
        return None                                       # ambiguous: flagged for a human elsewhere
    if named:
        return named[0]
    others = _assignees(dept, item, role.name)            # nobody reviews their own work
    eligible = [a for a in dept.staff_with(role.name) if a not in others]
    if any(p in item.labels for p in role.pool_labels()):
        return next((a for a in eligible if not busy(a, dept, items)), None)
    if not role.labelled or role.auto:
        return eligible[0] if eligible else None
    return None


def _conversation(item: Item) -> list[Comment]:
    return [c for c in item.comments if c.trusted and not c.status]


def _last_word(item: Item, me: str, humans_only: bool) -> Comment | None:
    """Last trusted comment if it is worth reacting to: not mine, and from a human if humans_only."""
    conv = _conversation(item)
    if not conv:
        return None
    c = conv[-1]
    if c.agent == me or (humans_only and c.agent is not None):
        return None
    return c


def _spoke(item: Item, me: str) -> bool:
    return any(c.agent == me for c in item.comments)


def _last_id(item: Item) -> int:
    return max((c.id for c in item.comments), default=0)


# ---------------------------------------------------------------- main
def events_for(agent: str, dept: Department, items: list[Item]) -> list[Event]:
    out: list[Event] = []
    roles = dept.staff.get(agent, [])
    for it in items:
        labels = set(it.labels)
        if BLOCKING & labels:
            continue
        state = it.state or "backlog"
        for rname in roles:
            role = dept.flow.roles[rname]
            triggers = role.on.get(state, [])
            if role.human or not triggers:
                continue
            if owner(role, dept, it, items) != agent:
                continue
            if not role.labelled and not it.author_trusted and not _assignees(dept, it, rname):
                continue                                  # outsider item, not vetted by a member

            def ev(trigger, disc, comment=None):
                key = "%s|%s|%s|%s|%s" % (dept.name, it.ref, rname, trigger, disc)
                out.append(Event(key, agent, dept.name, rname, trigger, it, comment))

            waiting = SPEC_QUESTION in labels
            new_fired = False
            for t in triggers:
                if waiting and t != "human_comment":
                    continue
                if t == "item_new" and not _spoke(it, agent):
                    ev(t, 0)
                    new_fired = True
                elif t == "human_comment" and not new_fired:
                    c = _last_word(it, agent, humans_only=True)
                    if c:
                        ev(t, c.id, c)
                elif t == "new_comment":
                    c = _last_word(it, agent, humans_only=False)
                    if c:
                        ev(t, c.id, c)
                elif t == "assigned":
                    if it.blocked_by == 0 and not busy(agent, dept, items):
                        ev(t, _last_id(it))
                elif t == "pr_updated" and it.prs:
                    heads = ",".join(sorted(p.head for p in it.prs))
                    ev(t, "%s|%s" % (heads, _last_id(it)))
                elif t == "pr_conflict":
                    pr = next((p for p in it.prs if p.mergeable == "CONFLICTING"), None)
                    if pr:
                        ev(t, pr.head)
                elif t == "ci_failed":
                    pr = next((p for p in it.prs if p.ci == "FAILURE"), None)
                    if pr:
                        ev(t, pr.head)
    out.sort(key=lambda e: e.priority)
    return out
