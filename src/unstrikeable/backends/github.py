"""GitHub Projects (v2) board, on top of the `gh` CLI."""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Callable, Sequence

from ..config import Department, Flow
from ..layout import expected_labels, plan_columns
from ..model import PR, Comment, Item
from ..status import parse_status

TRUSTED = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
AGENT_RE = re.compile(r"<!-- (?:uns|gha):agent=(\S+) -->")      # gha = legacy gh-agents markers
CI = {"SUCCESS": "SUCCESS", "FAILURE": "FAILURE", "ERROR": "FAILURE", "PENDING": "PENDING", "EXPECTED": "PENDING"}

ISSUE_FIELDS = """
  number title url state createdAt authorAssociation author { login }
  repository { nameWithOwner }
  labels(first:30) { nodes { name } }
  issueDependenciesSummary { blockedBy }
  comments(last:30) { nodes { databaseId createdAt author { login } authorAssociation body } }
  closedByPullRequestsReferences(first:5, includeClosedPrs:false) {
    nodes { number url mergeable headRefOid createdAt authorAssociation author { login }
      statusCheckRollup { state }
      comments(last:20) { nodes { createdAt authorAssociation author { login } } }
      reviews(last:20) { nodes { createdAt authorAssociation author { login } } } } }
"""

ITEMS_Q = """
query($login:String!, $num:Int!, $cursor:String) {
  %s(login:$login) { projectV2(number:$num) {
    items(first:50, after:$cursor) { pageInfo { hasNextPage endCursor }
      nodes { status: fieldValueByName(name:"Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
        content { __typename ... on Issue { %s } } } } } }
}"""

ISSUE_Q = """
query($o:String!, $r:String!, $n:Int!, $f:String!) { repository(owner:$o, name:$r) { issue(number:$n) {
  id %s
  projectItems(first:20) { nodes { id
    project { id number owner { ... on Organization { login } ... on User { login } }
      field(name:$f) { ... on ProjectV2SingleSelectField { id options { id name } } } }
    status: fieldValueByName(name:"Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } } } } } } }"""


class GitHubError(Exception):
    pass


def gh(args: list[str], stdin: str | None = None, token: str | None = None) -> str:
    env = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token
    p = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise GitHubError("gh %s: %s" % (" ".join(args[:2]), p.stderr.strip()[:500]))
    return p.stdout


def make_graphql(token: str | None = None) -> Callable[..., dict]:
    def graphql(query: str, **variables: Any) -> dict:
        out = json.loads(gh(["api", "graphql", "--input", "-"],
                            stdin=json.dumps({"query": query, "variables": variables}), token=token))
        if out.get("errors"):
            raise GitHubError("graphql: " + json.dumps(out["errors"])[:800])
        return out["data"]
    return graphql


def _login(author: dict | None) -> str:
    return ((author or {}).get("login") or "").replace("[bot]", "").lower()


WRITE_PERMS = frozenset({"admin", "maintain", "write", "triage"})


def _trusted(assoc: str | None, login: str, bots: set[str], can_write: Callable[[str], bool] | None) -> bool:
    """Member of the org, one of our agents, or (App tokens see private members as NONE) a repo writer."""
    if assoc in TRUSTED or login in bots:
        return True
    return bool(can_write and login and login != "?" and can_write(login))


def parse_comment(c: dict, bots: set[str], can_write: Callable[[str], bool] | None = None) -> Comment:
    login = _login(c.get("author"))
    trusted = _trusted(c.get("authorAssociation"), login, bots, can_write)
    body = c.get("body") or ""
    m = AGENT_RE.search(body) if trusted else None
    return Comment(int(c["databaseId"]), login or "?", trusted, agent=m.group(1) if m else None,
                   status=trusted and ("<!-- uns:status " in body or "<!-- gha:status " in body), body=body,
                   created=c.get("createdAt") or "")


def outsider_at(n: dict, bots: set[str], can_write: Callable[[str], bool] | None = None) -> str:
    """Newest content written by a non-member: issue body, comments, linked PRs, their comments and reviews."""
    def outside(x: dict) -> bool:
        return not _trusted(x.get("authorAssociation"), _login(x.get("author")), bots, can_write)

    nodes = [n] + n["comments"]["nodes"]
    for p in (n.get("closedByPullRequestsReferences") or {}).get("nodes") or []:
        nodes += [p] + ((p.get("comments") or {}).get("nodes") or []) + ((p.get("reviews") or {}).get("nodes") or [])
    return max((x.get("createdAt") or "" for x in nodes if outside(x)), default="")


