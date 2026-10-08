"""The `uns` CLI, with boards injected (no network)."""
import json
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
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "done", "--learned", "none"]) == 0
    assert _notes(home) == []


def test_done_with_a_lesson_stores_it_in_private_memory(env):
    home, board = env
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "done",
                     "--learned", "0.857 vs 0.753 is 10.4 points, not 12: name the model"]) == 0
    notes = _notes(home)
    assert len(notes) == 1 and "10.4 points" in notes[0].read_text() and "acme/mkt#1" in notes[0].read_text()
    assert [c for c in board.item("acme/mkt#1").comments if c.status]


def test_a_lesson_can_be_proposed_to_the_team(env):
    home, _ = env
    assert cli.main(["status", "acme/mkt#1", "--agent", "kevin", "--state", "done",
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
