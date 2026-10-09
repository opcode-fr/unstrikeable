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
  tasks_dir: /Users/Shared/uns/tasks # optional: task logs shared by the instances of this machine
  agents:
    kevin:                           # only agents hosted here
      meter: {type: hermes, profile: kevin}   # cost quotas + per-task tokens/cost (`state_db:` to override the path)
      app_id: 123456                 # optional: GitHub App; without it, the instance's `gh` auth is used
      app_key: ~/.unstrikeable/keys/kevin.pem
  ```

Every field is explained in `docs/design.md` §2 (what it is for, what breaks without it).
Several companies on one machine: one `UNS_HOME` per company, exported by every cron wrapper and agent `.env`;
an agent (profile) works for one company only, else its tasks and costs overlap.

## Where an agent runs (`instance`)

An agent must be polled by exactly one instance. `agents.<a>.instance` in `config.yml` says which one; each
instance refuses to poll an agent declared elsewhere. Without it, two instances listing the same agent in their
`local.yml` would both deliver every event, with separate states, budgets and cost quotas (double spend).
To move an agent: change that line, then set it up on the new instance; the old one stops by itself.

## New company

`uns init <dir> --org <org> [--flow dev|content]` writes a starter config repo that passes `uns check`. Then the
human creates the private repo and the board; its README lists the steps.

## Hire an agent

`uns hire --list`, then `uns hire kevin [--as name] --department marketing`: writes `agents/<name>.md` from the
preset and prints the `config.yml` lines to add. Fill in its real `capabilities` (what this instance gives it).
GitHub App: `uns app form --org <org> --agent <name>` (an org owner opens the page and clicks), then on the
instance hosting the agent `uns app exchange <code> --agent <name>`, install the App on the repos, copy
`app_id`/`app_key` into `local.yml`. Check with `uns token --agent <name> | wc -c` (never print the token).

## Setup a department

1. Declare it in `config.yml` (flow, board, repos, staff) and the agent in `agents:`. `uns check`.
   Optional `trusted_authors`: logins of humans whose items are taken without a label (runtime sets the named
   label on take). Humans only: `uns check` refuses an agent or a bot. Ask the human before adding anyone.
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
the crontab line. On someone else's machine, send them the `unstrikeable-join` skill instead of a procedure:
their own agent installs the instance and stops at the human gates.

## Follow-up

- Slack: two `--no-agent` crons in the admin profile, `uns digest --alerts` every 15 min (silent when nothing) and
  `uns digest` every morning, delivered to the human's channel.
- Task metrics: `uns report [--days 30] [--by agent,kind|role|trigger|department|outcome] [--json]` reads
  `state/tasks-<a>.jsonl` (one line per closed task: outcome, kind, wall time, nudges, tokens, cost). Per-task
  tokens/cost come from the profile's `state.db` (Bot Chat lineage only, read-only); `HERMES_HOME` must be the
  agent's own, or set `meter.state_db`. Unknown schema or file = cost unknown, time still counted. Task kinds are
  the flow's `kinds` list; a department replaces it with `overrides: kinds: [...]`.
- Agent isolated in its own OS account: never give it read access to `state.db` (every conversation of the
  profile). Use `integrations/hermes/uns_poll_isolated.sh`: the Hermes owner's account runs `uns usage` and pipes
  the counters to the agent's `uns poll --usage-from -`. For one report across accounts, create a shared dir once
  (`mkdir -m 1777 /Users/Shared/uns/tasks`, the agent would create it 700) and set `tasks_dir:` to it in every
  `local.yml` of the machine.
- Updates: `uns update` (upgrade, reinstall skills in `skills_dirs`, dry-run every hosted agent); as a cron every 6 h
  once trusted. `uns check` says whether the installed runtime matches the config's `runtime:` pin.

## Kill switches and quotas

- `uns pause --agent <a> [--reason …]` / `uns resume --agent <a>`: one agent. `uns pause` / `uns resume`: the instance.
- `limits.max_cost_per_day` / `max_cost_per_month` in `config.yml`: reached → the agent pauses itself and raises an
  alert; only `uns resume` restarts it. Board work only (closed tasks + the task in progress, Bot Chat counters):
  Slack chats do not count. Day = calendar day, month = rolling 30 days; history starts empty on upgrade. Needs a
  `meter` in `local.yml`, or the piped counters of `uns_poll_isolated.sh`.
- Memory: `memory.curator` in `config.yml`. The curator's PR on the config repo must be reviewed by a human:
  shared memory is read by every agent, it is the main injection risk.

## Pitfalls

- **Never test by running the cron wrapper by hand**: a real `poll` marks the event as delivered. Use `--dry-run`.
  Done by mistake? Delete `state/poll-<agent>.json` before resuming the cron.
- **`on:` in flow YAML** is read as the boolean `True` by YAML 1.1; the runtime handles it, other tools may not.
- **Kill switches**: `touch $UNS_HOME/PAUSE` stops every poll of the instance; label `agent:pause` silences one item.
