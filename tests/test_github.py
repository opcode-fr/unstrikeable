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


def test_layout_dry_run_plans_without_writing():
    from unstrikeable.config import Department
    status = {"organization": {"projectV2": {"title": "Mkt", "field": {"id": "F", "options": [
        {"id": "o1", "name": "Ideas", "color": "GRAY", "description": ""}]}}}}
    writes = []

    def fake_graphql(query, **v):
        if query.lstrip().startswith("mutation"):
            writes.append(v)
        return status

    def fake_run(args, stdin=None):
        if args[:2] == ["label", "list"]:
            return '[{"name": "needs:human", "color": "d93f0b", "description": ""}]'
        writes.append(args)
        return ""

    flow = load_flow("content")
    d = Department("marketing", flow, {"owner": "acme", "number": 2}, ["acme/mkt"], {"kevin": ["writer"]})
    board = GitHubBoard(d.board, flow, set(), graphql=fake_graphql, run=fake_run)
    plan = board.ensure_layout(d, apply=False)
    assert "+ column 'Drafting'" in plan and "acme/mkt: + label writer:kevin" in plan
    assert "acme/mkt: + label needs:human" not in plan
    assert writes == []
    board.ensure_layout(d, apply=True)
    assert writes and writes[0]["f"] == "F"


def test_prune_is_refused_while_items_sit_outside_the_flow():
    import pytest
    from unstrikeable.backends.github import GitHubError
    from unstrikeable.config import Department
    flow = load_flow("content")
    d = Department("marketing", flow, {"owner": "acme", "number": 2}, ["acme/mkt"], {})
    board = GitHubBoard(d.board, flow, set(), graphql=lambda q, **v: {}, run=lambda a, stdin=None: "[]")
    board.items = lambda: [parse_issue(node(), "Todo", flow, set())]
    with pytest.raises(GitHubError, match="1 open item"):
        board.ensure_layout(d, apply=True, prune=True)


def test_app_tokens_see_private_members_as_none_so_repo_permission_decides():
    # Verified live: through a GitHub App token, a private org member's authorAssociation is NONE.
    n = node(authorAssociation="NONE", comments={"nodes": [
        {"databaseId": 1, "author": {"login": "bdauzats"}, "authorAssociation": "NONE", "body": "go"},
        {"databaseId": 2, "author": {"login": "random"}, "authorAssociation": "NONE", "body": "hey"}]})
    n["author"] = {"login": "bdauzats"}
    writers = {"bdauzats"}
    it = parse_issue(n, "Ideas", load_flow("content"), set(), can_write=lambda login: login in writers)
    assert it.author_trusted
    assert [c.trusted for c in it.comments] == [True, False]


def test_repo_permission_lookup_is_cached_per_login():
    calls = []

    def run(args, stdin=None):
        calls.append(args)
        return "write\n" if "bdauzats" in args[1] else "read\n"
    board = GitHubBoard({"owner": "acme", "number": 1}, FLOW, set(), graphql=lambda q, **v: {}, run=run)
    assert board.can_write("acme/app", "bdauzats") and board.can_write("acme/app", "bdauzats")
    assert not board.can_write("acme/app", "random")
    assert len(calls) == 2
