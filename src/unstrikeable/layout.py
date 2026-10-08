"""What a department needs on its board: columns in flow order, and labels in each repo."""
from __future__ import annotations

from .config import Department

SYSTEM_LABELS = {
    "spec:question": ("fbca04", "The spec waits for a human answer"),
    "needs:human": ("d93f0b", "Blocked: a human must step in"),
    "agent:pause": ("b60205", "Kill switch: no agent touches this item"),
    "agent:lost": ("5c5c5c", "Agent went silent: check it, remove the label to resume"),
}
ROLE_COLOR = "1d76db"
EXTRA_COLOR = "c5def5"


def expected_labels(dept: Department) -> dict[str, tuple[str, str]]:
    want: dict[str, tuple[str, str]] = {}
    for rname, role in dept.flow.roles.items():
        for agent in dept.staff_with(rname):
            label = role.named_label(agent)
            if label:
                want[label] = (ROLE_COLOR, "%s assigned as %s" % (agent, rname))
        for pool in role.pool_labels():
            want[pool] = (ROLE_COLOR, "Any idle %s may take it" % rname)
    want.update(SYSTEM_LABELS)
    for extra in dept.flow.extra_labels:
        want.setdefault(extra, (EXTRA_COLOR, ""))
    return want


def plan_columns(have: list[dict], want: list[str]) -> tuple[list[dict] | None, list[str]]:
    """New Status options (None = nothing to do) and human-readable actions.
    Existing option ids are kept: items on those columns keep their status."""
    by_name = {o["name"].lower(): o for o in have}
    wanted = {w.lower() for w in want}
    actions = ["+ column %r" % w for w in want if w.lower() not in by_name]
    extra = [o for o in have if o["name"].lower() not in wanted]
    actions += ["! column %r not in the flow (kept)" % o["name"] for o in extra]
    present = [o["name"].lower() for o in have if o["name"].lower() in wanted]
    if present != [w.lower() for w in want if w.lower() in by_name]:
        actions.append("~ reorder columns")
    if not [a for a in actions if not a.startswith("!")]:
        return None, actions
    opts = []
    for w in want:
        o = by_name.get(w.lower())
        opts.append({"id": o["id"], "name": o["name"], "color": o["color"], "description": o.get("description") or ""}
                    if o else {"name": w, "color": "GRAY", "description": ""})
    opts += [{"id": o["id"], "name": o["name"], "color": o["color"], "description": o.get("description") or ""}
             for o in extra]
    return opts, actions
