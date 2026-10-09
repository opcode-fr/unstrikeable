"""uns report: per-task records aggregated by agent, kind, role… Pure: records in, text out."""
from unstrikeable.report import report

DAY = 86400
NOW = 100 * DAY


def rec(agent="kevin", kind="article", outcome="done", wall=600, cost=0.5, out=1000, cache=50_000, end=NOW - 60,
        role="writer", retries=0):
    u = None if cost is None else {"in": 10, "out": out, "cache_read": cache, "cache_write": 0, "reasoning": 0,
                                   "cost": cost}
    return {"agent": agent, "kind": kind, "outcome": outcome, "wall_s": wall, "end": end, "role": role,
            "trigger": role + ".assigned", "department": "marketing", "retries": retries, "usage": u}


def test_groups_count_outcomes_time_and_spend():
    text = report([rec(), rec(wall=1800, cost=1.5, retries=1), rec(outcome="blocked", wall=300, cost=0.1)],
                  by=["agent", "kind"], now=NOW, days=30)
    line = [l for l in text.splitlines() if "kevin · article" in l][0]
    assert "3 tasks (2 done, 1 blocked)" in line
    assert "median 10 min" in line
    assert "$2.10 total" in line and "$0.50 median" in line
    assert "3.0k tok" in line and "150k cache" in line
    assert "0.33 nudges/task" in line


def test_old_records_are_out_of_the_window():
    assert "nothing" in report([rec(end=NOW - 40 * DAY)], by=["agent"], now=NOW, days=30)


def test_unknown_cost_is_said_not_guessed():
    line = report([rec(cost=None), rec(cost=0.4)], by=["agent"], now=NOW, days=30).splitlines()[1]
    assert "cost known for 1/2" in line and "$0.40 total" in line


def test_groups_are_sorted_by_spend_and_missing_values_show_a_dash():
    text = report([rec(kind=None, cost=0.1), rec(agent="capucine", role="pm", cost=3.0)],
                  by=["agent", "kind"], now=NOW, days=30)
    lines = text.splitlines()
    assert lines[1].startswith("• capucine · article") and lines[2].startswith("• kevin · -")
    assert lines[-1].startswith("Total: 2 tasks")


def test_unknown_group_key_is_an_error():
    import pytest
    with pytest.raises(ValueError, match="color"):
        report([rec()], by=["color"], now=NOW, days=30)
