"""`uns`: the command line used by cron wrappers (poll) and by agents (move, status, comment, label)."""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from . import admin
from .backends.base import Board
from .backends.github import GitHubBoard, GitHubError
from .config import Company, ConfigError, Department, load_company
from .digest import digest
from .memory import commit_and_push, pull, write_entry
from .meter import hermes_state_db, make_usage, parse_usage, read_bot_chat_usage
from .model import AGENT_MARK
from .poll import MEMORY_REF, baseline, poll
from .presets import hire, list_presets
from .report import report
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

    try:
        inst = app.get("installation_id") or call("GET", "https://api.github.com/orgs/%s/installation" % owner)["id"]
        tok = call("POST", "https://api.github.com/app/installations/%s/access_tokens" % inst)
    except urllib.error.HTTPError as e:
        raise UsageError("GitHub App %s: HTTP %d (is the App installed on %s?)" % (app["app_id"], e.code, owner)) from e
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
def next_event(agent: str, dry_run: bool = False, piped_usage: str | None = None) -> str:
    """The text to hand to the agent ('' = nothing). Consumes the event unless dry_run.
    piped_usage: counters sent by the single reader of the Hermes database (isolated agent accounts); it wins
    over a local meter. Invalid or empty = unknown cost."""
    if (home() / "PAUSE").exists():
        return ""
    local = load_local()
    if local.get("config"):
        pull(Path(os.path.expanduser(local["config"])))
    co = load(local)
    me = co.agents.get(agent)
    if me is None:
        raise UsageError("agent %r is not declared in config.yml" % agent)
    if me.get("instance") and local.get("instance") and me["instance"] != local["instance"]:
        raise UsageError("agent %r is hosted on instance %r, not %r: refusing to poll" % (
            agent, me["instance"], local["instance"]))
    state = read_state(agent)
    boards = {d.name: make_board(co, d, agent) for d in co.departments_of(agent)}
    mcfg = ((local.get("agents") or {}).get(agent) or {}).get("meter")
    usage = make_usage(mcfg)
    if piped_usage is not None:
        counters = parse_usage(piped_usage)
        usage = lambda: counters                                       # noqa: E731
    out = poll(agent, co, boards, state, dry_run=dry_run, usage=usage)
    if not dry_run:
        append_tasks(agent, state.pop("finished", None) or [], local)
        write_state(agent, state)
    return "Load the `unstrikeable-agent` skill and handle this event.\n\n" + out if out else ""


def cmd_poll(a: argparse.Namespace) -> None:
    piped = sys.stdin.read() if a.usage_from == "-" else None
    out = next_event(a.agent, a.dry_run, piped)
    if out:
        print(out)


def cmd_run(a: argparse.Namespace) -> int:
    """Adapter for CLI agents (Kiro, Claude Code…): poll, then hand the event to `command` on stdin.
    A lock per agent skips the poll while the previous turn still runs (the event stays on the board)."""
    command = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not command:
        raise UsageError("run: give the agent command after --, e.g. uns run --agent a -- kiro-cli chat …")
    lock = state_path(a.agent).with_name("run-%s.lock" % a.agent)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0                                   # previous turn still running
        out = next_event(a.agent)
        if not out:
            return 0
        rc = subprocess.run(command, input=out, text=True).returncode
    if rc:
        print("uns: %s exited with %d (the event was delivered; the agent gets nudged if it did not report)" % (
            command[0], rc), file=sys.stderr)
    return rc


def state_path(agent: str) -> Path:
    return home() / "state" / ("poll-%s.json" % agent)


def read_state(agent: str) -> dict:
    p = state_path(agent)
    return json.loads(p.read_text()) if p.exists() else {}


def tasks_dir(local: dict) -> Path:
    """Where task logs go. `tasks_dir` in local.yml: a directory shared by the agents' accounts (sticky, like
    /tmp), so one `uns report` reads every agent; each log stays owned by its agent and only readable by others."""
    return Path(os.path.expanduser(local["tasks_dir"])) if local.get("tasks_dir") else home() / "state"


def append_tasks(agent: str, records: list[dict], local: dict) -> None:
    """Closed tasks, one JSON line each: append-only, the source of `uns report`."""
    if not records:
        return
    d = tasks_dir(local)
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("tasks-%s.jsonl" % agent)
    with open(p, "a") as fh:
        fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    os.chmod(p, 0o644)                                                  # the reader's account must read it


