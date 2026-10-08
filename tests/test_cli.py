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