def parse_issue(n: dict, column: str | None, flow: Flow, bots: set[str],
                can_write: Callable[[str], bool] | None = None) -> Item:
    prs = [PR(p["number"], p["url"], p["headRefOid"], p.get("mergeable") or "UNKNOWN",
              CI.get(((p.get("statusCheckRollup") or {}).get("state")) or ""))
           for p in (n.get("closedByPullRequestsReferences") or {}).get("nodes") or []]
    return Item(
        repo=n["repository"]["nameWithOwner"], number=n["number"], title=n["title"],
        state=flow.state_of(column), labels=[l["name"] for l in n["labels"]["nodes"]], url=n.get("url", ""),
        author=_login(n.get("author")),
        author_trusted=_trusted(n.get("authorAssociation"), _login(n.get("author")), bots, can_write),
        author_agent=_login(n.get("author")) if _login(n.get("author")) in bots else None,
        blocked_by=(n.get("issueDependenciesSummary") or {}).get("blockedBy") or 0,
        comments=[parse_comment(c, bots, can_write) for c in n["comments"]["nodes"]], prs=prs,
        outsider_at=outsider_at(n, bots, can_write))


def split_ref(ref: str) -> tuple[str, str, int]:
    repo, _, num = ref.partition("#")
    if "/" not in repo or not num.isdigit():
        raise GitHubError("bad item ref %r (expected owner/repo#123)" % ref)
    o, r = repo.split("/", 1)
    return o, r, int(num)


