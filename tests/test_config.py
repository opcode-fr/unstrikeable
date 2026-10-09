"""Loading flows and the company config (config.yml)."""
import textwrap

import pytest

from unstrikeable.config import ConfigError, load_company, load_flow


def write(tmp_path, name, text):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text))
    return p


MINIMAL = """
    runtime: ">=0.1,<0.2"
    departments:
      marketing:
        flow: content
        board:
          type: github-projects
          owner: acme
          number: 2
        repos:
          - acme/marketing
        staff:
          kevin:
            - planner
            - writer
    agents:
      kevin:
        instance: mac-mini
        identity: kevin-acme
"""


# ---------------------------------------------------------------- shipped flows
def test_shipped_content_flow_has_its_columns_in_order():
    flow = load_flow("content")
    assert [s.column for s in flow.states] == [
        "Ideas", "Ready to write", "Drafting", "In review", "Ready to publish", "Published"]


def test_shipped_dev_flow_maps_logical_keys_to_columns():
    flow = load_flow("dev")
    assert flow.column("doing") == "In progress"
    assert flow.state_of("To merge") == "approved"


def test_unknown_column_maps_to_no_state():
    assert load_flow("dev").state_of("Somewhere else") is None


def test_unknown_flow_is_an_error():
    with pytest.raises(ConfigError, match="unknown flow 'nope'"):
        load_flow("nope")


def test_role_label_templates_are_parsed():
    writer = load_flow("content").roles["writer"]
    assert writer.labels == ["writer:{agent}"]
    assert writer.named_label("kevin") == "writer:kevin"


def test_bare_on_key_is_read_despite_yaml_boolean_quirk():
    # YAML 1.1 parses `on:` as True; the shipped flows must still have their triggers.
    assert load_flow("content").roles["writer"].on["ready"] == ["assigned"]


def test_named_label_is_parsed_back_to_its_agent():
    writer = load_flow("content").roles["writer"]
    assert writer.agent_of("writer:kevin") == "kevin"
    assert writer.agent_of("writer:") is None
    assert writer.agent_of("channel:x") is None


# ---------------------------------------------------------------- overrides
def test_overrides_rename_columns_without_touching_keys():
    flow = load_flow("dev", overrides={"columns": {"approved": "Ready to ship"}})
    assert flow.column("approved") == "Ready to ship"
    assert flow.column("doing") == "In progress"


def test_override_of_unknown_state_is_an_error():
    with pytest.raises(ConfigError, match="unknown state 'shipping'"):
        load_flow("dev", overrides={"columns": {"shipping": "Shipping"}})


def test_overrides_add_extra_labels():
    flow = load_flow("content", overrides={"labels": ["channel:x"]})
    assert "channel:x" in flow.extra_labels


def test_overrides_replace_top_level_keys_shallowly():
    flow = load_flow("content", overrides={"playbooks": {"writer.assigned": "mine.md"}})
    assert flow.playbooks == {"writer.assigned": "mine.md"}


# ---------------------------------------------------------------- flow validation
def test_flow_with_unknown_trigger_is_rejected(tmp_path):
    write(tmp_path, "flows/bad.yml", """
        states:
          - {key: ready, column: Ready}
          - {key: doing, column: Doing}
        roles:
          dev:
            label: "dev:{agent}"
            on:
              ready: [teleport]
    """)
    with pytest.raises(ConfigError, match="unknown trigger 'teleport'"):
        load_flow("bad", search=[tmp_path / "flows"])


def test_flow_with_role_on_unknown_state_is_rejected(tmp_path):
    write(tmp_path, "flows/bad.yml", """
        states:
          - {key: ready, column: Ready}
          - {key: doing, column: Doing}
        roles:
          dev:
            on:
              nowhere: [assigned]
    """)
    with pytest.raises(ConfigError, match="unknown state 'nowhere'"):
        load_flow("bad", search=[tmp_path / "flows"])


def test_config_repo_flow_wins_over_shipped_flow(tmp_path):
    write(tmp_path, "flows/content.yml", """
        states:
          - {key: ready, column: Todo}
          - {key: doing, column: Doing}
        roles: {}
    """)
    assert load_flow("content", search=[tmp_path / "flows"]).column("ready") == "Todo"


