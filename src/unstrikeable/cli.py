"""`uns`: the command line used by cron wrappers (poll) and by agents (move, status, comment, label)."""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import yaml

from .backends.base import Board
from .backends.github import GitHubBoard, GitHubError
from .config import Company, ConfigError, Department, load_company
from .model import AGENT_MARK
from .poll import poll
from .status import STATES, parse_status, status_body

CULTURE_MAX_WORDS = 600          # ~1 page; it is injected in every event


class UsageError(Exception):
    pass


# ---------------------------------------------------------------- local instance
def home() -> Path:
    return Path(os.environ.get("UNS_HOME") or Path.home() / ".unstrikeable")


def load_local() -> dict:
    path = home() / "local.yml"
    if not path.exists():
        raise UsageError("missing %s (instance config: instance, config, agents)" % path)
    return yaml.safe_load(path.read_text()) or {}


def load(local: dict) -> Company:
    if not local.get("config"):
        raise UsageError("local.yml: `config` must point to the local clone of the config repo")
    return load_company(Path(os.path.expanduser(local["config"])))


def bots(co: Company) -> set[str]:
    return {str(a.get("identity", "")).lower() for a in co.agents.values() if a.get("identity")}


# ---------------------------------------------------------------- GitHub App token
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def app_token(app: dict, owner: str) -> str:
    """Installation token of a GitHub App (JWT signed with openssl), cached 50 min."""
    cache = home() / "state" / ("token-%s.json" % app["app_id"])
    if cache.exists():
        c = json.loads(cache.read_text())
        if c["exp"] > time.time() + 300:
            return c["token"]
    now = int(time.time())
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app["app_id"])}).encode())
    sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", os.path.expanduser(app["app_key"])],
                         input=("%s.%s" % (head, body)).encode(), capture_output=True)
    if sig.returncode != 0:
        raise UsageError("openssl sign failed: " + sig.stderr.decode()[:300])
    jwt = "%s.%s.%s" % (head, body, _b64(sig.stdout))

    def call(method: str, url: str) -> dict:
        req = urllib.request.Request(url, method=method, headers={
            "Authorization": "Bearer " + jwt, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "unstrikeable"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    inst = app.get("installation_id") or call("GET", "https://api.github.com/orgs/%s/installation" % owner)["id"]
    tok = call("POST", "https://api.github.com/app/installations/%s/access_tokens" % inst)
    cache.parent.mkdir(parents=True, exist_ok=True)
    exp = time.mktime(time.strptime(tok["expires_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    cache.write_text(json.dumps({"token": tok["token"], "exp": exp}))
    os.chmod(cache, 0o600)
    return tok["token"]


def agent_token(agent: str | None, owner: str) -> str | None:
    if not agent:
        return None
    app = (load_local().get("agents") or {}).get(agent) or {}
    return app_token(app, owner) if app.get("app_key") else None    # else: the instance's `gh` auth


def make_board(co: Company, dept: Department, agent: str | None) -> Board:
    if dept.board.get("type") != "github-projects":
        raise UsageError("%s: board type %r not supported yet" % (dept.name, dept.board.get("type")))
    return GitHubBoard(dept.board, dept.flow, bots(co), token=agent_token(agent, dept.board["owner"]))


def department_of(co: Company, ref: str, name: str | None = None) -> Department:
    repo = ref.partition("#")[0]
    found = [d for d in co.departments.values() if repo in d.repos and (not name or d.name == name)]
    if not found:
        raise UsageError("no department has repo %s" % repo)
    if len(found) > 1:
        raise UsageError("%s belongs to several departments (%s): pass --department" % (
            repo, ", ".join(d.name for d in found)))
    return found[0]


# ---------------------------------------------------------------- commands
def cmd_poll(a: argparse.Namespace) -> None:
    if (home() / "PAUSE").exists():
        return
    local = load_local()
    co = load(local)
    me = co.agents.get(a.agent)
    if me is None:
        raise UsageError("agent %r is not declared in config.yml" % a.agent)
    if me.get("instance") and local.get("instance") and me["instance"] != local["instance"]:
        raise UsageError("agent %r is hosted on instance %r, not %r: refusing to poll" % (
            a.agent, me["instance"], local["instance"]))
    path = home() / "state" / ("poll-%s.json" % a.agent)
    state = json.loads(path.read_text()) if path.exists() else {}
    boards = {d.name: make_board(co, d, a.agent) for d in co.departments_of(a.agent)}
    out = poll(a.agent, co, boards, state, dry_run=a.dry_run)
    if out:
        print("Load the `unstrikeable-agent` skill and handle this event.\n\n" + out)
    if not a.dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=1))


def _target(a: argparse.Namespace) -> tuple[Company, Department, Board]:
    co = load(load_local())
    dept = department_of(co, a.ref, a.department)
    return co, dept, make_board(co, dept, a.agent)


def cmd_move(a: argparse.Namespace) -> None:
    _, dept, board = _target(a)
    if not dept.flow.has_state(a.state):
        raise UsageError("unknown state %r (states: %s)" % (a.state, ", ".join(s.key for s in dept.flow.states)))
    it = board.item(a.ref)
    if it is None:
        raise UsageError("%s is closed or not found" % a.ref)
    if a.state in ("ready", "doing") and it.blocked_by and not a.force:
        raise UsageError("%s is blocked by %d open item(s); refusing (--force if a human said so)" % (
            a.ref, it.blocked_by))
    board.move(a.ref, dept.flow.column(a.state))
    print("%s -> %s (%s)" % (a.ref, a.state, dept.flow.column(a.state)))


def cmd_status(a: argparse.Namespace) -> None:
    _, _, board = _target(a)
    it = board.item(a.ref)
    prev = None
    for c in (it.comments if it else []):
        prev = prev or (parse_status(c.body, a.agent) if c.status else None)
    now = int(time.time())
    since = prev["since"] if prev and (prev["state"] == "working" or a.state != "working") else now
    board.upsert_status(a.ref, a.agent, status_body(a.agent, a.state, since, now, a.done or "", a.todo or "",
                                                    a.note or ""))
    print("%s status %s -> %s" % (a.ref, a.agent, a.state))


def cmd_comment(a: argparse.Namespace) -> None:
    _, _, board = _target(a)
    body = Path(a.body_file).read_text() if a.body_file else (a.body or "")
    if not body.strip():
        raise UsageError("empty comment (use --body or --body-file)")
    board.comment(a.ref, body.rstrip() + "\n\n" + AGENT_MARK % a.agent)


def cmd_label(a: argparse.Namespace) -> None:
    _, dept, board = _target(a)
    for label in [*a.add, *a.remove]:
        if any(r.agent_of(label) or label in r.pool_labels() for r in dept.flow.roles.values()):
            raise UsageError("%s: assignment labels are set by humans" % label)
    board.labels(a.ref, add=a.add, remove=a.remove)


def cmd_check(a: argparse.Namespace) -> None:
    co = load(load_local())
    for d in co.departments.values():
        print("%s: flow %s, %d staff, board %s" % (d.name, d.flow.name, len(d.staff), d.board.get("type")))
    culture = co.root / "culture.md"
    if culture.exists() and len(culture.read_text().split()) > CULTURE_MAX_WORDS:
        print("warning: culture.md is long (%d words > %d): it is sent with every event" % (
            len(culture.read_text().split()), CULTURE_MAX_WORDS))


def cmd_token(a: argparse.Namespace) -> None:
    co = load(load_local())
    owner = next((d.board.get("owner") for d in co.departments_of(a.agent)), None)
    tok = agent_token(a.agent, owner or "")
    if not tok:
        raise UsageError("agent %r has no GitHub App configured on this instance" % a.agent)
    print(tok)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="uns", description="unstrikeable: agents working a ticket board")
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("poll", help="print the next event for an agent (empty = nothing)")
    p.add_argument("--agent", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_poll)

    def item_cmd(name: str, fn, help: str) -> argparse.ArgumentParser:
        p = sp.add_parser(name, help=help)
        p.add_argument("ref", help="owner/repo#123")
        p.add_argument("--agent", required=True)
        p.add_argument("--department")
        p.set_defaults(fn=fn)
        return p

    p = item_cmd("move", cmd_move, "move an item to a state (logical key)")
    p.add_argument("state")
    p.add_argument("--force", action="store_true")
    p = item_cmd("status", cmd_status, "write the agent's status comment (heartbeat)")
    p.add_argument("--state", choices=STATES, required=True)
    p.add_argument("--done")
    p.add_argument("--todo")
    p.add_argument("--note")
    p = item_cmd("comment", cmd_comment, "comment on an item, signed by the agent")
    p.add_argument("--body")
    p.add_argument("--body-file")
    p = item_cmd("label", cmd_label, "add or remove state labels (not assignments)")
    p.add_argument("--add", action="append", default=[])
    p.add_argument("--remove", action="append", default=[])

    p = sp.add_parser("check", help="validate config.yml and print the departments")
    p.set_defaults(fn=cmd_check)
    p = sp.add_parser("token", help="print a GitHub App token for the agent")
    p.add_argument("--agent", required=True)
    p.set_defaults(fn=cmd_token)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        a.fn(a)
    except (UsageError, ConfigError, GitHubError) as e:
        print("uns: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
