# unstrikeable — design

> *The company that never goes on strike.*

Status: **v0.1 and v0.2 implemented** (core, GitHub backend, memory, kill switches, cost quotas, task accounting),
in daily use by one company on two instances. Open points: §12.

## 0. Goals and non-goals

**Goal**: let a team of AI agents work a ticket board (code, content, or anything else) through a
**flow declared in YAML**, with humans who assign and approve, and built-in guardrails
(one task at a time, budgets, heartbeat, kill switches).

**Non-goals**
- A generic workflow engine. The runtime ships a **closed** set of triggers; YAML only wires them.
- Replacing human decisions: assigning, approving, merging and publishing stay human by default.
- Webhooks, real time, UI. We poll (instances sit behind NAT; GitHub Projects v2 does not fire Actions).

## 1. Two separate things

| | Runtime (this repo) | Config repo (one per company) |
|---|---|---|
| Contains | code, `uns` CLI, backends, foundation skills, flow profiles, docs | `config.yml`, culture, agents, team skills, memory |
| Versioning | `version` in `pyproject.toml`; release tags to come (§12) | free |
| Installed as | Python package (`uv tool install git+…`), version range declared by the config | cloned on each instance |
| Secrets | never | never |

No fork: teams customise through their config repo. Fork only to change the runtime itself, then contribute upstream.

Secrets and local paths live in `local.yml` on each instance (chmod 600, never versioned).

## 2. Config repo layout

```
config.yml                # pinned runtime, departments, agents, limits
culture.md                # vision, values, mindset of the company (read by every agent)
agents/<agent>.md         # role, tone, capabilities, agent-specific instructions
skills/                   # team skills (same name as a runtime skill = replaces it)
memory/
  shared/*.md             # curated, human-approved, read by every agent
  inbox/<agent>/*.md      # proposals to share, consumed by the curator (see §8)
  agents/<agent>/*.md     # the agent's own long-term notes, never loaded by others
```

Example:

```yaml
runtime: ">=0.1,<0.2"            # checked by `uns check` (not enforced at install, see §9)

departments:
  marketing:
    flow: content               # profile shipped with the runtime
    board:
      type: github-projects
      owner: acme
      number: 2
    repos:
      - acme/marketing
    staff:                      # who works here, and with which roles of the flow
      kevin:
        - planner
        - writer
    overrides:
      columns:
        approved: Ready to publish
      labels:
        - channel:x
        - channel:linkedin
        - type:release

  rnd:
    flow: dev
    board:
      type: github-projects
      owner: acme
      number: 1
    repos:
      - acme/product
      - acme/lab
    staff:
      gerard:
        - pm
        - dev
      didier:
        - review

agents:                         # identity and hosting, independent of departments
  kevin:
    instance: mac-mini
    identity: kevin-acme
  gerard:
    instance: mac-mini
    identity: gerard-acme
  didier:
    instance: other-host
    identity: didier-acme
    limits:                     # per-agent override of the company limits below
      max_cost_per_day: 10

limits:
  poll_min: 5
  max_events_per_day: 10
  max_cost_per_day: 20          # USD, board work only (§6); reached = the agent is paused
  max_cost_per_month: 300       # rolling 30 days

memory:
  curator: kevin                # consolidates memory/inbox into memory/shared, through a PR
  inbox_max: 10                 # curate at 10 new entries…
  max_age_h: 24                 # …or when the oldest is a day old
```

Vocabulary:
- A **flow** is a template (`dev`, `content`): states, roles, triggers, playbooks.
- A **department** is a running instance of a flow: one board, its repos, its staff.
- **Roles are per department**: Kevin is `writer` in marketing; the same agent could be `reviewer` elsewhere.
  `limits` apply per agent, across all its departments.

### `local.yml`: the instance

An **instance** is one machine account that runs `uns poll` for some agents. Its directory is `$UNS_HOME`
(default `~/.unstrikeable`, chmod 700): `local.yml` (chmod 600, never versioned), `state/` (poll state per agent,
token cache), `PAUSE` (kill switch of the instance).