class GitHubBoard:
    """Board = one GitHub Project v2; items = its open issues."""

    def __init__(self, board: dict, flow: Flow, bots: set[str], token: str | None = None,
                 graphql: Callable[..., dict] | None = None, run: Callable[..., str] | None = None):
        self.owner = board["owner"]
        self.number = int(board["number"])
        self.kind = board.get("owner_type", "organization")
        self.flow, self.bots, self.token = flow, {b.lower() for b in bots}, token
        self.graphql = graphql or make_graphql(token)
        self.run = run or (lambda args, stdin=None: gh(args, stdin, token))
        self._perms: dict[tuple[str, str], bool] = {}

    def can_write(self, repo: str, login: str) -> bool:
        """Repo permission of a user, cached for the life of this board object (one poll)."""
        key = (repo, login)
        if key not in self._perms:
            try:
                perm = self.run(["api", "repos/%s/collaborators/%s/permission" % (repo, login),
                                 "--jq", ".permission"]).strip()
            except GitHubError:
                perm = ""
            self._perms[key] = perm in WRITE_PERMS
        return self._perms[key]

    def _parse(self, n: dict, column: str | None) -> Item:
        repo = n["repository"]["nameWithOwner"]
        return parse_issue(n, column, self.flow, self.bots, lambda login: self.can_write(repo, login))

    # ------------------------------------------------------------ read
    def items(self) -> list[Item]:
        out, cursor = [], None
        while True:
            d = self.graphql(ITEMS_Q % (self.kind, ISSUE_FIELDS), login=self.owner, num=self.number, cursor=cursor)
            page = d[self.kind]["projectV2"]["items"]
            for n in page["nodes"]:
                c = n.get("content") or {}
                if c.get("__typename") != "Issue" or c.get("state") != "OPEN":
                    continue
                out.append(self._parse(c, (n.get("status") or {}).get("name")))
            if not page["pageInfo"]["hasNextPage"]:
                return out
            cursor = page["pageInfo"]["endCursor"]

    def _issue(self, ref: str, field: str = "Status") -> tuple[dict, dict | None]:
        o, r, n = split_ref(ref)
        iss = self.graphql(ISSUE_Q % ISSUE_FIELDS, o=o, r=r, n=n, f=field)["repository"]["issue"]
        if not iss:
            raise GitHubError("%s not found" % ref)
        mine = next((pi for pi in iss["projectItems"]["nodes"]
                     if pi["project"]["number"] == self.number
                     and (pi["project"]["owner"] or {}).get("login", "").lower() == self.owner.lower()), None)
        return iss, mine

    def item(self, ref: str) -> Item | None:
        iss, pi = self._issue(ref)
        if iss["state"] != "OPEN":
            return None
        return self._parse(iss, ((pi or {}).get("status") or {}).get("name"))

    # ------------------------------------------------------------ write
    def move(self, ref: str, column: str) -> None:
        self.set_field(ref, "Status", column)

    def set_field(self, ref: str, name: str, value: str) -> None:
        """Set a single-select field of the board (Status, Size, Priority…) by option name."""
        iss, pi = self._issue(ref, name)
        if not pi:
            raise GitHubError("%s is not on project %s/%d" % (ref, self.owner, self.number))
        field = pi["project"]["field"]
        if not field:
            raise GitHubError("no single-select field %r on the board" % name)
        opt = next((o for o in field["options"] if o["name"].lower() == value.lower()), None)
        if not opt:
            raise GitHubError("no %r option %r on the board (%s)" % (name, value, [o["name"] for o in field["options"]]))
        self.graphql("""mutation($p:ID!, $i:ID!, $f:ID!, $o:String!) { updateProjectV2ItemFieldValue(input:{
            projectId:$p, itemId:$i, fieldId:$f, value:{ singleSelectOptionId:$o } }) { clientMutationId } }""",
                     p=pi["project"]["id"], i=pi["id"], f=field["id"], o=opt["id"])

    def labels(self, ref: str, add: Sequence[str] = (), remove: Sequence[str] = ()) -> None:
        o, r, n = split_ref(ref)
        if add:
            self.run(["api", "-X", "POST", "repos/%s/%s/issues/%d/labels" % (o, r, n), "--input", "-"],
                     stdin=json.dumps({"labels": list(add)}))
        for label in remove:
            try:
                self.run(["api", "-X", "DELETE", "repos/%s/%s/issues/%d/labels/%s" % (o, r, n, label)])
            except GitHubError as e:
                if "404" not in str(e) and "Not Found" not in str(e):   # already absent: fine
                    raise

    def comment(self, ref: str, body: str) -> None:
        o, r, n = split_ref(ref)
        self.run(["api", "-X", "POST", "repos/%s/%s/issues/%d/comments" % (o, r, n), "--input", "-"],
                 stdin=json.dumps({"body": body}))

    def upsert_status(self, ref: str, agent: str, body: str) -> None:
        o, r, n = split_ref(ref)
        pages = json.loads(self.run(["api", "repos/%s/%s/issues/%d/comments?per_page=100" % (o, r, n),
                                     "--paginate", "--slurp"]))
        mine = next((c for page in pages for c in page if parse_status(c.get("body") or "", agent)), None)
        if mine:
            self.run(["api", "-X", "PATCH", "repos/%s/%s/issues/comments/%d" % (o, r, mine["id"]), "--input", "-"],
                     stdin=json.dumps({"body": body}))
        else:
            self.comment(ref, body)

    # ------------------------------------------------------------ layout
    def ensure_layout(self, dept: Department, apply: bool = False, prune: bool = False) -> list[str]:
        """Columns of the Status field in flow order, and the department's labels in each repo.
        Returns the plan (one line per action); writes only when apply=True. Deletes columns only with prune,
        and only when no open item sits outside the flow's columns (its status would be lost)."""
        if prune and apply:
            lost = [i for i in self.items() if i.state is None]
            if lost:
                raise GitHubError("prune refused: %d open item(s) sit on columns outside the flow (%s)" % (
                    len(lost), ", ".join(i.ref for i in lost[:5])))
        d = self.graphql("""query($l:String!, $n:Int!) { %s(login:$l) { projectV2(number:$n) { title
            field(name:"Status") { ... on ProjectV2SingleSelectField { id options { id name color description } } }
            } } }""" % self.kind, l=self.owner, n=self.number)
        field = d[self.kind]["projectV2"]["field"]
        opts, plan = plan_columns(field["options"], [s.column for s in dept.flow.states], prune)
        if opts is not None and apply:
            self.graphql("""mutation($f:ID!, $o:[ProjectV2SingleSelectFieldOptionInput!]) {
                updateProjectV2Field(input:{fieldId:$f, singleSelectOptions:$o}) { clientMutationId } }""",
                         f=field["id"], o=opts)
        want = expected_labels(dept)
        for repo in dept.repos:
            have = {l["name"]: l["color"].lower() for l in json.loads(self.run(
                ["label", "list", "-R", repo, "--limit", "500", "--json", "name,color,description"]))}
            for name, (color, desc) in want.items():
                if name in have:
                    if have[name] != color:
                        plan.append("%s: ~ label %s color %s -> %s" % (repo, name, have[name], color))
                        if apply:
                            self.run(["label", "edit", name, "-R", repo, "--color", color])
                    continue
                plan.append("%s: + label %s" % (repo, name))
                if apply:
                    self.run(["label", "create", name, "-R", repo, "--color", color, "--description", desc])
        return plan
