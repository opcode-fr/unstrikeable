"""Human digest of an instance's agents, for a Slack cron. Pure: states in, text out."""
from __future__ import annotations

from .status import hhmm


def digest(states: dict[str, dict], cursor: int, today: str, alerts_only: bool,
           instance: str = "?") -> tuple[str, int]:
    """states: agent -> poll state. Returns (text, new cursor). Alerts mode is silent when nothing is new."""
    new = sorted(((a, al) for a, s in states.items() for al in s.get("alerts") or [] if al["ts"] > cursor),
                 key=lambda x: x[1]["ts"])
    new_cursor = max([cursor] + [al["ts"] for _, al in new])
    if alerts_only:
        return "\n".join("⚠️ *%s* · %s · %s" % (a, al["ref"], al["msg"]) for a, al in new), new_cursor
    lines = ["*unstrikeable · %s*" % instance]
    for name in sorted(states):
        s = states[name]
        cur = s.get("current")
        count = s.get("day_count", 0) if s.get("day") == today else 0
        what = "%s `%s` since %s" % (cur["ref"], cur["trigger"], hhmm(cur["delivered0"])) if cur else "free"
        lines.append("• *%s*: %s · %d event(s) today" % (name, what, count))
    if new:
        lines.append("Alerts since the last digest:")
        lines += ["  ⚠️ %s · %s · %s" % (a, al["ref"], al["msg"]) for a, al in new]
    return "\n".join(lines), new_cursor