```yaml
instance: mac-mini                # name of this instance; must equal agents.<a>.instance in config.yml
config: ~/acme-hq                 # local clone of the config repo (pulled at every poll)
skills_dirs:                      # where `uns update` copies the shipped skills (one per agent profile)
  - ~/.hermes/profiles/kevin/skills
tasks_dir: /Users/Shared/uns/tasks  # optional: where task logs go (default: $UNS_HOME/state)
agents:                           # only the agents hosted here
  kevin:
    app_id: 123456                # GitHub App of the agent
    app_key: ~/.unstrikeable/keys/kevin.pem
    meter:                        # optional: tokens and cost (task accounting, cost quotas)
      type: hermes
      profile: kevin
      state_db: ~/.hermes/profiles/kevin/state.db   # optional, default $HERMES_HOME/profiles/<profile>/state.db
```

- `instance`: guards against two machines serving one agent (see below). Without it, no check is made.
- `config`: every command reads the company from this clone. Wrong path = every command fails with a clear error.
- `skills_dirs`: without it, `uns update` upgrades the runtime but leaves old skills in the profiles, and agents
  follow outdated instructions (e.g. a closing command missing a required flag).
- `tasks_dir`: one directory shared by several instances of the same machine (agents isolated in their own OS
  accounts), so a single `uns report` reads them all. Created once by the admin with `mkdir -m 1777`: an agent
  would create it private. Logs stay owned by their agent, readable by others (644).
- `app_id` / `app_key` (optional `installation_id`): the agent acts on GitHub as its App. Without them, `uns`
  uses the instance's own `gh` login, so the agent writes as that human.
- `meter`: without it, tasks get a time but no cost, and cost quotas are not checked. An agent isolated in
  another OS account gets its counters piped by the Hermes owner's account instead (§6).

### Several companies on one machine

