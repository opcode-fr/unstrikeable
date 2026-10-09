"""The `uns` CLI, with boards injected (no network)."""
import fcntl
import json
import sys
import textwrap

import pytest
from fakes import FakeBoard

from unstrikeable import cli
from unstrikeable.model import Item

CONFIG = """
    departments:
      marketing:
        flow: content
        board: {type: github-projects, owner: acme, number: 2}
        repos: [acme/mkt]
        staff:
          kevin: [planner, writer]
    agents:
      kevin: {instance: mac-mini, identity: kevin-acme}
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    repo = tmp_path / "company"
    repo.mkdir()
    (repo / "config.yml").write_text(textwrap.dedent(CONFIG))
    home = tmp_path / "home"
    home.mkdir()
    (home / "local.yml").write_text("instance: mac-mini\nconfig: %s\nagents:\n  kevin: {}\n" % repo)
    monkeypatch.setenv("UNS_HOME", str(home))
    board = FakeBoard([Item("acme/mkt", 1, "post", "ready", ["writer:kevin"])],
                      columns={"Ready to write": "ready", "Drafting": "doing"})
    monkeypatch.setattr(cli, "make_board", lambda co, dept, agent: board)
    return home, board


def test_poll_prints_the_event_and_saves_state(env, capsys):
    home, _ = env
    assert cli.main(["poll", "--agent", "kevin"]) == 0
    assert "writer.assigned" in capsys.readouterr().out
    assert json.loads((home / "state" / "poll-kevin.json").read_text())["current"]["ref"] == "acme/mkt#1"


def test_pause_file_silences_the_instance(env, capsys):
    home, _ = env
    (home / "PAUSE").touch()
    assert cli.main(["poll", "--agent", "kevin"]) == 0
    assert capsys.readouterr().out == ""


def test_poll_refuses_an_agent_hosted_elsewhere(env, capsys):
    home, _ = env
    (home / "local.yml").write_text((home / "local.yml").read_text().replace("mac-mini", "other"))
    assert cli.main(["poll", "--agent", "kevin"]) == 2
    assert "hosted on instance 'mac-mini'" in capsys.readouterr().err


def test_move_uses_logical_state_keys(env):
    _, board = env
    assert cli.main(["move", "acme/mkt#1", "doing", "--agent", "kevin"]) == 0
    assert board.calls[-1] == ("move", "acme/mkt#1", "Drafting")


def test_move_rejects_unknown_state(env, capsys):
    assert cli.main(["move", "acme/mkt#1", "flying", "--agent", "kevin"]) == 2
    assert "unknown state 'flying'" in capsys.readouterr().err


def test_move_refuses_to_start_a_blocked_item(env, capsys):
    _, board = env
    board.set("acme/mkt#1", blocked_by=1)
    assert cli.main(["move", "acme/mkt#1", "doing", "--agent", "kevin"]) == 2
    assert "blocked" in capsys.readouterr().err


def test_item_outside_every_department_is_an_error(env, capsys):
    assert cli.main(["move", "acme/other#1", "doing", "--agent", "kevin"]) == 2
    assert "no department" in capsys.readouterr().err


def test_status_writes_the_heartbeat(env):
    _, board = env
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "working", "--todo", "draft"]) == 0
    st = [c for c in board.item("acme/mkt#1").comments if c.status][0]
    assert "state=working" in st.body and "**Next**: draft" in st.body


def test_comment_is_signed_by_the_agent(env):
    _, board = env
    assert cli.main(["comment", "acme/mkt#1", "--agent", "kevin", "--body", "Draft ready"]) == 0
    assert board.calls[-1][2].endswith("<!-- uns:agent=kevin -->")


def test_label_add_and_remove(env):
    _, board = env
    assert cli.main(["label", "acme/mkt#1", "--agent", "kevin", "--add", "spec:question"]) == 0
    assert "spec:question" in board.item("acme/mkt#1").labels
    assert cli.main(["label", "acme/mkt#1", "--agent", "kevin", "--remove", "spec:question"]) == 0
    assert "spec:question" not in board.item("acme/mkt#1").labels


def test_agents_cannot_touch_assignment_labels(env, capsys):
    assert cli.main(["label", "acme/mkt#1", "--agent", "kevin", "--remove", "writer:kevin"]) == 2
    assert "assignment labels are set by humans" in capsys.readouterr().err


def test_check_warns_about_a_long_culture(env, capsys):
    home, _ = env
    repo = home.parent / "company"
    (repo / "culture.md").write_text("word " * 1000)
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "marketing: flow content, 1 staff" in out and "culture.md is long" in out


def test_hire_writes_the_sheet_in_the_config_repo(env, capsys):
    home, _ = env
    assert cli.main(["hire", "capucine", "--department", "marketing"]) == 0
    assert (home.parent / "company" / "agents" / "capucine.md").exists()
    assert "- planner" in capsys.readouterr().out


def test_hire_list_shows_presets(env, capsys):
    assert cli.main(["hire", "--list"]) == 0
    out = capsys.readouterr().out
    assert "kevin" in out and "didier" in out


def test_digest_alerts_once(env, capsys):
    home, _ = env
    (home / "state").mkdir()
    (home / "state" / "poll-kevin.json").write_text(json.dumps(
        {"alerts": [{"ts": 5, "ref": "acme/mkt#1", "msg": "lost"}]}))
    assert cli.main(["digest", "--alerts"]) == 0
    assert "acme/mkt#1 · lost" in capsys.readouterr().out
    assert cli.main(["digest", "--alerts"]) == 0
    assert capsys.readouterr().out == ""


def test_check_flags_a_runtime_outside_the_pin(env, capsys):
    home, _ = env
    cfg = home.parent / "company" / "config.yml"
    cfg.write_text('runtime: "<0.0.1"\n' + cfg.read_text())
    assert cli.main(["check"]) == 0
    assert "does NOT match" in capsys.readouterr().out


def _state(home):
    return json.loads((home / "state" / "poll-kevin.json").read_text())


def test_remember_writes_a_private_note(env, capsys):
    home, _ = env
    assert cli.main(["remember", "--agent", "kevin", "--title", "Hooks", "--body", "Two lines max"]) == 0
    notes = list((home.parent / "company" / "memory" / "agents" / "kevin").glob("*.md"))
    assert len(notes) == 1 and "Two lines max" in notes[0].read_text()


def test_remember_share_goes_to_the_inbox(env):
    home, _ = env
    assert cli.main(["remember", "--agent", "kevin", "--title", "X", "--body", "280", "--share"]) == 0
    assert list((home.parent / "company" / "memory" / "inbox" / "kevin").glob("*.md"))


def test_remember_refuses_secrets(env, capsys):
    assert cli.main(["remember", "--agent", "kevin", "--title", "t", "--body", "AKIA" + "A" * 16]) == 2
    assert "secret" in capsys.readouterr().err


def test_status_memory_is_kept_locally(env):
    home, _ = env
    assert cli.main(["status", "memory", "--agent", "kevin", "--state", "done", "--learned", "none"]) == 0
    assert _state(home)["memory_status"]["state"] == "done"


def test_pause_and_resume_an_agent(env, capsys):
    home, _ = env
    assert cli.main(["pause", "--agent", "kevin", "--reason", "too chatty"]) == 0
    assert _state(home)["paused"]["reason"] == "too chatty"
    assert cli.main(["poll", "--agent", "kevin"]) == 0
    assert "writer.assigned" not in capsys.readouterr().out
    assert cli.main(["resume", "--agent", "kevin"]) == 0
    assert cli.main(["poll", "--agent", "kevin"]) == 0
    assert "writer.assigned" in capsys.readouterr().out


def test_pause_without_agent_stops_the_whole_instance(env):
    home, _ = env
    assert cli.main(["pause"]) == 0 and (home / "PAUSE").exists()
    assert cli.main(["resume"]) == 0 and not (home / "PAUSE").exists()


def test_digest_shows_paused_agents_and_cost(env, capsys):
    home, _ = env
    (home / "state").mkdir(exist_ok=True)
    (home / "state" / "poll-kevin.json").write_text(json.dumps({
        "paused": {"ts": 1, "reason": "daily cost quota reached ($7.50 / $5.00)", "by": "quota"},
        "usage": {"ts": 1, "day": 7.5, "month": 40.0}}))
    assert cli.main(["digest"]) == 0
    out = capsys.readouterr().out
    assert "⏸️ paused: daily cost quota reached" in out and "$7.50 today" in out


def test_token_of_an_app_not_installed_is_a_clear_error(env, monkeypatch, capsys, tmp_path):
    import subprocess
    import urllib.error
    home, _ = env
    key = tmp_path / "k.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    (home / "local.yml").write_text((home / "local.yml").read_text().replace(
        "  kevin: {}", "  kevin: {app_id: 1, app_key: %s}" % key))

    def not_found(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(cli.urllib.request, "urlopen", not_found)
    assert cli.main(["token", "--agent", "kevin"]) == 2
    assert "is the App installed on acme?" in capsys.readouterr().err


# ---------------------------------------------------------------- mandatory lesson at closing
def _notes(home, folder="agents"):
    return list((home.parent / "company" / "memory" / folder / "kevin").glob("*.md"))


def test_done_requires_a_lesson(env, capsys):
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "done"]) == 2
    assert "--learned" in capsys.readouterr().err


def test_done_with_nothing_learned_writes_no_memory(env):
    home, _ = env
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "done", "--kind", "post", "--learned", "none"]) == 0
    assert _notes(home) == []


def test_done_with_a_lesson_stores_it_in_private_memory(env):
    home, board = env
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "done", "--kind", "post",
                     "--learned", "0.857 vs 0.753 is 10.4 points, not 12: name the model"]) == 0
    notes = _notes(home)
    assert len(notes) == 1 and "10.4 points" in notes[0].read_text() and "acme/mkt#1" in notes[0].read_text()
    assert [c for c in board.item("acme/mkt#1").comments if c.status]


def test_a_lesson_can_be_proposed_to_the_team(env):
    home, _ = env
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "done", "--kind", "post",
                     "--learned", "AG News is the only clean comparison", "--share-learned"]) == 0
    assert _notes(home, "inbox") and not _notes(home)


def test_working_and_blocked_need_no_lesson(env):
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "blocked", "--note", "need input"]) == 0


def test_baseline_command(env, capsys):
    home, _ = env
    assert cli.main(["baseline", "--agent", "kevin"]) == 0
    assert "1 event(s) marked as delivered" in capsys.readouterr().out
    assert cli.main(["poll", "--agent", "kevin"]) == 0
    assert capsys.readouterr().out == ""


def test_set_field(env):
    _, board = env
    assert cli.main(["set", "acme/mkt#1", "Size", "M", "--agent", "kevin"]) == 0
    assert board.calls[-1] == ("set", "acme/mkt#1", "Size", "M")


# ---------------------------------------------------------------- uns run (CLI agents: Kiro, Claude Code…)
def _recorder(tmp_path, code=0):
    """A command that stores what it reads on stdin, then exits with `code`."""
    out = tmp_path / "received.txt"
    script = "import sys; open(%r, 'w').write(sys.stdin.read()); sys.exit(%d)" % (str(out), code)
    return out, ["--", sys.executable, "-c", script]


def test_run_hands_the_event_to_the_command_on_stdin(env, tmp_path):
    home, _ = env
    out, cmd = _recorder(tmp_path)
    assert cli.main(["run", "--agent", "kevin"] + cmd) == 0
    got = out.read_text()
    assert got.startswith("Load the `unstrikeable-agent` skill") and "writer.assigned" in got
    assert json.loads((home / "state" / "poll-kevin.json").read_text())["current"]["ref"] == "acme/mkt#1"


def test_run_does_not_start_the_command_when_there_is_nothing(env, tmp_path):
    home, board = env
    board._items.clear()
    out, cmd = _recorder(tmp_path)
    assert cli.main(["run", "--agent", "kevin"] + cmd) == 0
    assert not out.exists()


def test_run_skips_the_poll_while_the_previous_turn_runs(env, tmp_path):
    home, _ = env
    out, cmd = _recorder(tmp_path)
    (home / "state").mkdir()
    with open(home / "state" / "run-kevin.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert cli.main(["run", "--agent", "kevin"] + cmd) == 0
    assert not out.exists()
    assert not (home / "state" / "poll-kevin.json").exists()      # the event was not consumed


def test_run_reports_a_failing_command(env, tmp_path, capsys):
    out, cmd = _recorder(tmp_path, code=3)
    assert cli.main(["run", "--agent", "kevin"] + cmd) == 3
    assert "exited with 3" in capsys.readouterr().err


def test_run_needs_a_command(env, capsys):
    assert cli.main(["run", "--agent", "kevin"]) == 2
    assert "command" in capsys.readouterr().err


def test_run_honours_the_pause_file(env, tmp_path):
    home, _ = env
    (home / "PAUSE").touch()
    out, cmd = _recorder(tmp_path)
    assert cli.main(["run", "--agent", "kevin"] + cmd) == 0
    assert not out.exists()


# ---------------------------------------------------------------- task kinds and records
def test_done_requires_a_kind_from_the_flow(env, capsys):
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "done", "--learned", "none"]) == 2
    err = capsys.readouterr().err
    assert "--kind" in err and "article" in err
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "done", "--learned", "none",
                     "--kind", "poem"]) == 2
    assert "unknown kind 'poem'" in capsys.readouterr().err


def test_kind_is_written_in_the_status(env):
    _, board = env
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "done", "--learned", "none",
                     "--kind", "post"]) == 0
    st = [c for c in board.item("acme/mkt#1").comments if c.status][0]
    assert "kind=post" in st.body


def test_poll_appends_closed_tasks_to_the_task_log_then_report_reads_it(env, capsys):
    home, board = env
    assert cli.main(["poll", "--agent", "kevin"]) == 0
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "done", "--learned", "none",
                     "--kind", "post"]) == 0
    assert cli.main(["poll", "--agent", "kevin"]) == 0
    capsys.readouterr()
    lines = (home / "state" / "tasks-kevin.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["kind"] == "post"
    assert "finished" not in _state(home)
    assert cli.main(["report", "--by", "agent,kind"]) == 0
    assert "kevin · post · 1 task (1 done)" in capsys.readouterr().out
    assert cli.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[0])["ref"] == "acme/mkt#1"


# ---------------------------------------------------------------- one usage reader, isolated agents
def test_usage_prints_the_bot_chat_counters(tmp_path, capsys):
    from test_meter import REAL, state_db
    db = state_db(tmp_path, REAL)
    assert cli.main(["usage", "--profile", "kevin", "--state-db", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["out"] == 8074


def test_usage_of_an_unreadable_database_fails_loudly(tmp_path, capsys):
    assert cli.main(["usage", "--profile", "kevin", "--state-db", str(tmp_path / "nope.db")]) == 2
    assert capsys.readouterr().out == ""


def _pipe(monkeypatch, text):
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def _u(out, cost):
    return json.dumps({"in": 1, "out": out, "cache_read": 10 * out, "cache_write": 0, "reasoning": 0, "cost": cost})


def test_poll_takes_the_counters_piped_by_the_reader(env, monkeypatch, capsys):
    home, _ = env
    _pipe(monkeypatch, _u(100, 1.0))
    assert cli.main(["poll", "--agent", "kevin", "--usage-from", "-"]) == 0
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "done", "--learned", "none",
                     "--kind", "post"]) == 0
    _pipe(monkeypatch, _u(160, 1.25))
    assert cli.main(["poll", "--agent", "kevin", "--usage-from", "-"]) == 0
    rec = json.loads((home / "state" / "tasks-kevin.jsonl").read_text())
    assert rec["usage"]["out"] == 60 and rec["usage"]["cost"] == 0.25


def test_task_log_goes_to_tasks_dir_readable_by_the_reader(env, tmp_path, capsys):
    import stat
    home, _ = env
    shared = tmp_path / "shared"
    (home / "local.yml").write_text((home / "local.yml").read_text() + "tasks_dir: %s\n" % shared)
    import os
    old_umask = os.umask(0o077)                     # agent accounts may run with a private umask
    try:
        assert cli.main(["poll", "--agent", "kevin"]) == 0
        assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "done", "--learned", "none",
                         "--kind", "post"]) == 0
        assert cli.main(["poll", "--agent", "kevin"]) == 0
    finally:
        os.umask(old_umask)
    log = shared / "tasks-kevin.jsonl"
    assert log.exists() and not (home / "state" / "tasks-kevin.jsonl").exists()
    assert stat.S_IMODE(log.stat().st_mode) == 0o644
    capsys.readouterr()
    assert cli.main(["report"]) == 0
    assert "kevin · post · 1 task" in capsys.readouterr().out


def test_init_needs_no_instance_and_then_checks_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("UNS_HOME", str(tmp_path / "nohome"))
    assert cli.main(["init", str(tmp_path / "hq"), "--org", "acme"]) == 0
    assert "README.md" in capsys.readouterr().out
    assert cli.main(["init", str(tmp_path / "hq"), "--org", "acme"]) == 2
    assert "not empty" in capsys.readouterr().err
    home = tmp_path / "home"
    home.mkdir()
    (home / "local.yml").write_text("instance: x\nconfig: %s\n" % (tmp_path / "hq"))
    monkeypatch.setenv("UNS_HOME", str(home))
    assert cli.main(["check"]) == 0
    assert "rnd: flow dev, 0 staff" in capsys.readouterr().out


# ------------------------------------------------------------ memory-publish
def test_only_the_curator_publishes_memory(env, capsys):
    assert cli.main(["memory-publish", "--agent", "kevin", "--summary", "x"]) == 2
    assert "only the curator" in capsys.readouterr().err


def test_memory_publish_hands_the_configured_reviewer_to_publish(env, monkeypatch, capsys):
    home, _ = env
    cfg = home.parent / "company" / "config.yml"
    cfg.write_text(cfg.read_text() + "memory:\n  curator: kevin\n")
    (home / "local.yml").write_text((home / "local.yml").read_text() + "memory_review:\n  - cat\n")
    got = {}

    def fake(root, agent, summary, review, gh, env=None, now=None):
        got.update(agent=agent, summary=summary, answer=review("SAFE\nprompt on stdin"))
        return "published"
    monkeypatch.setattr(cli, "publish", fake)
    assert cli.main(["memory-publish", "--agent", "kevin", "--summary", "kept 2"]) == 0
    assert got == {"agent": "kevin", "summary": "kept 2", "answer": "SAFE\nprompt on stdin"}
    assert "published" in capsys.readouterr().out


def test_memory_review_must_be_a_command_list():
    assert cli.reviewer(None) is None
    with pytest.raises(cli.UsageError):
        cli.reviewer("hermes -z")
    with pytest.raises(RuntimeError):
        cli.reviewer(["false"])("prompt")
