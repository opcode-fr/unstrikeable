"""Vetting gate: content from non-members waits for a member's go before any agent reads it."""
import json
from pathlib import Path

from unstrikeable.backends.github import parse_issue
from unstrikeable.config import load_flow
from unstrikeable.events import needs_vetting
from unstrikeable.model import BLOCKING, VETTING, Comment, Item

FLOW = load_flow("dev")
DATA = Path(__file__).parent / "data"
REAL_BOTS = {"didier-usejul", "jeanmi-usejul", "gerard-usejul", "capucine-usejul"}


def item(outsider_at="", comments=()):
    return Item("acme/app", 1, "t", "ready", outsider_at=outsider_at, comments=list(comments))


def human(i, at):
    return Comment(i, "brice", True, created=at)


def test_vetting_label_blocks_agents():
    assert VETTING in BLOCKING


def test_item_without_outside_content_needs_no_vetting():
    assert not needs_vetting(item())


def test_outside_content_needs_vetting_until_a_member_comments_after_it():
    assert needs_vetting(item("2026-10-09T10:00:00Z"))
    assert needs_vetting(item("2026-10-09T10:00:00Z", [human(1, "2026-10-09T09:00:00Z")]))   # go before it
    assert not needs_vetting(item("2026-10-09T10:00:00Z", [human(1, "2026-10-09T11:00:00Z")]))


def test_agent_comments_and_status_comments_are_not_a_go():
    agent = Comment(2, "jeanmichel-acme", True, agent="jeanmichel", created="2026-10-09T11:00:00Z")
    status = Comment(3, "brice", True, status=True, created="2026-10-09T11:00:00Z")
    outsider = Comment(4, "random", False, created="2026-10-09T11:00:00Z")
    assert needs_vetting(item("2026-10-09T10:00:00Z", [agent, status, outsider]))


# ------------------------------------------------------------ backend: outsider_at from a real payload
def real():
    return json.loads((DATA / "gh_issue_jul36.json").read_text())


def test_real_issue_from_members_and_our_agents_has_no_outside_content():
    it = parse_issue(real(), "Ready", FLOW, REAL_BOTS)
    assert it.outsider_at == ""
    assert all(c.created for c in it.comments)


def test_real_issue_without_our_bots_counts_their_comments_as_outside():
    it = parse_issue(real(), "Ready", FLOW, set())
    agent_dates = [c["createdAt"] for c in real()["comments"]["nodes"] if c["authorAssociation"] == "NONE"]
    pr_dates = [x["createdAt"] for p in real()["closedByPullRequestsReferences"]["nodes"]
                for x in p["comments"]["nodes"] + p["reviews"]["nodes"] if x["authorAssociation"] == "NONE"]
    assert it.outsider_at == max(agent_dates + pr_dates)


def test_outsider_issue_body_pull_request_and_review_all_count():
    n = real()
    n["comments"]["nodes"] = []
    n["closedByPullRequestsReferences"]["nodes"] = []
    n.update(authorAssociation="NONE", author={"login": "stranger"})
    assert parse_issue(n, "Ready", FLOW, REAL_BOTS).outsider_at == n["createdAt"]

    pr = real()["closedByPullRequestsReferences"]["nodes"][0]
    pr.update(authorAssociation="NONE", author={"login": "forker"}, createdAt="2027-01-01T00:00:00Z")
    n = real()
    n["closedByPullRequestsReferences"]["nodes"] = [pr]
    assert parse_issue(n, "Ready", FLOW, REAL_BOTS).outsider_at == "2027-01-01T00:00:00Z"

    pr = real()["closedByPullRequestsReferences"]["nodes"][0]
    pr["reviews"]["nodes"].append({"createdAt": "2027-02-01T00:00:00Z", "authorAssociation": "NONE",
                                   "author": {"login": "drive-by"}})
    n = real()
    n["closedByPullRequestsReferences"]["nodes"] = [pr]
    assert parse_issue(n, "Ready", FLOW, REAL_BOTS).outsider_at == "2027-02-01T00:00:00Z"


def test_private_member_seen_as_none_by_an_app_token_is_not_outside_when_he_can_write():
    n = real()
    n["closedByPullRequestsReferences"]["nodes"] = []
    for c in n["comments"]["nodes"]:
        if c["author"]["login"] == "jlestel":
            c["authorAssociation"] = "NONE"
    it = parse_issue(n, "Ready", FLOW, REAL_BOTS, can_write=lambda login: login == "jlestel")
    assert it.outsider_at == ""


def test_ci_bot_comment_on_our_agents_pr_is_not_outside_content():
    n = json.loads((DATA / "gh_issue_jul52.json").read_text())          # PR #55 of gerard + a github-actions comment
    it = parse_issue(n, "Ready", FLOW, REAL_BOTS)
    assert it.outsider_at == "" and not needs_vetting(it)
    assert not any(c.trusted for c in it.comments if c.author == "github-actions")   # still not a human go
