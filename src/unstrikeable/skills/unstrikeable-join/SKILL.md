---
name: unstrikeable-join
description: "Use when asked to join an unstrikeable company as an agent. Installs and checks the instance, stops at human gates."
---

# unstrikeable-join

You are setting up the machine you run on so that **you** (or an agent of this machine) work a company's board
through unstrikeable. You do the mechanical steps and check each one; a human does the few steps that need an
owner's click or a secret. Before installation, read this file from GitHub:
`https://raw.githubusercontent.com/opcode-fr/unstrikeable/main/src/unstrikeable/skills/unstrikeable-join/SKILL.md`.

## Inputs (ask the human if one is missing, never guess)

- `CONFIG_REPO`: the company config repo (`acme/hq`).
- `AGENT`: the agent name, already declared in its `config.yml` (`agents.<AGENT>` and a department `staff`).
- `INSTANCE`: the name of this machine in `config.yml` (`agents.<AGENT>.instance`).
- `RUNNER`: `kiro` (Kiro CLI) or `hermes` (Hermes profile).

## Rules

- **Human gates are hard stops.** At each `STOP`, say exactly what the human must do, then wait for their answer.
- **Secrets never pass through you**: no API key, private key or token in the chat, a command line you show,
  a file you print or a log. The human writes the Kiro key into its file; you only check that the file exists and is chmod 600.
- **Nothing reaches the board before the last gate**: only `--dry-run`, `check` and read-only `gh api` calls.
  Never run the cron wrapper or `uns poll` without `--dry-run`: it consumes an event.
- Each step ends with its check. A failed check: stop, show the error, propose the fix; do not skip ahead.
- Report at the end: what is installed, what each check returned, what is left to the human.

## 1. Preflight

```sh
uname -s; command -v uv git gh; gh auth status
```
- macOS or Linux, `uv`, `git`, `gh` authenticated with read access to `CONFIG_REPO`'s org.
- `kiro`: `kiro-cli --version` must print 3.x or later (install: `curl -fsSL https://cli.kiro.dev/install | bash`).
- `hermes`: `hermes --version`, and know the profile that will host `AGENT`.

## 2. Runtime and config clone

```sh
uv tool install git+https://github.com/opcode-fr/unstrikeable
export UNS_HOME="${UNS_HOME:-$HOME/.unstrikeable}"
mkdir -p "$UNS_HOME/state" "$UNS_HOME/bin" "$UNS_HOME/logs" "$UNS_HOME/keys" && chmod 700 "$UNS_HOME" "$UNS_HOME/keys"
gh repo clone CONFIG_REPO ~/<repo-name>
```
Check in the clone's `config.yml`: `agents.AGENT` exists, its `instance` equals `INSTANCE`, and AGENT appears in a
department's `staff`. Otherwise STOP: the human must declare it (`uns hire`, see the admin skill) and push.

## 3. `local.yml`

Write `$UNS_HOME/local.yml`, `chmod 600`:
```yaml
instance: INSTANCE
config: ~/<repo-name>
skills_dirs:
  - ~/.kiro/skills                 # kiro; hermes: <hermes home>/profiles/<profile>/skills
agents:
  AGENT:
    app_key: ~/.unstrikeable/keys/AGENT.pem
    # app_id: filled at step 4
    # hermes only: meter: {type: hermes, profile: <profile>}
```
Check: `uns check` runs clean and prints the runtime pin as matching.

## 4. GitHub App (gate)

**STOP**: an owner of the org runs `uns app form --org <org> --agent AGENT`, opens the page, clicks, and gives
the human here the `code=` value of the page they land on (single use, valid 1 hour).
Then: `uns app exchange <code> --agent AGENT`. It writes the key into `$UNS_HOME/keys/` and prints `app_id`
and the install link; put `app_id` into `local.yml`.
**STOP**: the owner installs the App on the department's repos **and** on `CONFIG_REPO` (memory is pushed there).
Check, without printing the token:
`GH_TOKEN=$(uns token --agent AGENT) gh api installation/repositories --jq '[.repositories[].full_name]'`
must list every department repo and `CONFIG_REPO`.

## 5. Runner

Runner files are not installed with `uns`: fetch them from
`https://raw.githubusercontent.com/opcode-fr/unstrikeable/main/integrations/<runner>/<file>`.

Kiro (`integrations/kiro/`):
1. `uns update` copies the `unstrikeable-agent` skill into `~/.kiro/skills`; check `~/.kiro/skills/unstrikeable-agent/SKILL.md`.
2. Agent file: `integrations/kiro/agent.md` → `~/.kiro/agents/AGENT.md`, `__AGENT__` replaced.
3. **STOP**: the human creates a Kiro API key (Kiro Pro or above) and writes it themselves:
   `printf 'KIRO_API_KEY=%s\n' '<key>' > "$UNS_HOME/kiro.env" && chmod 600 "$UNS_HOME/kiro.env"`.
   Check only `ls -l "$UNS_HOME/kiro.env"` (`-rw-------`), never its content.
4. Wrapper: `integrations/kiro/uns_run.sh` → `$UNS_HOME/bin/uns_run_AGENT.sh`, placeholders replaced with absolute
   paths (`command -v uns`, `command -v kiro-cli`, a work dir such as `~/AGENT`), `chmod 700`; `sh -n` on it.
5. Smoke test of Kiro alone (no board): `echo 'Reply OK.' | (set -a; . "$UNS_HOME/kiro.env"; kiro-cli chat --no-interactive --agent AGENT)`.
   It must answer without an auth or agent error (exit code 4 = agent file not found).

Hermes: follow the `Hermes` section of the `unstrikeable-admin` skill (wrapper in `integrations/hermes/`, cron
created `--paused`), and put the agent's `UNS_HOME` in the profile's `.env`.

Both: in every clone the agent pushes from, make git use the App token instead of the human's account:
```sh
git config credential.https://github.com.helper ''
git config --add credential.https://github.com.helper '!gh auth git-credential'
```

## 6. Dry run and baseline

1. `uns poll --agent AGENT --dry-run`: show the human what the agent would receive now.
2. **STOP**: ask whether these pending events should be handled. If not (usual on a busy board):
   `uns baseline --agent AGENT` marks them delivered without sending anything.

## 7. Start (gate)

**STOP**: explain what starts (every 5 min, the agent reads the board and acts on GitHub as its App) and wait
for an explicit go. Then:
- Kiro: `(crontab -l 2>/dev/null; echo "*/5 * * * * $UNS_HOME/bin/uns_run_AGENT.sh >> $UNS_HOME/logs/AGENT.log 2>&1") | crontab -`
- Hermes: `hermes -p <profile> cron resume uns-AGENT`.

First real check: the human assigns a small item to AGENT; within one poll its status comment turns 🟢 working.
Stop it any time with `uns pause --agent AGENT`.
