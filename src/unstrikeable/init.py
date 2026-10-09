"""`uns init`: a starter config repo for a new company, valid for `uns check` as generated.

Only local files: creating the GitHub repo, the board and the Apps stay human steps (printed as next steps).
"""
from __future__ import annotations

from pathlib import Path

from .admin import installed_version
from .config import SHIPPED_FLOWS

DEPARTMENT = {"dev": "rnd", "content": "marketing"}

CONFIG = """\
# Company config for unstrikeable: https://github.com/opcode-fr/unstrikeable/blob/main/docs/design.md
runtime: "{runtime}"            # versions of `uns` this config is written for (`uns check` compares)

departments:
  {department}:                 # a department = one board worked through one flow
    flow: {flow}                 # shipped flow: dev (spec, code, review) or content (brief, draft, publish)
    board:
      type: github-projects
      owner: {org}
      number: 1                 # number of the GitHub Project (its URL ends with /projects/<number>)
    repos:                      # repos whose issues sit on the board (agents need their App installed there)
      - {org}/CHANGE-ME
    # trusted_authors:          # optional: humans whose items are assigned without a label (never an agent)
    #   - your-github-login
    staff:                      # agent -> roles of the flow, filled by `uns hire <preset> --department {department}`

agents:                         # identity and hosting of each agent, filled by `uns hire`

limits:
  poll_min: 5                   # cron period of every agent's poll
  max_events_per_day: 10        # per agent: events delivered per calendar day
  # max_cost_per_day: 10        # USD, board work only; reached = the agent pauses (needs a meter)
  # max_cost_per_month: 150     # rolling 30 days

# memory:
#   curator: <agent>            # consolidates memory/inbox into memory/shared through a PR (human-reviewed)
"""

CULTURE = """\
# Culture

Read by every agent with every event: keep it under one page.

- Why {org} exists, in one sentence.
- What we value (e.g. facts over opinions: nothing is "done" without a test, a log or a number).
- How we work (e.g. small steps, one subject per PR, say no early with a reason and an alternative).
"""

README = """\
# {org} company config (unstrikeable)

Next steps:

1. Make this a **private** repo: `git init && gh repo create {org}/<name> --private --source . --push`.
2. Create the GitHub Project (board) in `{org}`, then set its number in `config.yml` and list the repos.
3. Hire agents: `uns hire --list`, then `uns hire <preset> --as <name> --department {department}`.
4. On the machine that runs them: `$UNS_HOME/local.yml` pointing `config:` to a clone of this repo
   (see the `unstrikeable-admin` skill), then `uns check` and `uns layout` (dry-run, then `--apply`).

Layout: `config.yml`, `culture.md`, `agents/<agent>.md`, `memory/` (shared, inbox, agents). Never a secret here.
"""

GITIGNORE = """\
# secrets and instance files never belong in the config repo
local.yml
*.pem
.env
*.env
"""


def init(root: Path | str, org: str, flow: str = "dev") -> list[Path]:
    root = Path(root)
    if flow not in DEPARTMENT or not (SHIPPED_FLOWS / ("%s.yml" % flow)).exists():
        raise ValueError("unknown flow %r (shipped: %s)" % (flow, ", ".join(sorted(DEPARTMENT))))
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("%s is not empty: init only writes into a new or empty directory" % root)
    v = installed_version().split(".")
    major, minor = (int(v[0]) if v[0].isdigit() else 0), (int(v[1]) if len(v) > 1 and v[1].isdigit() else 1)
    values = {"org": org, "flow": flow, "department": DEPARTMENT[flow],
              "runtime": ">=%d.%d,<%d.%d" % (major, minor, major, minor + 1)}
    files = {"config.yml": CONFIG, "culture.md": CULTURE, "README.md": README, ".gitignore": GITIGNORE}
    written = []
    for d in ("agents", "memory/shared", "memory/inbox", "memory/agents"):
        (root / d).mkdir(parents=True, exist_ok=True)
        keep = root / d / ".gitkeep"
        keep.write_text("")
        written.append(keep)
    for name, text in files.items():
        path = root / name
        path.write_text(text.format(**values))
        written.append(path)
    return written
