# Kiro CLI adapter

Runs an unstrikeable agent with [Kiro CLI](https://kiro.dev/docs/cli/headless/) in headless mode, from cron.
`uns run` polls the board, hands the event to `kiro-cli chat --no-interactive` on stdin, and holds a lock per
agent so a new event never starts while the previous turn still runs (the next cron tick polls again).

Needs a Kiro plan that allows API keys (Kiro Pro or above): headless mode authenticates with `KIRO_API_KEY`.

## Setup on the instance

1. `uns` installed, `local.yml` with this agent (GitHub App `app_id`/`app_key`) and
   `skills_dirs: [~/.kiro/skills]`, then `uns update` (copies the `unstrikeable-agent` skill there).
2. Agent: copy `agent.md` to `~/.kiro/agents/<agent>.md`, replace `__AGENT__`.
3. API key: `~/.unstrikeable/kiro.env` with one line `KIRO_API_KEY=...`, `chmod 600`.
4. Wrapper: copy `uns_run.sh` to `~/.unstrikeable/bin/uns_run_<agent>.sh`, replace the placeholders, `chmod 700`.
5. `uns poll --agent <agent> --dry-run` must run clean; `uns baseline --agent <agent>` if the board already has
   events you do not want delivered.
6. Cron, once a human said go: `*/5 * * * * ~/.unstrikeable/bin/uns_run_<agent>.sh >> ~/.unstrikeable/logs/<agent>.log 2>&1`
   (create `~/.unstrikeable/logs` first). Pause with `uns pause --agent <agent>`, not by editing the crontab.

## Limits

- **No cost meter**: Kiro credits are not read by `uns`, so cost quotas do not apply; use
  `limits.max_events_per_day` and the Kiro plan's own credit cap.
- **No conversation between runs**: each event starts a fresh Kiro session; continuity comes from the event
  and the memory files.
- **Tools are pre-approved** (`--trust-all-tools`): what the agent can see is set by `tools` in `agent.md`,
  the shell deny rules block pushes to default branches and merges, and the real boundary is the GitHub App's
  permissions plus branch protection on the repos.
- A failing `kiro-cli` (bad key, network) still consumes the event: the agent gets nudged, then marked
  `agent:lost`, which `uns digest --alerts` reports.
