"""Usage meters, read from the runtime that executes an agent.

- cost meter: estimated cost over N days (cost quotas), from `hermes insights`.
- usage reader: cumulative counters of the agent's board work (tokens, cost), read at the start and the end of a
  task; the difference is what the task spent. Hermes delivers board events to the profile's canonical "Bot Chat"
  session, so only that session and its children (compression rotations, subagents) are counted: Slack chats of
  the same profile are left out.
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Callable

COST_RE = re.compile(r"Estimated:\s*~?\$([\d,]+(?:\.\d+)?)")
BOT_CHAT_TITLE = "Bot Chat"                 # Hermes CANONICAL_BOT_CHAT_TITLE (hermes_state.py)
USAGE_KEYS = ("in", "out", "cache_read", "cache_write", "reasoning", "cost")

Usage = Callable[[], "dict | None"]

_BOT_CHAT_SQL = """
WITH RECURSIVE lineage(id) AS (
    SELECT id FROM sessions WHERE title = ?
    UNION
    SELECT s.id FROM sessions s JOIN lineage l ON s.parent_session_id = l.id
)
SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), COALESCE(SUM(cache_read_tokens), 0),
       COALESCE(SUM(cache_write_tokens), 0), COALESCE(SUM(reasoning_tokens), 0),
       COALESCE(SUM(estimated_cost_usd), 0)
FROM sessions WHERE id IN (SELECT id FROM lineage)
"""


def hermes_cost(text: str) -> float | None:
    m = COST_RE.search(text or "")
    return float(m.group(1).replace(",", "")) if m else None


def _run(args: list[str]) -> str:
    p = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:300])
    return p.stdout


def _check(cfg: dict) -> None:
    if cfg.get("type") != "hermes":
        raise ValueError("unknown meter type %r" % cfg.get("type"))


def make_meter(cfg: dict | None, run: Callable[[list[str]], str] = _run) -> Callable[[int], float | None] | None:
    """local.yml `agents.<a>.meter`: {type: hermes, profile: <profile>}. Unknown cost never crashes a poll."""
    if not cfg:
        return None
    _check(cfg)

    def meter(days: int) -> float | None:
        try:
            return hermes_cost(run(["hermes", "-p", cfg["profile"], "insights", "--days", str(days)]))
        except Exception:
            return None
    return meter


def hermes_state_db(profile: str) -> Path:
    root = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return root / "state.db" if profile in ("", "default") else root / "profiles" / profile / "state.db"


def read_bot_chat_usage(db: Path) -> dict:
    """Cumulative counters of the Bot Chat lineage. Read-only: never writes to the agent's database."""
    if not Path(db).exists():
        raise FileNotFoundError(db)
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
    try:
        row = con.execute(_BOT_CHAT_SQL, (BOT_CHAT_TITLE,)).fetchone()
    finally:
        con.close()
    return dict(zip(USAGE_KEYS, row))


def make_usage(cfg: dict | None) -> Usage | None:
    """Same `meter` entry as make_meter (`state_db:` overrides the path). None = unknown, never an exception."""
    if not cfg:
        return None
    _check(cfg)
    db = Path(os.path.expanduser(cfg["state_db"])) if cfg.get("state_db") else hermes_state_db(cfg["profile"])

    def usage() -> dict | None:
        try:
            return read_bot_chat_usage(db)
        except Exception:
            return None
    return usage


def usage_delta(start: dict | None, end: dict | None) -> dict | None:
    """What a task spent. None when unknown, or when a counter went down (sessions deleted in between)."""
    if not start or not end:
        return None
    d = {k: (end.get(k) or 0) - (start.get(k) or 0) for k in USAGE_KEYS}
    if any(v < 0 for v in d.values()):
        return None
    d["cost"] = round(d["cost"], 4)
    return d