One `$UNS_HOME` per company: each holds its own `local.yml` (pointing to that company's config clone), state and
kill switch. Every cron wrapper and every agent profile's `.env` exports the `UNS_HOME` of its company, so the
agent's `uns status` writes to the same state as its poll. Give each company its own `tasks_dir` if you use one.

**An agent (one Hermes profile, one Kiro agent) works for one company only.** Two states would each deliver it
a task, breaking *one task at a time*, and both would read the same usage counters, counting the same spend twice
in task costs and quotas. A person who works for two companies gets two agents (two profiles), one per company.

### `agents.<a>.instance`: where an agent runs

An agent must be hosted by **exactly one** instance (machine + account running `uns poll` for it).
Each instance only knows its own `local.yml`; two instances listing the same agent cannot see each other, and
would both deliver every event (two sessions on one item), each with its own state, budgets and cost quotas
(so the real spend could reach twice the cap).

`instance` in `config.yml` is the shared source of truth: "Kevin runs on `mac-mini`". At each poll, the instance
compares it with its own `instance` in `local.yml` and **refuses to poll** an agent declared elsewhere.

- Moving an agent = change this one line; the old instance stops by itself at its next poll.
- Optional: without `instance`, no check is made.
- It guards against configuration mistakes, not against a hostile instance (two machines both claiming
  `mac-mini` would pass). It is not a secret, but it does reveal a machine name in the config repo.

### `culture.md`: the company mindset

Free-form Markdown, written by humans: why the company exists, what it values, how it works.

```markdown
# Culture
- Frugal: managed and on-demand services first; no spend without a reason.
- Facts over opinions: nothing is "done" or "faster" without a test, a log or a number.
- Small steps: one subject per PR, the minimal change that solves it.
- Say no early: a bad idea gets flagged with a reason and an alternative.
```

- Injected into **every** event delivered to **every** agent, so it must stay short (cap: ~1 page; `uns check` warns above).
- It shapes judgement, it does not grant rights: the runtime rules (§6) and the flow always win over it.

### `agents/<agent>.md`: identity and capabilities

```markdown
---
capabilities:
  - I have an AWS account to run SageMaker GPU jobs.
  - I run on a Mac with Apple Silicon (MLX).
  - I can write and publish posts on X.
---
Gerard is a senior ML engineer. Terse, measures before claiming.
```

- `capabilities` are **plain sentences**, no taxonomy. The planner reads them to suggest who should take an item
  ("needs a GPU run → Gerard has SageMaker"); the agent reads its own to know what it can do.
- A capability is a **claim, not a permission**: the credentials behind it live in that instance's `local.yml`,
  and humans still assign. Declaring "I can publish on X" without the token simply fails at run time.

### Agent presets

The runtime ships ready-made agent sheets (personality + suggested roles per flow, no capabilities):
Kevin (community manager), Brandon (growth hacker), Capucine (project manager), JeanMichel (developer),
Didier (reviewer). `uns hire <preset> [--as <name>]` copies one into `agents/<name>.md` and prints the
`config.yml` lines to add; the human then writes the real capabilities of that instance.

### What an agent receives with each event

`culture.md` → `agents/<self>.md` → `memory/shared/` → `memory/agents/<self>/` → flow playbook for (role, trigger) → the event itself.
Runtime guardrails come last and cannot be overridden by anything above them.

## 3. Flow (YAML profile)

A profile declares **states, roles, triggers and playbooks**. Shipped profiles (`flows/dev.yml`,
`flows/content.yml`) are templates: each department references one and overrides it key by key
(shallow merge, no deep merge).

```yaml
# flows/content.yml (shipped)
states:                         # order = column order
  - key: backlog                # logical key, used by agents and the CLI
    column: Ideas               # column name on the board
  - key: ready
    column: Ready to write
  - key: doing
    column: Drafting
  - key: review
    column: In review
  - key: approved
    column: Ready to publish
  - key: done
    column: Published

roles:
  planner:                      # no label: acts on every item, first planner of the staff
    on:                         # state -> triggers this role reacts to
      backlog:
        - item_new
        - human_comment
  writer:
    label: "writer:{agent}"
    on:
      ready:
        - assigned
      doing:
        - human_comment
      approved:
        - pr_conflict
        - human_comment
  reviewer:                     # a human: reviews the PR, publishes, then merges
    human: true

playbooks:                      # instructions handed to the agent, per role.trigger
  planner.item_new: playbooks/content/brief.md
  planner.human_comment: playbooks/content/brief.md
  writer.assigned: playbooks/content/write.md
  writer.human_comment: playbooks/content/rework.md
  writer.pr_conflict: playbooks/content/conflict.md

artifact:
  path: "{yyyy}/{mm}/{channel}-{dd}-{slug}.md"

kinds:                          # nature of the work, chosen by the agent when it closes a task
  - post
  - article
  - newsletter
  - research
  - other
```

- Agents and the CLI speak **logical keys** (`uns move <ref> review`), never column names.
- A `human: true` role has no agent: the runtime only watches and waits for the human.
- `label` is the assignment label of a role, a template:
  - **with `{agent}`** (`writer:{agent}`): named assignment, one label per staff member (`writer:kevin`).
  - **without** (`to-write`): pool assignment, any idle staff member holding that role may take the item.
  The pool is resolved **deterministically** (first idle staff member, in `staff` order): every poller computes
  the same answer from the same board, with no coordination. Two named labels for one role → `needs:human`.
  Not done yet: swapping the pool label for the named one on take (§12); until then the holder of a pool item
  can change when the first holder becomes busy elsewhere. Shipped flows only use named labels.
- A role without `label` acts on every item in its states (first staff member with the role); `auto: true`
  does the same for a labelled role when the item carries none of its labels (the `dev` reviewer).
  A flow may declare both forms for the same role:
  ```yaml
  label:
    - "writer:{agent}"
    - to-write
  ```
- System labels (`needs:human`, `agent:pause`, `agent:lost`,
  `spec:question`) are fixed and shared by every flow.
- `kinds` is a **closed list** of task kinds (lowercase slugs) used to classify work in `uns report`. What it is
  for: comparing time and cost per kind of work (a bug vs a feature, a post vs an article). What breaks without
  it: nothing, tasks are simply not classified (`--kind` is then neither asked nor accepted). Limits: the kind is
  the agent's judgement, not a human label; keep an `other` entry so it never has to pick a wrong one. A department
  replaces the whole list through `overrides: kinds:` (shallow merge).

## 4. Triggers (closed set, coded and tested)

| Trigger | Fires when |
|---|---|
| `item_new` | item is in the state and has never been handled by this role |
| `human_comment` | a human member spoke last, after the agent's last comment |
| `new_comment` | anyone trusted (human or another agent, e.g. a reviewer) spoke last, after the agent |
| `assigned` | a label of the role is set (named for this agent, or pool and this agent is first idle), state is `ready`, item not blocked |
| `pr_updated` | the head commit of the linked PR changed |
| `pr_conflict` | the linked PR is not mergeable |
| `ci_failed` | CI is red on the linked PR |

A blocked item simply never fires `assigned`; it does once its dependencies are closed.

Adding a trigger = code + tests in the runtime, never in a config.
Delivery priority: work in progress (`pr_conflict`, `ci_failed`, `human_comment` in `doing`) > review > spec > new work.

## 5. Backends

Two interfaces, because GitHub plays two roles and only one of them is replaceable by Trello & co.

```python
class Board(Protocol):            # GitHub Projects today; Trello, Linear, Jira… when a project needs one
    def items(self) -> list[Item]: ...                       # open items, with comments and linked PRs
    def item(self, ref: str) -> Item | None: ...
    def move(self, ref: str, column: str) -> None: ...
    def set_field(self, ref: str, name: str, value: str) -> None: ...      # single-select fields (Size, Priority…)
    def labels(self, ref: str, add=(), remove=()) -> None: ...
    def comment(self, ref: str, body: str) -> None: ...
    def upsert_status(self, ref: str, agent: str, body: str) -> None: ...  # one status comment per agent
    def ensure_layout(self, dept: Department, apply=False, prune=False) -> list[str]: ...   # columns + labels
```

- **Only `github-projects` is implemented**, on top of the `gh` CLI (already authenticated, handles pagination
  and GraphQL; agent tokens are passed through `GH_TOKEN`). The interface exists so the core never depends on
  GraphQL; a Trello backend gets written when a real project needs it.
- The **forge** (PRs: head, mergeable, CI) is not a separate interface yet: linked PRs come with each item of the
  GitHub board. It gets its own interface with the first board that is not on the forge (§12).
- **Trust** (who the agents listen to): a comment or item counts only if its author is a member, collaborator or
  owner, one of the company's agents (its App login), or, because an App token sees private org members as
  `NONE`, a user with triage, write, maintain or admin permission on the repo (cached for one poll). Agent markers (`<!-- uns:agent=… -->`,
  status comments) count only on trusted comments, else anyone could impersonate an agent.
- Agents **never** touch the board directly (no `gh project …` in skills): everything goes through `uns`.
  `git` and `gh pr` stay allowed (that is the forge).
- Identity is the backend's job (one GitHub App per agent; on Trello, one member per bot). The core only sees a token.

## 6. Runtime core

- **Poll**: `uns poll --agent <a>` every `poll_min`; empty output = zero tokens spent. Events are deduplicated by key.
  It first pulls the config clone (agents push memory there), then: kill switches and quotas, the current task,
  memory curation (curator only), then at most one new event.
- **Migration**: `uns baseline --agent <a>` marks every event the agent would get now as delivered, sending
  nothing: run once before the first real poll on a board that already has history.
- **One task at a time** per agent; the next event is delivered only once the current task is `done` or `blocked`.
- **Status**: a single comment per agent and per item (🟢 / ✅ / ⏸️, Done / Next), the heartbeat read from outside.
  Silent after `ack_min` → nudge; after `stale_min` → `agent:lost` + alert.
- **Budgets**: `max_events_per_day`, `max_runs` per item, `max_review_rounds` → `needs:human`.
- **Kill switches**: `agent:pause` label (item), `uns pause --agent <a>` (one agent), `uns pause` (the instance).
  A paused agent receives nothing, not even nudges, until `uns resume`.
- **Cost quotas**: `max_cost_per_day` (calendar day, local time) / `max_cost_per_month` (rolling 30 days), USD,
  **board work only**: the sum of the agent's closed tasks (`spend` in the poll state, from the task accounting
  below) plus what the task in progress has used so far, checked at every poll. The counters come from the **meter**
  declared per agent in `local.yml` (`{type: hermes, profile: <p>}`, the Bot Chat counters) or are piped by the
  single reader for an isolated agent. Slack chats of the profile do not count. Quota reached → the agent is
  paused, an alert is raised, and only a human resumes it (resuming while still over the cap pauses it again at
  the next poll: raise the cap or wait for the window). No counters or no quota = no cost check (event budgets
  still apply). The history starts with the first task closed by this version: no back-fill.
