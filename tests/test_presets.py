"""Agent presets: ready-made agent sheets to hire into a company."""
import pytest

from unstrikeable.config import ConfigError
from unstrikeable.presets import hire, list_presets, load_preset


def test_shipped_presets():
    names = [p.name for p in list_presets()]
    assert names == sorted(names)
    assert {"kevin", "jeanmichel", "didier", "capucine", "brandon"} <= set(names)
    assert "gerard" not in names


def test_every_preset_suggests_roles_that_exist_in_its_flows():
    from unstrikeable.config import load_flow
    for p in list_presets():
        assert p.roles, p.name
        for flow, roles in p.roles.items():
            assert set(roles) <= set(load_flow(flow).roles), (p.name, flow)
        assert p.summary and p.body


def test_preset_ships_no_capability_by_default():
    # capabilities are claims about a real instance: the human adds them after hiring
    assert all(p.capabilities == [] for p in list_presets())


def test_hire_writes_the_agent_sheet_under_its_new_name(tmp_path):
    path, snippet = hire("kevin", tmp_path, name="kev", department="marketing")
    assert path == tmp_path / "agents" / "kev.md"
    text = path.read_text()
    assert text.startswith("---\n") and "capabilities: []" in text and "kevin" not in text.split("---")[1]
    assert "kev:" in snippet and "- writer" in snippet and "marketing" in snippet


def test_hire_never_overwrites_an_existing_agent(tmp_path):
    hire("kevin", tmp_path)
    with pytest.raises(ConfigError, match="agents/kevin.md already exists"):
        hire("kevin", tmp_path)


def test_unknown_preset_lists_the_available_ones():
    with pytest.raises(ConfigError, match="unknown preset 'gerard'.*kevin"):
        load_preset("gerard")


def test_preset_keeps_its_own_spelling_of_its_name(tmp_path):
    path, _ = hire("jeanmichel", tmp_path)
    assert "JeanMichel is the developer" in path.read_text()
