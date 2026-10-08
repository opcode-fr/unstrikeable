"""Flows (templates) and the company config (config.yml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SHIPPED_FLOWS = Path(__file__).parent / "flows"

TRIGGERS = frozenset({
    "item_new",        # item in the state, never handled by this role
    "human_comment",   # a human member spoke last, after this agent
    "new_comment",     # anyone trusted (human or other agent) spoke last, after this agent
    "assigned",        # role label set for this agent, item not blocked, agent idle
    "pr_updated",      # head commit of the linked PR changed
    "pr_conflict",     # linked PR not mergeable
    "ci_failed",       # CI red on the linked PR
})

DEFAULT_MEMORY = {
    "curator": None,          # agent that consolidates memory/inbox into memory/shared (via a PR)
    "inbox_max": 10,          # curate once the inbox holds this many new entries…
    "max_age_h": 24,          # …or once the oldest one is this old
    "shared_max_words": 3000,
    "private_max_words": 1500,
}

DEFAULT_LIMITS = {
    "poll_min": 3,
    "max_events_per_day": 20,
    "max_runs": 10,
    "max_review_rounds": 3,
    "ack_min": 15,
    "stale_min": 45,
}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class State:
    key: str
    column: str


@dataclass(frozen=True)
class Role:
    name: str
    labels: list[str] = field(default_factory=list)   # templates, "{agent}" = named, else pool
    on: dict[str, list[str]] = field(default_factory=dict)
    human: bool = False
    auto: bool = False                                 # unlabelled item: first staff member with the role

    @property
    def labelled(self) -> bool:
        return bool(self.labels)

    def named_label(self, agent: str) -> str | None:
        for t in self.labels:
            if "{agent}" in t:
                return t.replace("{agent}", agent)
        return None

    def pool_labels(self) -> list[str]:
        return [t for t in self.labels if "{agent}" not in t]

    def agent_of(self, label: str) -> str | None:
        """'writer:kevin' -> 'kevin' for a named template 'writer:{agent}'."""
        for t in self.labels:
            if "{agent}" in t:
                pre, post = t.split("{agent}", 1)
                if label.startswith(pre) and label.endswith(post) and len(label) > len(pre) + len(post):
                    return label[len(pre):len(label) - len(post)]
        return None


@dataclass(frozen=True)
class Flow:
    name: str
    states: list[State]
    roles: dict[str, Role]
    playbooks: dict[str, str] = field(default_factory=dict)
    extra_labels: list[str] = field(default_factory=list)
    artifact: dict[str, Any] = field(default_factory=dict)

    def column(self, key: str) -> str:
        for s in self.states:
            if s.key == key:
                return s.column
        raise ConfigError("flow %s: unknown state %r" % (self.name, key))

    def state_of(self, column: str | None) -> str | None:
        for s in self.states:
            if column is not None and s.column.lower() == column.lower():
                return s.key
        return None

    def has_state(self, key: str) -> bool:
        return any(s.key == key for s in self.states)


@dataclass(frozen=True)
class Department:
    name: str
    flow: Flow
    board: dict[str, Any]
    repos: list[str]
    staff: dict[str, list[str]]

    def staff_with(self, role: str) -> list[str]:
        return [a for a, roles in self.staff.items() if role in roles]


@dataclass(frozen=True)
class Company:
    root: Path
    departments: dict[str, Department]
    agents: dict[str, dict[str, Any]]
    limits: dict[str, Any]
    forge: dict[str, Any] | None = None
    runtime: str = ""                                # version pin of the runtime
    memory: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_MEMORY))

    def limits_for(self, agent: str) -> dict[str, Any]:
        """Company limits, overridden by `agents.<a>.limits` (budgets and cost quotas differ per agent)."""
        return {**self.limits, **((self.agents.get(agent) or {}).get("limits") or {})}

    def departments_of(self, agent: str) -> list[Department]:
        return [d for d in self.departments.values() if agent in d.staff]


# ---------------------------------------------------------------- loading
def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError("%s: invalid YAML: %s" % (path, e)) from e
    if not isinstance(data, dict):
        raise ConfigError("%s: expected a mapping at top level" % path)
    return data


def load_flow(name: str, overrides: dict | None = None, search: list[Path] | None = None) -> Flow:
    """Find flows/<name>.yml (config repo dirs first, then shipped), apply overrides, validate."""
    for d in [*(search or []), SHIPPED_FLOWS]:
        path = Path(d) / ("%s.yml" % name)
        if path.exists():
            raw = _read_yaml(path)
            break
    else:
        raise ConfigError("unknown flow %r" % name)

    overrides = dict(overrides or {})
    columns = overrides.pop("columns", {}) or {}
    extra = overrides.pop("labels", []) or []
    raw = {**raw, **overrides}                         # shallow merge, by design

    states = [State(key=s["key"], column=s["column"]) for s in raw.get("states") or []]
    keys = {s.key for s in states}
    if len(keys) != len(states):
        raise ConfigError("flow %s: duplicate state keys" % name)
    for k, col in columns.items():
        if k not in keys:
            raise ConfigError("flow %s: override of unknown state %r" % (name, k))
    states = [State(s.key, columns.get(s.key, s.column)) for s in states]

    roles = {}
    for rname, r in (raw.get("roles") or {}).items():
        r = r or {}
        labels = r.get("label") or []
        if isinstance(labels, str):
            labels = [labels]
        # YAML 1.1 reads a bare `on:` key as the boolean True (same quirk as GitHub Actions).
        on_raw = r.get("on", r.get(True)) or {}
        on = {st: list(trs or []) for st, trs in on_raw.items()}
        for st, trs in on.items():
            if st not in keys:
                raise ConfigError("flow %s: role %s reacts in unknown state %r" % (name, rname, st))
            for t in trs:
                if t not in TRIGGERS:
                    raise ConfigError("flow %s: role %s: unknown trigger %r" % (name, rname, t))
        roles[rname] = Role(rname, list(labels), on, bool(r.get("human")), bool(r.get("auto")))

    return Flow(name, states, roles, dict(raw.get("playbooks") or {}),
                list(raw.get("extra_labels") or []) + list(extra), dict(raw.get("artifact") or {}))


def load_company(root: Path | str) -> Company:
    root = Path(root)
    path = root / "config.yml"
    if not path.exists():
        raise ConfigError("config.yml not found in %s" % root)
    raw = _read_yaml(path)
    agents = raw.get("agents") or {}
    depts = {}
    for name, d in (raw.get("departments") or {}).items():
        if not d.get("flow"):
            raise ConfigError("%s: missing flow" % name)
        flow = load_flow(d["flow"], d.get("overrides"), search=[root / "flows"])
        staff = {a: list(roles or []) for a, roles in (d.get("staff") or {}).items()}
        for a, roles in staff.items():
            if a not in agents:
                raise ConfigError("%s: staff %r is not declared in agents" % (name, a))
            for r in roles:
                if r not in flow.roles:
                    raise ConfigError("%s: %s has role %r, not in flow %s" % (name, a, r, flow.name))
        depts[name] = Department(name, flow, dict(d.get("board") or {}), list(d.get("repos") or []), staff)
    return Company(root, depts, agents, {**DEFAULT_LIMITS, **(raw.get("limits") or {})}, raw.get("forge"),
                   str(raw.get("runtime") or ""), {**DEFAULT_MEMORY, **(raw.get("memory") or {})})
