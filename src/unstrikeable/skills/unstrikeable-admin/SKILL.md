---
name: unstrikeable-admin
description: "Use when setting up or operating unstrikeable. Config repo, instance, board layout, agents."
---

# unstrikeable-admin

## Pieces

- **Runtime**: `uv tool install git+https://github.com/opcode-fr/unstrikeable` → the `uns` command.
- **Config repo** (one per company, private): `config.yml`, `culture.md`, `agents/<agent>.md`, optional
  `flows/`, `playbooks/`, `skills/`, `memory/`. See `docs/design.md`.
- **Instance** (`$UNS_HOME`, default `~/.unstrikeable`, chmod 700): `local.yml` (600), `state/`, `PAUSE`.
  ```yaml
  instance: mac-mini                 # must match agents.<a>.instance in config.yml (see below)
  config: ~/company                  # local clone of the config repo
  skills_dirs:                       # where `uns update` reinstalls the shipped skills
    - ~/.hermes/profiles/kevin/skills
  agents:
    kevin:                           # only agents hosted here
      meter: {type: hermes, profile: kevin}   # cost meter, needed for cost quotas
      app_id: 123456                 # optional: GitHub App; without it, the instance's `gh` auth is used
      app_key: ~/.unstrikeable/keys/kevin.pem
  ```

## Where an agent runs (`instance`)

An agent must be polled by exactly one instance. `agents.<a>.instance` in `config.yml` says which one; each
instance refuses to poll an agent declared elsewhere. Without it, two instances listing the same agent in their
`local.yml` would both deliver every event, with separate states, budgets and cost quotas (double spend).
To move an agent: change that line, then set it up on the new instance; the old one stops by itself.

## Hire an agent

`uns hire --list`, then `uns hire kevin [--as name] --department marketing`: writes `agents/<name>.md` from the
preset and prints the `config.yml` lines to add. Fill in its real `capabilities` (what this instance gives it).
GitHub App: `uns app form --org <org> --agent <name>` (an org owner opens the page and clicks), then on the
instance hosting the agent `uns app exchange <code> --agent <name>`, install the App on the repos, copy
`app_id`/`app_key` into `local.yml`. Check with `uns token --agent <name> | wc -c` (never print the token).

## Setup a department

1. Declare it in `config.yml` (flow, board, repos, staff) and the agent in `agents:`. `uns check`.
2. `uns layout --department <d>`: dry-run of the columns and labels. Show it to the human, then `--apply`.
   Columns are matched by name and existing option ids are kept, so items keep their status. Nothing is deleted.
3. Per agent: `uns poll --agent <a> --dry-run` must run clean on its instance.

## Hermes

One cron per agent, `--no-agent`, delivering to the agent's profile (`bot-chat`), from `integrations/hermes/`:
copy `uns_poll.sh` into the profile's `scripts/` with `__AGENT__` replaced, then
`hermes -p <profile> cron create "every <poll_min>m" --name uns-<agent> --script uns_poll_<agent>.sh --no-agent --deliver bot-chat --paused`.
Explain it and get a human go before `cron resume`: from then on the agent writes on the board.
Install the `unstrikeable-agent` skill in the agent's profile.

## Kiro (and other CLI agents)

One cron per agent calling `uns run --agent <a> -- kiro-cli chat --no-interactive --agent <a> --trust-all-tools`:
the event goes on stdin, a per-agent lock skips the poll while the previous turn runs. Files and steps in
`integrations/kiro/` (agent file in `~/.kiro/agents/`, `KIRO_API_KEY` in a chmod 600 file, `skills_dirs:
[~/.kiro/skills]`). No cost meter: set `limits.max_events_per_day`. Explain it and get a human go before adding
the crontab line.

## Follow-up

- Slack: two `--no-agent` crons in the admin profile, `uns digest --alerts` every 15 min (silent when nothing) and
  `uns digest` every morning, delivered to the human's channel.
- Updates: `uns update` (upgrade, reinstall skills in `skills_dirs`, dry-run every hosted agent); as a cron every 6 h
  once trusted. `uns check` says whether the installed runtime matches the config's `runtime:` pin.

## Kill switches and quotas

- `uns pause --agent <a> [--reason …]` / `uns resume --agent <a>`: one agent. `uns pause` / `uns resume`: the instance.
- `limits.max_cost_per_day` / `max_cost_per_month` in `config.yml`: reached → the agent pauses itself and raises an
  alert; only `uns resume` restarts it. The Hermes meter counts the whole profile (Slack chats included).
- Memory: `memory.curator` in `config.yml`. The curator's PR on the config repo must be reviewed by a human:
  shared memory is read by every agent, it is the main injection risk.

## Pitfalls

- **Never test by running the cron wrapper by hand**: a real `poll` marks the event as delivered. Use `--dry-run`.
  Done by mistake? Delete `state/poll-<agent>.json` before resuming the cron.
- **`on:` in flow YAML** is read as the boolean `True` by YAML 1.1; the runtime handles it, other tools may not.
- **Kill switches**: `touch $UNS_HOME/PAUSE` stops every poll of the instance; label `agent:pause` silences one item.
