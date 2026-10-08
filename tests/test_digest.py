"""Digest for humans (Slack): alerts since last time, and a daily summary of the instance's agents."""
from unstrikeable.digest import digest

T0 = 1_000_000


def states():
    return {
        "kevin": {"day": "2026-10-08", "day_count": 3,
                  "current": {"ref": "acme/mkt#4", "trigger": "writer.assigned", "delivered0": T0},
                  "alerts": [{"ts": T0 + 10, "ref": "acme/mkt#2", "msg": "kevin stopped answering"}]},
        "jeanmichel": {"day": "2026-10-07", "day_count": 9, "current": None, "alerts": []},
    }


def test_alerts_mode_is_silent_when_nothing_new():
    text, cursor = digest(states(), cursor=T0 + 10, today="2026-10-08", alerts_only=True)
    assert text == "" and cursor == T0 + 10


def test_alerts_mode_lists_new_alerts_and_moves_the_cursor():
    text, cursor = digest(states(), cursor=0, today="2026-10-08", alerts_only=True)
    assert "⚠️ *kevin* · acme/mkt#2 · kevin stopped answering" in text
    assert cursor == T0 + 10


def test_summary_shows_each_agent_with_today_count():
    text, _ = digest(states(), cursor=T0 + 10, today="2026-10-08", alerts_only=False, instance="mac-mini")
    assert "*unstrikeable · mac-mini*" in text
    assert "• *kevin*: acme/mkt#4 `writer.assigned`" in text and "3 event(s) today" in text
    assert "• *jeanmichel*: free · 0 event(s) today" in text            # yesterday's count does not show
