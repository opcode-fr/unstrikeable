"""Status comment (the heartbeat) and the lease on an agent's current task."""
from unstrikeable.status import lease_step, parse_status, status_body

LIMITS = {"ack_min": 15, "stale_min": 45}
T0 = 1_000_000


def cur(delivered0=T0, delivered=T0, retries=0):
    return {"delivered0": delivered0, "delivered": delivered, "retries": retries}


def st(state, beat):
    return {"state": state, "since": T0, "beat": beat}


# ---------------------------------------------------------------- status comment
def test_status_body_round_trips():
    body = status_body("kevin", "working", since=T0, now=T0 + 60, done="draft", todo="polish")
    assert parse_status(body, "kevin") == {"state": "working", "since": T0, "beat": T0 + 60}
    assert "**Done**: draft" in body and "**Next**: polish" in body


def test_status_of_another_agent_is_not_mine():
    assert parse_status(status_body("kevin", "done", T0, T0), "gerard") is None


def test_status_carries_the_agent_marker():
    assert "<!-- uns:agent=kevin -->" in status_body("kevin", "blocked", T0, T0)


# ---------------------------------------------------------------- lease
def test_task_is_done_when_status_says_done_after_delivery():
    assert lease_step(cur(), st("done", T0 + 10), T0 + 20, LIMITS) == "done"


def test_old_done_status_from_a_previous_task_does_not_count():
    assert lease_step(cur(), st("done", T0 - 10), T0 + 20, LIMITS) == "wait"


def test_no_ack_within_ack_min_retries_once_then_escalates():
    late = T0 + 16 * 60
    assert lease_step(cur(), None, late, LIMITS) == "retry"
    assert lease_step(cur(retries=1), None, late, LIMITS) == "escalate"


def test_working_agent_with_fresh_heartbeat_waits():
    assert lease_step(cur(), st("working", T0 + 60), T0 + 30 * 60, LIMITS) == "wait"


def test_stale_heartbeat_resumes_once_then_escalates():
    late = T0 + 60 + 46 * 60
    assert lease_step(cur(), st("working", T0 + 60), late, LIMITS) == "resume"
    assert lease_step(cur(retries=1), st("working", T0 + 60), late, LIMITS) == "escalate"


def test_closed_or_silenced_item_ends_the_task():
    assert lease_step(cur(), None, T0, LIMITS, gone=True) == "done"


def test_legacy_gha_status_is_parsed():
    body = "<!-- gha:status agent=kevin state=working since=1 beat=5 -->"
    assert parse_status(body, "kevin") == {"state": "working", "since": 1, "beat": 5}


def test_done_status_carries_the_task_kind():
    body = status_body("kevin", "done", T0, T0 + 60, kind="bug")
    assert parse_status(body, "kevin") == {"state": "done", "since": T0, "beat": T0 + 60, "kind": "bug"}
    assert "· bug" in body.splitlines()[0]