def write_state(agent: str, state: dict) -> None:
    p = state_path(agent)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1))


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


NOTHING_LEARNED = {"none", "nothing", "-"}


def cmd_status(a: argparse.Namespace) -> None:
    if a.state == "done" and not (a.learned or "").strip():
        raise UsageError('closing a task needs a lesson: --learned "<rule> because <reason>" (or --learned none)')
    _write_status(a)
    lesson = (a.learned or "").strip()
    if a.state == "done" and lesson.lower() not in NOTHING_LEARNED:
        co = load(load_local())
        path = _remember(co, a.agent, "Learned on %s: %s" % (a.ref, lesson[:50]),
                         "%s\n\nSource: %s" % (lesson, a.ref), share=a.share_learned)
        print("lesson %s %s" % ("proposed to the team" if a.share_learned else "noted", path.relative_to(co.root)))


def _write_status(a: argparse.Namespace) -> None:
    if a.ref == MEMORY_REF:                       # curation task: no board item, the status lives in the state
        state = read_state(a.agent)
        prev = state.get("memory_status") or {}
        now = int(time.time())
        since = prev.get("since", now) if prev.get("state") == "working" or a.state != "working" else now
        state["memory_status"] = {"state": a.state, "since": since, "beat": now}
        write_state(a.agent, state)
        print("memory status %s -> %s" % (a.agent, a.state))
        return
    _, dept, board = _target(a)
    kinds = dept.flow.kinds
    if a.kind and a.kind not in kinds:
        raise UsageError("unknown kind %r (flow %s: %s)" % (a.kind, dept.flow.name, ", ".join(kinds) or "no kinds"))
    if a.state == "done" and kinds and not a.kind:
        raise UsageError("closing a task needs its kind: --kind <%s>" % "|".join(kinds))
    it = board.item(a.ref)
    prev = None
    for c in (it.comments if it else []):
        prev = prev or (parse_status(c.body, a.agent) if c.status else None)
    now = int(time.time())
    since = prev["since"] if prev and (prev["state"] == "working" or a.state != "working") else now
    board.upsert_status(a.ref, a.agent, status_body(a.agent, a.state, since, now, a.done or "", a.todo or "",
                                                    a.note or "", kind=a.kind or ""))
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
    if co.runtime:
        v = admin.installed_version()
        print("runtime %s %s %s" % (v, "matches" if admin.version_ok(v, co.runtime) else "does NOT match", co.runtime))
    culture = co.root / "culture.md"
    if culture.exists() and len(culture.read_text().split()) > CULTURE_MAX_WORDS:
        print("warning: culture.md is long (%d words > %d): it is sent with every event" % (
            len(culture.read_text().split()), CULTURE_MAX_WORDS))


def cmd_layout(a: argparse.Namespace) -> None:
    co = load(load_local())
    depts = [co.departments[a.department]] if a.department else list(co.departments.values())
    print("mode: %s" % ("APPLY" if a.apply else "dry-run (nothing is changed)"))
    for d in depts:
        plan = make_board(co, d, None).ensure_layout(d, apply=a.apply, prune=a.prune)
        print("## %s" % d.name)
        print("\n".join("  " + line for line in plan) if plan else "  = OK")


def cmd_hire(a: argparse.Namespace) -> None:
    if a.list or not a.preset:
        for p in list_presets():
            print("%-10s %s  (%s)" % (p.name, p.summary, "; ".join(
                "%s: %s" % (f, ", ".join(r)) for f, r in p.roles.items())))
        return
    co = load(load_local())
    path, snippet = hire(a.preset, co.root, name=a.as_name, department=a.department)
    print("wrote %s (add its real capabilities)\nadd to config.yml:\n%s" % (path, snippet))


def cmd_digest(a: argparse.Namespace) -> None:
    local = load_local()
    states = {}
    for agent in sorted(local.get("agents") or {}):
        p = home() / "state" / ("poll-%s.json" % agent)
        states[agent] = json.loads(p.read_text()) if p.exists() else {}
    cursor_path = home() / "state" / "digest-cursor.json"
    cursor = json.loads(cursor_path.read_text())["ts"] if cursor_path.exists() else 0
    text, new = digest(states, cursor, time.strftime("%Y-%m-%d"), a.alerts, local.get("instance", "?"))
    if text:
        print(text)
    if new != cursor and not a.dry_run:
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        cursor_path.write_text(json.dumps({"ts": new}))


