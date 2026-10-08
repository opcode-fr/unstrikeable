# unstrikeable

> *The company that never goes on strike.*

A team of AI agents working a ticket board: a flow declared in YAML, humans who assign and approve,
and guardrails built in (one task at a time, budgets, heartbeat, kill switches).

- Design: [docs/design.md](docs/design.md)
- Flows shipped: [`dev`](src/unstrikeable/flows/dev.yml) (spec → code → review → merge) and
  [`content`](src/unstrikeable/flows/content.yml) (idea → brief → draft → review → publish).
- Skills: [`unstrikeable-agent`](src/unstrikeable/skills/unstrikeable-agent/SKILL.md) (agents) and
  [`unstrikeable-admin`](src/unstrikeable/skills/unstrikeable-admin/SKILL.md) (setup and operations).

## Quick start

```sh
uv tool install git+https://github.com/opcode-fr/unstrikeable    # gives `uns`; needs `gh` authenticated
mkdir -p ~/.unstrikeable && chmod 700 ~/.unstrikeable
cat > ~/.unstrikeable/local.yml <<'EOF'
instance: my-laptop
config: ~/company          # local clone of your config repo (config.yml, culture.md, agents/)
agents:
  kevin: {}                # agents hosted on this instance
EOF
uns check                          # validate config.yml
uns layout                         # dry-run: columns and labels the board needs; then --apply
uns poll --agent kevin --dry-run   # what Kevin would receive right now
```

## Commands

| Command | For | What |
|---|---|---|
| `uns poll --agent A [--dry-run]` | cron | next event for A, or nothing |
| `uns move REF STATE --agent A` | agents | move an item to a logical state |
| `uns status REF --agent A --state working\|done\|blocked` | agents | heartbeat comment |
| `uns comment REF --agent A --body-file F` | agents | signed comment |
| `uns label REF --agent A --add/--remove L` | agents | state labels (never assignments) |
| `uns layout [--department D] [--apply]` | admins | create missing columns and labels |
| `uns check` | admins | validate the config |
| `uns token --agent A` | admins | GitHub App token of an agent |

## Development

```sh
uv venv -p 3.12 && uv pip install -e '.[dev]' && .venv/bin/pytest
```