- **Reports**: `uns digest --alerts` every 15 min (silent when nothing happens) and `uns digest` every morning:
  per agent, current task, events today, cost today / 30 days, paused or not.
- **Task accounting**: every closed task (one delivered event, from delivery to `done`, `blocked`, `lost` or the
  item gone) becomes one line of `state/tasks-<a>.jsonl`: agent, ref, role, trigger, kind, outcome, wall time,
  nudges, runs and review rounds on the item, and the tokens and cost the task used. `uns report --by agent,kind`
  aggregates them (count, outcomes, median time, total and median cost, tokens with cache apart, nudges per task);
  `--json` gives the raw lines.
  - **Time** = wall time from delivery to the agent's closing status: one event is one turn of work, so it is
    close to the working time, not the ticket's lead time (a ticket made of several events has several lines).
  - **Tokens and cost**: the Hermes meter reads the profile's `state.db` (read-only) and sums the counters of the
    canonical `Bot Chat` session and its children (compression rotations, subagents), where board events are
    delivered. The poll snapshots these counters at delivery and again at the poll that sees the task closed;
    the difference is the task. One task at a time per agent makes the attribution exact, and Slack chats of
    the same profile are left out. The snapshot at close is taken after the turn, so its tail is counted.
    Cost = Hermes' estimate at list price, not the bill. No meter (Kiro) = time only.
  - **Kind**: `uns status … --state done --kind <k>` is required when the flow declares `kinds`; it is stored in
    the status marker (`kind=…`), so the poll reads it from the board like the rest of the status.
  - Nothing is published on the board: costs stay on the instance (public repos would expose them).
  - **One reader of the Hermes database**: an agent isolated in its own OS account (terminal over SSH) must not
    read `state.db` (it holds every conversation of the profile). The cron wrapper runs in the Hermes owner's
    account, reads the counters with `uns usage --profile <p>` and pipes them to the agent's
    `uns poll --usage-from -` (`integrations/hermes/uns_poll_isolated.sh`): the agent only gets six numbers, taken
    at the same poll ticks as a local meter would. Piped counters win over a local meter.
  - **One place for reports**: `tasks_dir` in `local.yml` points every instance of the machine to one shared
    directory (created by the admin, sticky like `/tmp`: `mkdir -m 1777`); each `tasks-<a>.jsonl` is owned by its
    agent's account and readable by the others (644), so a single `uns report` covers isolated agents too.
