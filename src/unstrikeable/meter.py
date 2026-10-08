"""Usage meters: estimated cost of an agent over N days, read from the runtime that executes it."""
from __future__ import annotations

import re
import subprocess
from typing import Callable

COST_RE = re.compile(r"Estimated:\s*~?\$([\d,]+(?:\.\d+)?)")


def hermes_cost(text: str) -> float | None:
    m = COST_RE.search(text or "")
    return float(m.group(1).replace(",", "")) if m else None


def _run(args: list[str]) -> str:
    p = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:300])
    return p.stdout


def make_meter(cfg: dict | None, run: Callable[[list[str]], str] = _run) -> Callable[[int], float | None] | None:
    """local.yml `agents.<a>.meter`: {type: hermes, profile: <profile>}. Unknown cost never crashes a poll."""
    if not cfg:
        return None
    if cfg.get("type") != "hermes":
        raise ValueError("unknown meter type %r" % cfg.get("type"))

    def meter(days: int) -> float | None:
        try:
            return hermes_cost(run(["hermes", "-p", cfg["profile"], "insights", "--days", str(days)]))
        except Exception:
            return None
    return meter
