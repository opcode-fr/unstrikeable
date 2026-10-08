"""poll: lease of the current task, then at most one new event per agent."""
from pathlib import Path

from fakes import FakeBoard

from unstrikeable.config import DEFAULT_LIMITS, Company, Department, load_flow
from unstrikeable.model import Comment, Item
from unstrikeable.poll import poll
from unstrikeable.status import status_body

T0 = 1_000_000


def company(tmp_path, limits=None, staff=None):
    flow = load_flow("content")
    d = Department("marketing", flow, {}, ["acme/mkt"], staff or {"kevin": ["planner", "writer"]})
    return Company(Path(tmp_path), {"marketing": d}, {"kevin": {}, "brandon": {}},
                   {**DEFAULT_LIMITS, **(limits or {})})


def item(n, state="ready", labels=("writer:kevin",), comments=()):
    return Item("acme/mkt", n, "post %d" % n, state, list(labels), comments=list(comments))


def run(co, board, state, now=T0, dry=False):
    return poll("kevin", co, {"marketing": board}, state, now, dry_run=dry)


def test_first_event_is_delivered_and_becomes_current(tmp_path):
    board, state = FakeBoard([item(1)]), {}
    out = run(company(tmp_path), board, state)
    assert "acme/mkt#1" in out and "writer.assigned" in out
    assert "uns status acme/mkt#1 --agent kevin --state working" in out
    assert state["current"]["ref"] == "acme/mkt#1"


def test_nothing_else_while_current_task_is_running(tmp_path):
    board, state, co = FakeBoard([item(1), item(2, "backlog", ())]), {}, company(tmp_path)
    run(co, board, state)
    assert run(co, board, state, now=T0 + 60) == ""


def test_done_status_frees_the_agent_for_the_next_event(tmp_path):
    board, state, co = FakeBoard([item(1), item(2, "backlog", ())]), {}, company(tmp_path)
    assert "planner.item_new" in run(co, board, state)          # specs before new work
    board.upsert_status("acme/mkt#2", "kevin", status_body("kevin", "done", T0, T0 + 120))
    out = run(co, board, state, now=T0 + 180)
    assert "acme/mkt#1" in out and "writer.assigned" in out


def test_same_event_is_never_delivered_twice(tmp_path):
    board, state, co = FakeBoard([item(1)]), {}, company(tmp_path)
    run(co, board, state)
    board.upsert_status("acme/mkt#1", "kevin", status_body("kevin", "done", T0, T0 + 60))
    assert run(co, board, state, now=T0 + 120) == ""


def test_silent_agent_is_nudged_once_then_marked_lost(tmp_path):
    board, state, co = FakeBoard([item(1)]), {}, company(tmp_path)
    run(co, board, state)
    out = run(co, board, state, now=T0 + 16 * 60)
    assert "RETRY" in out
    out = run(co, board, state, now=T0 + 33 * 60)
    assert out == ""
    assert "agent:lost" in board.item("acme/mkt#1").labels
    assert state["current"] is None


def test_closed_item_ends_the_task(tmp_path):
    board, state, co = FakeBoard([item(1)]), {}, company(tmp_path)
    run(co, board, state)
    board.close("acme/mkt#1")
    run(co, board, state, now=T0 + 60)
    assert state["current"] is None


def test_daily_budget_stops_new_events(tmp_path):
    board, state = FakeBoard([item(1)]), {"day": "x", "day_count": 0}
    co = company(tmp_path, limits={"max_events_per_day": 0})
    assert run(co, board, state) == ""


def test_too_many_runs_on_one_item_asks_for_a_human(tmp_path):
    board = FakeBoard([item(1)])
    state = {"runs": {"acme/mkt#1": 10}}
    out = run(company(tmp_path), board, state)
    assert "BUDGET" in out and "needs:human" in out


def test_dry_run_changes_nothing(tmp_path):
    board, state = FakeBoard([item(1)]), {}
    out = run(company(tmp_path), board, state, dry=True)
    assert "acme/mkt#1" in out
    assert state.get("current") is None and not state.get("seen")


def test_two_named_assignees_are_flagged_for_a_human(tmp_path):
    co = company(tmp_path, staff={"kevin": ["writer"], "brandon": ["writer"]})
    board, state = FakeBoard([item(1, labels=("writer:kevin", "writer:brandon"))]), {}
    assert run(co, board, state) == ""
    assert "needs:human" in board.item("acme/mkt#1").labels


def test_culture_and_agent_sheet_are_handed_over(tmp_path):
    (tmp_path / "culture.md").write_text("Frugal first.")
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "kevin.md").write_text("Kevin, community manager.")
    out = run(company(tmp_path), FakeBoard([item(1)]), {})
    assert "Frugal first." in out and "Kevin, community manager." in out


def test_human_comment_is_quoted_in_the_event(tmp_path):
    c = Comment(7, "brice", True, body="Shorter please")
    board = FakeBoard([item(1, "doing", comments=[c])])
    assert "Shorter please" in run(company(tmp_path), board, {})
