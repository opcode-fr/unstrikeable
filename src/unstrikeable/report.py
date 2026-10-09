"""`uns report`: closed tasks aggregated by agent, kind, role… Pure: records in, text out.

A record is one line of state/tasks-<agent>.jsonl, written when the poll sees a task closed (see poll._record).
"""
from __future__ import annotations

from statistics import median

GROUP_KEYS = ("agent", "kind", "role", "trigger", "department", "flow", "outcome")
OUTCOMES = ("done", "blocked", "lost", "gone")


def _num(n: float) -> str:
    for div, unit in ((1e6, "M"), (1e3, "k")):
        if n >= div:
            return "%.1f%s" % (n / div, unit) if n < 100 * div else "%d%s" % (n // div, unit)
    return "%d" % n


def _line(label: str, recs: list[dict]) -> str:
    n = len(recs)
    outcomes = ", ".join("%d %s" % (c, o) for o in OUTCOMES for c in [sum(r.get("outcome") == o for r in recs)] if c)
    parts = ["%s · %d task%s (%s)" % (label, n, "" if n == 1 else "s", outcomes)]
    parts.append("median %d min" % round(median(r.get("wall_s") or 0 for r in recs) / 60))
    known = [r["usage"] for r in recs if r.get("usage")]
    if known:
        cost = "$%.2f total, $%.2f median" % (sum(u["cost"] for u in known), median(u["cost"] for u in known))
        if len(known) < n:
            cost += " (cost known for %d/%d)" % (len(known), n)
        parts.append(cost)
        tok = sum(u["in"] + u["out"] + u["reasoning"] for u in known)
        cache = sum(u["cache_read"] + u["cache_write"] for u in known)
        parts.append("%s tok (+%s cache)" % (_num(tok), _num(cache)))
    else:
        parts.append("cost unknown")
    parts.append("%.2f nudges/task" % (sum(r.get("retries") or 0 for r in recs) / n))
    return " · ".join(parts)


def report(records: list[dict], by: list[str], now: int, days: int) -> str:
    bad = [k for k in by if k not in GROUP_KEYS]
    if bad:
        raise ValueError("unknown group key(s) %s (use %s)" % (", ".join(bad), ", ".join(GROUP_KEYS)))
    recs = [r for r in records if (r.get("end") or 0) >= now - days * 86400]
    head = "*Closed tasks, last %d days, by %s*" % (days, ", ".join(by))
    if not recs:
        return head + "\nnothing closed in this window"
    groups: dict[tuple, list[dict]] = {}
    for r in recs:
        groups.setdefault(tuple(str(r.get(k) or "-") for k in by), []).append(r)

    def spend(item: tuple) -> tuple:
        rs = item[1]
        return (-sum((r.get("usage") or {}).get("cost", 0) for r in rs), -len(rs), item[0])
    lines = [head] + ["• " + _line(" · ".join(k), rs) for k, rs in sorted(groups.items(), key=spend)]
    return "\n".join(lines + [_line("Total:", recs).replace("Total: · ", "Total: ", 1)])
