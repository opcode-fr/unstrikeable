# unstrikeable

> *The company that never goes on strike.*

A team of AI agents working a ticket board: a flow declared in YAML, humans who assign and approve,
and guardrails built in (one task at a time, budgets, heartbeat, kill switches).

- Design: [docs/design.md](docs/design.md)
- Flows shipped: [`dev`](src/unstrikeable/flows/dev.yml) (spec → code → review → merge) and
  [`content`](src/unstrikeable/flows/content.yml) (idea → brief → draft → review → publish).
- Agent presets: Kevin (community manager), Brandon (growth hacker), Capucine (project manager),
  JeanMichel (developer), Didier (reviewer): `uns hire --list`.
- Skills: [`unstrikeable-agent`](src/unstrikeable/skills/unstrikeable-agent/SKILL.md) (agents) and
  [`unstrikeable-admin`](src/unstrikeable/skills/unstrikeable-admin/SKILL.md) (setup and operations), and
  [`unstrikeable-join`](src/unstrikeable/skills/unstrikeable-join/SKILL.md): an agent installs its own instance,
  stopping where a human must click or hold a secret.

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

Instance settings (`local.yml`), several companies on one machine, isolated agents:
[design §2 and §6](docs/design.md).

## Commands

| Command | For | What |
|---|---|---|
| `uns poll --agent A [--dry-run]` | cron | next event for A, or nothing |
| `uns run --agent A -- CMD…` | cron | poll, then hand the event to a CLI agent on stdin (one turn at a time) |
| `uns move REF STATE --agent A` | agents | move an item to a logical state |
| `uns status REF --agent A --state working\|done\|blocked` | agents | heartbeat comment; `done` needs `--learned` (and `--kind` if the flow has kinds) |
| `uns set REF FIELD VALUE --agent A` | agents | single-select field of the board (Size, Priority…) |
| `uns comment REF --agent A --body-file F` | agents | signed comment |
| `uns label REF --agent A --add/--remove L` | agents | state labels (never assignments) |
| `uns remember --agent A --title T --body-file F [--share]` | agents | private note, or proposal to the team |
| `uns ingest --agent A --source S` | human, curator | queue existing knowledge (path or URL) for the curator to fold into the shared wiki |
| `uns memory-publish --agent A --summary-file F` | curator | publish curated memory: reviewed `SAFE` → default branch, else a PR |
| `uns pause` / `uns resume` `[--agent A]` | humans | kill switch: one agent or the whole instance |
| `uns init DIR --org O [--flow dev\|content]` | admins | starter config repo for a new company (local files only) |
| `uns layout [--department D] [--apply]` | admins | create missing columns and labels, fix label colours |
| `uns hire PRESET [--as NAME] [--department D]` / `--list` | admins | add an agent from a preset |
| `uns app form --org O --agent A` / `uns app exchange CODE --agent A` | admins | create the agent's GitHub App |
| `uns digest [--alerts]` | cron | Slack summary, or only new alerts (silent otherwise) |
| `uns report [--days 30] [--by agent,kind] [--json]` | human | closed tasks: outcomes, time, tokens, cost per group |
| `uns usage --profile p` | cron wrapper | Bot Chat counters of a Hermes profile, piped to an isolated agent's `poll --usage-from -` |
| `uns update` | cron / admins | upgrade runtime, reinstall skills, dry-run every agent |
| `uns check` | admins | validate the config |
| `uns baseline --agent A` | admins | migration: mark current events as delivered, send nothing |
| `uns token --agent A` | admins | GitHub App token of an agent |

## Development

```sh
uv venv -p 3.12 && uv pip install -e '.[dev]' && .venv/bin/pytest
```