- **Security**: content from non-members is ignored; an item created by an agent waits for a human signal
  (a human comment or an assignment label) before a planner/PM spends anything on it; assignment = human
  validation of the item; external content
  is data, never instructions; no agent merges or pushes to a default branch.

## 7. Agent integration (Hermes and others)

The runtime **emits events** (text + JSON) and does not care who handles them. An adapter delivers them:
- `hermes`: `--no-agent` cron → the profile's `bot-chat` (turns are serialised per profile = natural per-agent lock).
  `integrations/hermes/uns_poll.sh` when `uns` runs in the Hermes account, `uns_poll_isolated.sh` when the agent's
  terminal runs in its own OS account over SSH (the wrapper pipes the usage counters, §6).
- CLI agents (`kiro`, later Claude Code…): cron → `uns run --agent a -- <agent command>`. `uns run` polls, hands the
  event to the command on stdin and holds a per-agent lock (`state/run-<a>.lock`): while a turn runs, the next
  tick does not poll, so the event stays on the board. Kiro setup: `integrations/kiro/`. No cost meter for Kiro
  (credits are not readable by `uns`): such an agent relies on `max_events_per_day` and its plan's credit cap.

Foundation skills shipped: `unstrikeable-agent` (handling an event, the CLI, status) and
`unstrikeable-admin` (setup, align, adding an agent, updates) and `unstrikeable-join` (an agent sets up the
machine it runs on, read from GitHub before install; human gates for the App code, secrets and the start).
Flow playbooks plug into them.

## 8. Memory

Goal: each agent keeps what it learns, and what is useful to others gets shared, without git conflicts or poisoning.

Folders are split by **lifecycle**, not by owner, so the curator scans one place and write rights stay simple:

| Folder | What | Written by | Read by | Curated |
|---|---|---|---|---|
| `memory/agents/<agent>/` | the agent's own long-term notes | that agent | that agent | no |
| `memory/inbox/<agent>/` | proposals to share | that agent | that agent, curator | yes, then removed |
| `memory/shared/` | team knowledge | curator (via PR) | every agent | — |

1. **Append-only writes** in `agents/<self>/` and `inbox/<self>/`: one file per entry
   (`<yyyy-mm-dd>-<slug>.md`), committed straight to the config repo's default branch
   (unique names → no conflict possible). An agent may edit or delete its own `agents/<self>/` files to keep them tidy.
