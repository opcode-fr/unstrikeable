"""Status comment (one per agent and item, edited in place) and the lease on the current task."""
from __future__ import annotations

import re
import time

from .model import AGENT_MARK

STATUS_RE = re.compile(r"<!-- uns:status agent=(\S+) state=(\w+) since=(\d+) beat=(\d+) -->")
STATES = ("working", "done", "blocked")


def hhmm(ts: int) -> str:
    t = time.localtime(ts)
    same_day = time.strftime("%Y%m%d", t) == time.strftime("%Y%m%d")
    return time.strftime("%H:%M" if same_day else "%d/%m %H:%M", t)


def status_body(agent: str, state: str, since: int, now: int, done: str = "", todo: str = "", note: str = "") -> str:
    head = {
        "working": "🟢 **%s** · working since %s · last activity %s",
        "done": "✅ **%s** · done (started %s) · %s",
        "blocked": "⏸️ **%s** · waiting for a human (since %s) · %s",
    }[state] % (agent, hhmm(since), hhmm(now))
    lines = [head, ""]
    if done:
        lines.append("**Done**: %s" % done)
    if todo:
        lines.append("**Next**: %s" % todo)
    if note:
        lines.append(note)
    lines += ["", AGENT_MARK % agent,
              "<!-- uns:status agent=%s state=%s since=%d beat=%d -->" % (agent, state, since, now)]
    return "\n".join(lines)


def parse_status(body: str, agent: str) -> dict | None:
    m = STATUS_RE.search(body or "")
    if not m or m.group(1) != agent:
        return None
    return {"state": m.group(2), "since": int(m.group(3)), "beat": int(m.group(4))}


def lease_step(cur: dict, st: dict | None, now: int, limits: dict, gone: bool = False) -> str:
    """What to do with the agent's current task -> done | wait | retry | resume | escalate.
    cur: {delivered0, delivered, retries}; st: parsed status comment or None."""
    if gone:
        return "done"
    ack_s = limits.get("ack_min", 15) * 60
    stale_s = limits.get("stale_min", 45) * 60
    if st is None or st["beat"] < cur["delivered0"]:
        st = None                                         # no status, or one from a previous task
    if st is not None and st["state"] in ("done", "blocked"):
        return "done"
    if st is None or st["state"] != "working":
        if now - cur["delivered"] > ack_s:
            return "retry" if cur["retries"] == 0 else "escalate"
        return "wait"
    if now - max(st["beat"], cur["delivered"]) > stale_s:
        return "resume" if cur["retries"] == 0 else "escalate"
    return "wait"