def cmd_report(a: argparse.Namespace) -> None:
    records = []
    for p in sorted(tasks_dir(load_local()).glob("tasks-*.jsonl")):
        records += [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    records = [r for r in records if not a.agent or r.get("agent") == a.agent]
    if a.json:
        since = time.time() - a.days * 86400
        for r in records:
            if (r.get("end") or 0) >= since:
                print(json.dumps(r, ensure_ascii=False))
        return
    try:
        print(report(records, [k.strip() for k in a.by.split(",") if k.strip()], int(time.time()), a.days))
    except ValueError as e:
        raise UsageError(str(e)) from e


def cmd_usage(a: argparse.Namespace) -> None:
    """The single reader: Bot Chat counters of a Hermes profile, as JSON, for `uns poll --usage-from -`."""
    db = Path(os.path.expanduser(a.state_db)) if a.state_db else hermes_state_db(a.profile)
    try:
        print(json.dumps(read_bot_chat_usage(db)))
    except Exception as e:
        raise UsageError("cannot read %s: %s" % (db, e)) from e


def cmd_app(a: argparse.Namespace) -> None:
    if a.action == "form":
        out = admin.app_form(a.org, a.agent, Path(a.out or "app-%s-%s.html" % (a.agent, a.org)))
        print("open %s in a browser logged in as an owner of %s" % (out.resolve(), a.org))
    else:
        info = admin.app_exchange(a.code, a.agent, home())
        print(json.dumps(info, indent=1))
        print("then: install the App (install_url) on the department repos, and in local.yml:\n"
              "  %s: {app_id: %s, app_key: %s}" % (a.agent, info["app_id"], info["app_key"]))


def cmd_update(a: argparse.Namespace) -> None:
    local = load_local()
    print(admin.update(local, load(local).runtime))


def _remember(co: Company, agent: str, title: str, body: str, share: bool) -> Path:
    try:
        path = write_entry(co.root, agent, title, body, share=share)
    except ValueError as e:
        raise UsageError(str(e)) from e
    if (co.root / ".git").exists():
        owner = next((d.board.get("owner") for d in co.departments_of(agent)), "")
        token = agent_token(agent, owner) if owner else None
        env = {**os.environ, "GH_TOKEN": token} if token else None     # push as the agent's App
        commit_and_push(co.root, [path], "memory(%s): %s" % (agent, title), env=env)
    return path


def cmd_remember(a: argparse.Namespace) -> None:
    co = load(load_local())
    body = Path(a.body_file).read_text() if a.body_file else (a.body or "")
    if not body.strip():
        raise UsageError("empty note (use --body or --body-file)")
    path = _remember(co, a.agent, a.title, body, a.share)
    print("%s %s" % ("proposed to the team" if a.share else "noted", path.relative_to(co.root)))


def cmd_pause(a: argparse.Namespace) -> None:
    if not a.agent:
        home().mkdir(parents=True, exist_ok=True)
        (home() / "PAUSE").write_text(a.reason or "")
        print("instance paused: no agent receives anything until `uns resume`")
        return
    state = read_state(a.agent)
    state["paused"] = {"ts": int(time.time()), "reason": a.reason or "manual", "by": "human"}
    write_state(a.agent, state)
    print("%s paused" % a.agent)


def cmd_resume(a: argparse.Namespace) -> None:
    if not a.agent:
        (home() / "PAUSE").unlink(missing_ok=True)
        print("instance resumed")
        return
    state = read_state(a.agent)
    state.pop("paused", None)
    state.pop("usage", None)                      # re-read the meter now
    write_state(a.agent, state)
    print("%s resumed" % a.agent)


def cmd_baseline(a: argparse.Namespace) -> None:
    co = load(load_local())
    state = read_state(a.agent)
    boards = {d.name: make_board(co, d, a.agent) for d in co.departments_of(a.agent)}
    n = baseline(a.agent, co, boards, state)
    write_state(a.agent, state)
    print("%d event(s) marked as delivered for %s (nothing sent)" % (n, a.agent))


def cmd_set(a: argparse.Namespace) -> None:
    _, _, board = _target(a)
    if a.field.lower() == "status":
        raise UsageError("use `uns move` for the status (logical states)")
    board.set_field(a.ref, a.field, a.value)
    print("%s %s -> %s" % (a.ref, a.field, a.value))


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
    p.add_argument("--usage-from", choices=["-"], help="read usage counters on stdin (from `uns usage`)")
    p.set_defaults(fn=cmd_poll)
    p = sp.add_parser("run", help="poll, then hand the event on stdin to a CLI agent: uns run --agent a -- <cmd>")
    p.add_argument("--agent", required=True)
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_run)

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
    p = item_cmd("set", cmd_set, "set a single-select field of the board (Size, Priority…)")
    p.add_argument("field")
    p.add_argument("value")
    p = item_cmd("status", cmd_status, "write the agent's status comment (heartbeat)")
    p.add_argument("--state", choices=STATES, required=True)
    p.add_argument("--done")
    p.add_argument("--todo")
    p.add_argument("--note")
    p.add_argument("--learned", help='required with --state done: "<rule> because <reason>", or "none"')
    p.add_argument("--kind", help="nature of the task, from the flow's `kinds` (required with --state done)")
    p.add_argument("--share-learned", action="store_true", help="propose the lesson to the team (curator)")
    p = item_cmd("comment", cmd_comment, "comment on an item, signed by the agent")
    p.add_argument("--body")
    p.add_argument("--body-file")
    p = item_cmd("label", cmd_label, "add or remove state labels (not assignments)")
    p.add_argument("--add", action="append", default=[])
    p.add_argument("--remove", action="append", default=[])

    p = sp.add_parser("check", help="validate config.yml and print the departments")
    p.set_defaults(fn=cmd_check)
    p = sp.add_parser("layout", help="create the columns and labels the departments need (dry-run by default)")
    p.add_argument("--department")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--prune", action="store_true", help="also remove columns outside the flow (refused if items sit there)")
    p.set_defaults(fn=cmd_layout)
    p = sp.add_parser("hire", help="add an agent to the company from a preset (--list to see them)")
    p.add_argument("preset", nargs="?")
    p.add_argument("--as", dest="as_name")
    p.add_argument("--department")
    p.add_argument("--list", action="store_true")
    p.set_defaults(fn=cmd_hire)
    p = sp.add_parser("digest", help="human summary for Slack (--alerts: only new alerts, silent otherwise)")
    p.add_argument("--alerts", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_digest)
    p = sp.add_parser("report", help="closed tasks: count, outcome, time, tokens, cost, grouped (--by agent,kind)")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--agent")
    p.add_argument("--by", default="agent,kind", help="comma list of: agent, kind, role, trigger, department, "
                                                       "flow, outcome")
    p.add_argument("--json", action="store_true", help="raw records, one JSON per line")
    p.set_defaults(fn=cmd_report)
    p = sp.add_parser("usage", help="Bot Chat counters of a Hermes profile (JSON), piped to an isolated agent's poll")
    p.add_argument("--profile", required=True)
    p.add_argument("--state-db", help="path of state.db (default: $HERMES_HOME profile)")
    p.set_defaults(fn=cmd_usage)
    p = sp.add_parser("app", help="create an agent's GitHub App: `form` (owner clicks), then `exchange CODE`")
    asp = p.add_subparsers(dest="action", required=True)
    f = asp.add_parser("form")
    f.add_argument("--org", required=True)
    f.add_argument("--agent", required=True)
    f.add_argument("--out")
    e = asp.add_parser("exchange")
    e.add_argument("code")
    e.add_argument("--agent", required=True)
    p.set_defaults(fn=cmd_app)
    p = sp.add_parser("update", help="upgrade the runtime, reinstall skills, dry-run every hosted agent")
    p.set_defaults(fn=cmd_update)
    p = sp.add_parser("remember", help="write a memory note (--share: propose it to the team via the curator)")
    p.add_argument("--agent", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--share", action="store_true")
    p.set_defaults(fn=cmd_remember)
    p = sp.add_parser("pause", help="kill switch: one agent (--agent) or the whole instance")
    p.add_argument("--agent")
    p.add_argument("--reason")
    p.set_defaults(fn=cmd_pause)
    p = sp.add_parser("resume", help="lift a pause (manual or quota)")
    p.add_argument("--agent")
    p.set_defaults(fn=cmd_resume)
    p = sp.add_parser("baseline", help="migration: mark current events as delivered, send nothing")
    p.add_argument("--agent", required=True)
    p.set_defaults(fn=cmd_baseline)
    p = sp.add_parser("token", help="print a GitHub App token for the agent")
    p.add_argument("--agent", required=True)
    p.set_defaults(fn=cmd_token)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        return a.fn(a) or 0
    except (UsageError, ConfigError, GitHubError) as e:
        print("uns: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
