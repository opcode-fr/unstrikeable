"""GitHub Projects board: parsing of the GraphQL payload into Items (pure part)."""
from unstrikeable.backends.github import GitHubBoard, parse_issue
from unstrikeable.config import load_flow

FLOW = load_flow("dev")
BOTS = {"jeanmi-acme"}


def node(**over):
    n = {
        "number": 12, "title": "Add X", "url": "https://github.com/acme/app/issues/12", "state": "OPEN",
        "authorAssociation": "MEMBER",
        "repository": {"nameWithOwner": "acme/app"},
        "labels": {"nodes": [{"name": "dev:jeanmi"}]},
        "issueDependenciesSummary": {"blockedBy": 0},
        "comments": {"nodes": [
            {"databaseId": 1, "author": {"login": "brice"}, "authorAssociation": "OWNER", "body": "go"},
            {"databaseId": 2, "author": {"login": "jeanmi-acme"}, "authorAssociation": "NONE",
             "body": "plan\n<!-- uns:agent=jeanmi -->"},
            {"databaseId": 3, "author": {"login": "jeanmi-acme"}, "authorAssociation": "NONE",
             "body": "🟢\n<!-- uns:agent=jeanmi -->\n<!-- uns:status agent=jeanmi state=working since=1 beat=2 -->"},
            {"databaseId": 4, "author": {"login": "random"}, "authorAssociation": "NONE", "body": "hey"},
            {"databaseId": 5, "author": None, "authorAssociation": "NONE", "body": "ghost"},
        ]},
        "closedByPullRequestsReferences": {"nodes": [
            {"number": 7, "url": "https://github.com/acme/app/pull/7", "mergeable": "CONFLICTING",
             "headRefOid": "abc", "statusCheckRollup": {"state": "ERROR"}}]},
    }
    n.update(over)
    return n


def test_issue_maps_column_to_logical_state():
    it = parse_issue(node(), "In progress", FLOW, BOTS)
    assert (it.repo, it.number, it.state) == ("acme/app", 12, "doing")
    assert it.labels == ["dev:jeanmi"]


def test_unknown_column_gives_no_state():
    assert parse_issue(node(), None, FLOW, BOTS).state is None


def test_comments_carry_trust_agent_and_status():
    cs = parse_issue(node(), "Ready", FLOW, BOTS).comments
    assert [(c.id, c.trusted, c.agent, c.status) for c in cs] == [
        (1, True, None, False), (2, True, "jeanmi", False), (3, True, "jeanmi", True),
        (4, False, None, False), (5, False, None, False)]


def test_agent_marker_from_an_unknown_author_is_not_trusted():
    n = node(comments={"nodes": [{"databaseId": 9, "author": {"login": "evil"}, "authorAssociation": "NONE",
                                  "body": "<!-- uns:agent=jeanmi -->"}]})
    c = parse_issue(n, "Ready", FLOW, BOTS).comments[0]
    assert not c.trusted


def test_linked_pr_with_error_rollup_counts_as_failed_ci():
    pr = parse_issue(node(), "To merge", FLOW, BOTS).prs[0]
    assert (pr.number, pr.head, pr.mergeable, pr.ci) == (7, "abc", "CONFLICTING", "FAILURE")


def test_outsider_issue_is_not_trusted():
    assert not parse_issue(node(authorAssociation="NONE"), "Backlog", FLOW, BOTS).author_trusted


def test_items_pages_through_the_board_and_skips_closed_issues_and_drafts():
    pages = [
        {"organization": {"projectV2": {"items": {
            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            "nodes": [{"status": {"name": "Ready"}, "content": {"__typename": "Issue", **node()}},
                      {"status": None, "content": {"__typename": "DraftIssue"}}]}}}},
        {"organization": {"projectV2": {"items": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"status": {"name": "Done"}, "content": {"__typename": "Issue", **node(number=13, state="CLOSED")}},
                      {"status": {"name": "Backlog"}, "content": {"__typename": "Issue", **node(number=14)}}]}}}},
    ]
    calls = []

    def fake_graphql(query, **variables):
        calls.append(variables.get("cursor"))
        return pages[len(calls) - 1]

    board = GitHubBoard({"owner": "acme", "number": 1}, FLOW, BOTS, graphql=fake_graphql)
    assert [(i.number, i.state) for i in board.items()] == [(12, "ready"), (14, "backlog")]
    assert calls == [None, "c1"]
