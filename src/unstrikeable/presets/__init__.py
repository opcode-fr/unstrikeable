"""Agent presets: ready-made agent sheets (personality + suggested roles) to hire into a company."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..config import ConfigError

HERE = Path(__file__).parent


@dataclass(frozen=True)
class Preset:
    name: str
    summary: str
    roles: dict[str, list[str]]        # flow -> suggested roles
    capabilities: list[str]
    body: str                          # personality; "{name}" is replaced by the hired name
    display: str = ""                  # how the preset writes its own name (e.g. JeanMichel)


def _parse(path: Path) -> Preset:
    text = path.read_text()
    _, front, body = text.split("---\n", 2)
    meta = yaml.safe_load(front) or {}
    return Preset(path.stem, meta.get("summary", ""), meta.get("roles") or {},
                  list(meta.get("capabilities") or []), body.strip(), meta.get("display", ""))


def list_presets() -> list[Preset]:
    return [_parse(p) for p in sorted(HERE.glob("*.md"))]


def load_preset(name: str) -> Preset:
    path = HERE / ("%s.md" % name)
    if not path.exists():
        raise ConfigError("unknown preset %r (available: %s)" % (name, ", ".join(p.name for p in list_presets())))
    return _parse(path)


def hire(preset: str, root: Path, name: str | None = None, department: str | None = None) -> tuple[Path, str]:
    """Write agents/<name>.md in the config repo from a preset. Returns (path, config.yml snippet to add)."""
    p = load_preset(preset)
    name = name or p.name
    path = Path(root) / "agents" / ("%s.md" % name)
    if path.exists():
        raise ConfigError("agents/%s.md already exists: pick another name (--as)" % name)
    path.parent.mkdir(parents=True, exist_ok=True)
    shown = p.display if (p.display and name == p.name) else name.capitalize()
    body = p.body.replace("{name}", shown)
    path.write_text("---\n# Plain sentences about what THIS agent really has: accounts, machines, tokens.\n"
                    "# A capability is a claim, not a permission: the credentials live in local.yml.\n"
                    "capabilities: []\n---\n%s\n" % body)
    flow, roles = next(iter(p.roles.items()))
    snippet = "\n".join([
        "agents:",
        "  %s:" % name,
        "    instance: <instance>",
        "    identity: %s-<org>            # GitHub App slug, see `uns app form`" % name,
        "departments:",
        "  %s:                             # a department running the %s flow" % (department or "<department>", flow),
        "    staff:",
        "      %s:" % name,
        *["        - %s" % r for r in roles],
    ])
    return path, snippet