# ---------------------------------------------------------------- company config
def test_company_config_loads_departments_and_agents(tmp_path):
    write(tmp_path, "config.yml", MINIMAL)
    co = load_company(tmp_path)
    dept = co.departments["marketing"]
    assert dept.flow.column("ready") == "Ready to write"
    assert dept.staff == {"kevin": ["planner", "writer"]}
    assert dept.board == {"type": "github-projects", "owner": "acme", "number": 2}
    assert co.agents["kevin"]["identity"] == "kevin-acme"


def test_departments_of_an_agent(tmp_path):
    write(tmp_path, "config.yml", MINIMAL)
    co = load_company(tmp_path)
    assert [d.name for d in co.departments_of("kevin")] == ["marketing"]
    assert co.departments_of("nobody") == []


def test_staff_with_a_role_missing_from_the_flow_is_rejected(tmp_path):
    write(tmp_path, "config.yml", MINIMAL.replace("- writer", "- juggler"))
    with pytest.raises(ConfigError, match="marketing: kevin has role 'juggler'"):
        load_company(tmp_path)


def test_staff_member_not_declared_in_agents_is_rejected(tmp_path):
    write(tmp_path, "config.yml", MINIMAL.replace("          kevin:\n            - planner",
                                                  "          bob:\n            - planner"))
    with pytest.raises(ConfigError, match="marketing: staff 'bob' is not declared in agents"):
        load_company(tmp_path)


def test_missing_config_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="config.yml not found"):
        load_company(tmp_path)


def test_limits_have_defaults(tmp_path):
    write(tmp_path, "config.yml", MINIMAL)
    limits = load_company(tmp_path).limits
    assert limits["poll_min"] == 3
    assert limits["max_events_per_day"] == 20


def test_limits_can_be_overridden(tmp_path):
    write(tmp_path, "config.yml", MINIMAL + "    limits:\n      max_events_per_day: 10\n")
    assert load_company(tmp_path).limits["max_events_per_day"] == 10


def test_every_shipped_playbook_exists():
    from unstrikeable.poll import SHIPPED
    for name in ("dev", "content"):
        for key, path in load_flow(name).playbooks.items():
            assert (SHIPPED / path).exists(), (name, key, path)


def test_agent_limits_override_company_limits(tmp_path):
    write(tmp_path, "config.yml", MINIMAL.replace(
        "        identity: kevin-acme\n",
        "        identity: kevin-acme\n        limits:\n          max_cost_per_day: 30\n") +
        "    limits:\n      max_cost_per_day: 5\n      max_events_per_day: 10\n")
    co = load_company(tmp_path)
    assert co.limits_for("kevin")["max_cost_per_day"] == 30
    assert co.limits_for("kevin")["max_events_per_day"] == 10
    assert co.limits_for("nobody")["max_cost_per_day"] == 5


# ---------------------------------------------------------------- task kinds (classification for reports)
def test_shipped_flows_declare_a_closed_list_of_kinds():
    assert "bug" in load_flow("dev").kinds and "other" in load_flow("dev").kinds
    assert "article" in load_flow("content").kinds


def test_kinds_can_be_replaced_by_the_department():
    assert load_flow("dev", {"kinds": ["spike", "fix"]}).kinds == ["spike", "fix"]


@pytest.mark.parametrize("kinds", [["bug", "bug"], ["Bug Fix"], "bug"])
def test_invalid_kinds_are_rejected(kinds):
    with pytest.raises(ConfigError, match="kinds"):
        load_flow("dev", {"kinds": kinds})


# ---------------------------------------------------------------- trusted authors
TRUSTING = MINIMAL.replace("        staff:\n", "        trusted_authors:\n          - brice\n        staff:\n")


def test_trusted_authors_are_read_per_department(tmp_path):
    write(tmp_path, "config.yml", TRUSTING)
    assert load_company(tmp_path).departments["marketing"].trusted_authors == ["brice"]


@pytest.mark.parametrize("who", ["kevin", "kevin-acme", "Kevin-Acme[bot]", "dependabot[bot]"])
def test_trusted_authors_are_humans_only(tmp_path, who):
    write(tmp_path, "config.yml", TRUSTING.replace("- brice", "- %s" % who))
    with pytest.raises(ConfigError, match="humans only"):
        load_company(tmp_path)