2. **Promotion** is explicit: to share a private note, the agent writes a new entry in `inbox/<self>/`.
   **Mandatory lesson at closing**: `uns status … --state done` requires `--learned "<rule> because <reason>"`
   (stored in the agent's private memory, or in the inbox with `--share-learned`) or `--learned none`.
   Without it, agents skip memory on short tasks and the same pitfalls come back.
3. **`curator` role** (`memory.curator`, an existing agent): triggered when the inbox holds `inbox_max` new
   entries (default 10) or its oldest entry is older than `max_age_h` (default 24 h); it consolidates into
   `memory/shared/*.md` through **a PR** and removes the processed entries. The curation task has no board item:
   its status lives in the instance state (`uns status memory --agent <curator> …`).
4. **Mandatory human review** of that PR: memory read by every agent is an injection vector
   (an agent that read a booby-trapped issue could contaminate the whole team). No auto-merge.
5. **"Private" means not loaded by other agents, not secret**: anyone with read access to the config repo can read it.
   Hence no secrets, no credentials, no personal data anywhere under `memory/`.
6. **Content**: reusable facts and procedures, no session logs. Size caps (`memory.shared_max_words`, default
   3000; `memory.private_max_words`, default 1500): above them the event carries a warning asking the owner (or the
   curator for `shared/`) to condense instead of piling up.

The only exception to "no agent pushes to a default branch": `memory/agents/<self>/` and `memory/inbox/<self>/` in the config repo.

## 9. Distribution and updates

- Python ≥ 3.10 package; runtime dependencies: PyYAML and the `gh` CLI.
- Install: `uv tool install git+https://github.com/opcode-fr/unstrikeable` (main). An instance whose agents run
  under another OS account installs it in that account too (build the wheel, copy it, `uv tool install <wheel>`).
- `uns update`: `uv tool upgrade unstrikeable`, copies the shipped skills into every `skills_dirs` entry, then
  runs `poll --dry-run` for each hosted agent. Upgrade the runtime and the skills together: a new runtime can
  require a flag that old skills do not mention.
- The config's `runtime:` range is only **checked** (`uns check` says whether the installed version matches), not
  enforced at upgrade: there are no release tags yet, so every instance follows `main` (§12).

## 10. Plan

1. **v0.1** (done): core + GitHub backend + `dev` and `content` flows + Hermes adapter + tests.
2. **v0.2** (done): memory (private + shared) + curator, kill switches, cost quotas, CLI agents (`uns run`, Kiro),
   migration from the previous system (`uns baseline`, legacy markers read).
3. **v0.3** (done): task accounting (`uns report`, kinds), cost quotas on board work only, isolated agents.
4. Next: see §12.

## 11. Decisions

- Language: English (docs, messages, CLI).
- GitHub backends use the `gh` CLI.
- CLI name: `uns`.
- CLI agents run through one generic command (`uns run … -- <cmd>`, event on stdin) rather than one adapter per
  tool: the lock and the poll are coded and tested once; a tool only needs a wrapper script and an agent file.
- Task cost = difference of cumulative counters (snapshot at delivery, snapshot at close) rather than counting
  per session: Hermes reuses one `Bot Chat` session for every event, so a session is not a task. Reading
  `state.db` couples us to Hermes' schema: an unreadable schema gives an unknown cost, never a failed poll.
- Cost quotas count board work only, from the same counters as the task records, rather than `hermes insights`
  (whole profile, Slack included, and not runnable from an isolated account): a long Slack conversation with a
  human must not pause the agent's board work, and one mechanism serves both reports and quotas.
- Isolated agents get their usage from a single reader (the Hermes owner's account) through the poll's stdin,
  rather than read access to `state.db` (it would expose every conversation and break the isolation) or a
  periodic sampler joined after the fact (same precision, more code).
- Task kinds are declared by the agent at closing, from a closed list in the flow, rather than read from issue
  labels: every task gets one, with no human discipline needed. Costs are not written on the board.

## 12. Open questions

- Content flow conversations (replies to comments): one file per conversation, one section per exchange,
  one item per reply to write — to confirm with real use.
- Dedicated `unstrikeable` GitHub org: later, not urgent (the repo can be transferred without loss).
- Release tags and an enforced `runtime:` range (today every instance follows `main`).
- Pool labels: swap the pool label for the named one when an agent takes the item (§3).
- A separate `Forge` interface, once a board that is not on the forge is needed (§5).
- Proposed, to decide: `uns init` (starter config repo), `auto_merge` per department, trusted authors whose
  items can be assigned without a human label.
