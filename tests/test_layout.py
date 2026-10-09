"""Board layout: the columns and labels a department needs (pure planning)."""
from unstrikeable.config import Department, load_flow
from unstrikeable.layout import expected_labels, plan_columns


def test_missing_columns_are_added_and_existing_ids_kept():
    have = [{"id": "a", "name": "Todo", "color": "GRAY", "description": ""},
            {"id": "b", "name": "Ideas", "color": "BLUE", "description": "x"}]
    opts, actions = plan_columns(have, ["Ideas", "Drafting"])
    assert opts[:2] == [{"id": "b", "name": "Ideas", "color": "BLUE", "description": "x"},
                        {"name": "Drafting", "color": "GRAY", "description": ""}]
    assert "+ column 'Drafting'" in actions


def test_extra_columns_are_kept_at_the_end_so_items_keep_their_status():
    have = [{"id": "a", "name": "Todo", "color": "GRAY", "description": ""}]
    opts, actions = plan_columns(have, ["Ideas"])
    assert [o["name"] for o in opts] == ["Ideas", "Todo"] and opts[1]["id"] == "a"
    assert "! column 'Todo' not in the flow (kept)" in actions


def test_matching_columns_need_nothing():
    have = [{"id": "a", "name": "Ideas", "color": "GRAY", "description": ""}]
    assert plan_columns(have, ["ideas"]) == (None, [])


def test_wrong_order_is_fixed():
    have = [{"id": "b", "name": "B", "color": "GRAY", "description": ""},
            {"id": "a", "name": "A", "color": "GRAY", "description": ""}]
    opts, actions = plan_columns(have, ["A", "B"])
    assert [o["id"] for o in opts] == ["a", "b"] and "~ reorder columns" in actions


def test_expected_labels_cover_staff_system_and_extra_labels():
    flow = load_flow("content", overrides={"labels": ["channel:x"]})
    d = Department("marketing", flow, {}, ["acme/mkt"], {"kevin": ["planner", "writer"]})
    names = set(expected_labels(d))
    assert {"writer:kevin", "spec:question", "needs:human", "agent:pause", "agent:lost", "needs:vetting",
            "channel:x"} <= names
    assert not any(n.startswith("planner") for n in names)          # unlabelled role


def test_prune_drops_columns_outside_the_flow():
    have = [{"id": "a", "name": "Todo", "color": "GRAY", "description": ""}]
    opts, actions = plan_columns(have, ["Ideas"], prune=True)
    assert [o["name"] for o in opts] == ["Ideas"] and "- column 'Todo' (pruned)" in actions


def test_label_colour_follows_the_prefix_role_colours_differ_and_overrides_add_extras():
    flow = load_flow("dev", overrides={"labels": ["type:bench", "channel:x", "misc"],
                                       "label_colors": {"type": "C5DEF5", "channel": "bfdadc"}})
    d = Department("rnd", flow, {}, ["acme/x"], {"jm": ["dev"], "kiki": ["dev"], "didier": ["review"]})
    want = expected_labels(d)
    assert want["dev:jm"][0] == want["dev:kiki"][0] == "1d76db"         # one colour per role, not per agent
    assert want["review:didier"][0] == "8250df"
    assert want["type:bench"][0] == "c5def5" and want["channel:x"][0] == "bfdadc"
    assert want["misc"][0] == "c5def5"                                      # undeclared prefix: default pastel
    assert want["agent:pause"][0] == "b60205"                               # system labels keep theirs


def test_override_colours_extend_the_flow_ones():
    flow = load_flow("content", overrides={"label_colors": {"channel": "bfdadc"}})
    d = Department("mkt", flow, {}, ["acme/m"], {"kevin": ["writer"]})
    assert expected_labels(d)["writer:kevin"][0] == "006b75"
