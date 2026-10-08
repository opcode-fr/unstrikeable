"""Usage meters: estimated agent cost, read from the agent runtime."""
from unstrikeable.meter import hermes_cost, make_meter

# Real `hermes -p <profile> insights --days 1` output (head), captured on 2026-10-08.
INSIGHTS = """
  📋 Overview
  ────────────────────────────────────────────────────────
  Sessions:          8             Messages:        538
  Total tokens:      24,063,668

  💰 Cost
  ────────────────────────────────────────────────────────
  Estimated:          ~$17.63
"""


def test_hermes_cost_is_parsed():
    assert hermes_cost(INSIGHTS) == 17.63


def test_unparsable_output_gives_unknown_cost():
    assert hermes_cost("no sessions") is None


def test_hermes_meter_calls_insights_for_the_profile_and_window():
    calls = []
    meter = make_meter({"type": "hermes", "profile": "kevin"}, run=lambda args: calls.append(args) or INSIGHTS)
    assert meter(30) == 17.63
    assert calls == [["hermes", "-p", "kevin", "insights", "--days", "30"]]


def test_no_meter_configured():
    assert make_meter(None) is None


def test_failing_meter_gives_unknown_cost_instead_of_crashing():
    def boom(args):
        raise RuntimeError("hermes not found")
    assert make_meter({"type": "hermes", "profile": "kevin"}, run=boom)(1) is None
