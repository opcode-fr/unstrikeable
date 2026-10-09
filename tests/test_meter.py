"""Usage meter: Bot Chat counters of a Hermes profile (per-task cost and cost quotas)."""
import sqlite3
from pathlib import Path

from unstrikeable.meter import hermes_state_db, make_usage, read_bot_chat_usage

SCHEMA = (Path(__file__).parent / "data" / "hermes_sessions.sql").read_text()
COLS = ("id", "source", "title", "parent_session_id", "started_at", "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "estimated_cost_usd")
# Real rows of a worker profile (kevin), 2026-10-08: a provider check, then the canonical Bot Chat.
REAL = [("20261008_224806_17ae6b", "oneshot", "Reply with OK", None, 1791492486.5, 4, 4, 0, 18915, 0, 0.0473355),
        ("20261008_230149_59cd88", "cli", "Bot Chat", None, 1791493309.2, 38, 8074, 562750, 86195, 0, 0.3525785)]


def state_db(tmp_path, rows):
    path = tmp_path / "state.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO sessions (%s) VALUES (%s)" % (",".join(COLS), ",".join("?" * len(COLS))), rows)
    con.commit()
    con.close()
    return path


def test_bot_chat_usage_reads_real_rows(tmp_path):
    u = read_bot_chat_usage(state_db(tmp_path, REAL))
    assert u == {"in": 38, "out": 8074, "cache_read": 562750, "cache_write": 86195, "reasoning": 0,
                 "cost": 0.3525785}


def test_bot_chat_usage_follows_compression_and_subagent_children(tmp_path):
    rows = REAL + [("child", "cli", None, "20261008_230149_59cd88", 1791493400.0, 10, 20, 30, 40, 5, 0.1),
                   ("grandchild", "cli", "Bot Chat", "child", 1791493500.0, 1, 2, 3, 4, 0, 0.01),
                   ("slack", "slack", "Chat with Brice", None, 1791493600.0, 999, 999, 999, 999, 0, 9.0)]
    u = read_bot_chat_usage(state_db(tmp_path, rows))
    assert u["in"] == 49 and u["reasoning"] == 5 and round(u["cost"], 4) == round(0.3525785 + 0.11, 4)


def test_no_bot_chat_yet_is_zero_not_unknown(tmp_path):
    assert read_bot_chat_usage(state_db(tmp_path, REAL[:1]))["cost"] == 0


def test_state_db_path_of_a_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert hermes_state_db("kevin") == tmp_path / "profiles" / "kevin" / "state.db"
    assert hermes_state_db("default") == tmp_path / "state.db"


def test_usage_reader_from_local_config(tmp_path):
    db = state_db(tmp_path, REAL)
    usage = make_usage({"type": "hermes", "profile": "kevin", "state_db": str(db)})
    assert usage()["out"] == 8074


def test_usage_reader_never_crashes(tmp_path):
    usage = make_usage({"type": "hermes", "profile": "kevin", "state_db": str(tmp_path / "missing.db")})
    assert usage() is None
    assert make_usage(None) is None


def test_usage_delta_is_what_the_task_spent():
    from unstrikeable.meter import usage_delta
    a = {"in": 1, "out": 2, "cache_read": 3, "cache_write": 4, "reasoning": 0, "cost": 0.1}
    b = {"in": 11, "out": 22, "cache_read": 33, "cache_write": 44, "reasoning": 5, "cost": 0.35}
    assert usage_delta(a, b) == {"in": 10, "out": 20, "cache_read": 30, "cache_write": 40, "reasoning": 5, "cost": 0.25}
    assert usage_delta(b, a) is None and usage_delta(None, b) is None


def test_piped_usage_is_parsed_and_checked():
    from unstrikeable.meter import parse_usage
    good = '{"in": 1, "out": 2, "cache_read": 3, "cache_write": 4, "reasoning": 0, "cost": 0.5}'
    assert parse_usage(good)["cost"] == 0.5
    for bad in ("", "null", "not json", '{"in": 1}', '{"in": "x", "out": 2, "cache_read": 3, "cache_write": 4, '
                '"reasoning": 0, "cost": 0.5}'):
        assert parse_usage(bad) is None
