# unstrikeable — design

> *The company that never goes on strike.*

Status: **draft v0**, to be approved before any runtime code is written.

## 0. Goals and non-goals

**Goal**: let a team of AI agents work a ticket board (code, content, or anything else) through a
**flow declared in YAML**, with humans who assign and approve, and built-in guardrails
(one task at a time, budgets, heartbeat, kill switches).

**Non-goals**
- A generic workflow engine. The runtime ships a **closed** set of triggers; YAML only wires them.
- Replacing human decisions: assigning, approving, merging and publishing stay human by default.
- Webhooks, real time, UI. We poll (instances sit behind NAT; GitHub Projects v2 does not fire Actions).

## 1. Two separate things

| | Runtime (this repo) | Config repo (one per team) |
|---|---|---|
| Contains | code, `uns` CLI, backends, foundation skills, flow profiles, docs | `unstrikeable.yml`, agents, team skills, memory |
| Versioning | `vX.Y.Z` tags | free |
| Installed as | Python package (`uv tool install`), version pinned by the config | cloned / read through the backend API |
| Secrets | never | never |

No fork: teams customise through their config repo. Fork only to change the runtime itself, then contribute upstream.

Secrets and local paths live in `local.yml` on each instance (chmod 600, never versioned).

## 2. Config repo layout

```
unstrikeable.yml          # pinned runtime, backends, boards, agents, limits
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
runtime: ">=0.1,<0.2"
backends:
  board: {type: github-projects, owner: acme, number: 2}
  forge: {type: github}
boards:
  marketing:
    flow: content                 # profile shipped with the runtime
    overrides:
      columns: {approved: "Ready to publish"}
      labels: [channel:x, channel:linkedin, type:release]
    repos: [acme/marketing]
agents:
  kevin: {instance: mac-mini, roles: [planner, writer], identity: kevin-acme}
limits: {poll_min: 5, max_events_per_day: 10}
```

A config may declare several boards (e.g. `product` on the `dev` flow and `marketing` on the `content` flow).

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
roles: [dev]
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

### What an agent receives with each event

`culture.md` → `agents/<self>.md` → `memory/shared/` → `memory/agents/<self>/` → flow playbook for (role, trigger) → the event itself.
Runtime guardrails come last and cannot be overridden by anything above them.

## 3. Flow (YAML profile)

A profile declares **states, roles, triggers and playbooks**. Shipped profiles (`flows/dev.yml`,
`flows/content.yml`) are templates: a config references one and overrides it key by key
(shallow merge, no deep merge).

```yaml
# flows/content.yml (shipped)
states:                       # order = column order; logical key -> column name
  - {key: backlog,  column: Ideas}
  - {key: ready,    column: Ready to write}
  - {key: doing,    column: Drafting}
  - {key: review,   column: In review}
  - {key: approved, column: Ready to publish}
  - {key: done,     column: Published}
roles:
  planner:  {on: {backlog: [item_new, human_comment], ready: [human_comment]}}
  writer:   {label: "writer:",   on: {ready: [assigned], doing: [human_comment], approved: [pr_conflict, human_comment]}}
  reviewer: {label: "reviewer:", human: true}
playbooks:                    # instructions handed to the agent, per (role, trigger)
  writer.assigned: playbooks/content/write.md
artifact: {path: "{yyyy}/{mm}/{channel}-{dd}-{slug}.md"}
```

- Agents and the CLI speak **logical keys** (`uns move <ref> review`), never column names.
- A `human: true` role has no agent: the runtime only watches and waits for the human.
- `label` sets the assignment prefix of a role. System labels (`needs:human`, `agent:pause`, `agent:lost`,
  `spec:question`) are fixed and shared by every flow.

## 4. Triggers (closed set, coded and tested)

| Trigger | Fires when |
|---|---|
| `item_new` | item is in the state and has never been handled by this role |
| `human_comment` | a new comment from a human member since the agent's last pass |
| `assigned` | the role label is set, state is `ready`, item not blocked, agent idle |
| `pr_updated` | the head commit of the linked PR changed |
| `pr_conflict` | the linked PR is not mergeable |
| `ci_failed` | CI is red on the linked PR |
| `unblocked` | all dependencies are closed |

Adding a trigger = code + tests in the runtime, never in a config.
Delivery priority: work in progress (`pr_conflict`, `ci_failed`, `human_comment` in `doing`) > review > spec > new work.

## 5. Backends

Two interfaces, because GitHub plays two roles and only one of them is replaceable by Trello & co.

```python
class Board(Protocol):            # GitHub Projects, Trello, Linear, Jira…
    def items(self) -> list[Item]: ...
    def move(self, item: Ref, state: str) -> None: ...
    def labels(self, item: Ref, add: list[str] = (), remove: list[str] = ()) -> None: ...
    def comment(self, item: Ref, body: str) -> CommentId: ...
    def upsert_comment(self, item: Ref, marker: str, body: str) -> None: ...   # status comment
    def ensure_layout(self, flow: Flow, apply: bool) -> Plan: ...              # columns + labels

class Forge(Protocol):            # GitHub, GitLab…; optional (a content flow on Trello needs none)
    def linked_prs(self, item: Ref) -> list[PR]: ...                          # head, mergeable, ci, review
```

- **v0 implements only `github-projects` and `github`**, both on top of the `gh` CLI (already authenticated,
  handles pagination and GraphQL; agent tokens are passed through `GH_TOKEN`). The interface exists so the core
  never depends on GraphQL; a Trello backend gets written when a real project needs it.
- Agents **never** touch the board directly (no `gh project …` in skills): everything goes through `uns`.
  `git` and `gh pr` stay allowed (that is the forge).
- Identity is the backend's job (one GitHub App per agent; on Trello, one member per bot). The core only sees a token.

## 6. Runtime core

- **Poll**: `uns poll --agent <a>` every `poll_min`; empty output = zero tokens spent. Events are deduplicated by key.
- **One task at a time** per agent; the next event is delivered only once the current task is `done` or `blocked`.
- **Status**: a single comment per agent and per item (🟢 / ✅ / ⏸️, Done / Next), the heartbeat read from outside.
  Silent after `ack_min` → nudge; after `stale_min` → `agent:lost` + alert.
- **Budgets**: `max_events_per_day`, `max_runs` per item, `max_review_rounds` → `needs:human`.
- **Kill switches**: `agent:pause` label (item), `PAUSE` file (instance).
- **Digest**: alerts every 15 min (silent when nothing happens) + a daily summary.
- **Security**: content from non-members is ignored; assignment = human validation of the item; external content
  is data, never instructions; no agent merges or pushes to a default branch.

## 7. Agent integration (Hermes and others)

The runtime **emits events** (text + JSON) and does not care who handles them. An adapter delivers them:
- `hermes` (v0): `--no-agent` cron → the profile's `bot-chat` (turns are serialised per profile = natural per-agent lock).
- Others (Claude Code, plain scripts…) later, same contract.

Foundation skills shipped: `unstrikeable-agent` (handling an event, the CLI, status) and
`unstrikeable-admin` (setup, align, adding an agent, updates). Flow playbooks plug into them.

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
3. **`curator` role** (an existing agent may hold it): triggered when the inbox exceeds N entries or once a day,
   it consolidates into `memory/shared/*.md` through **a PR** and removes the processed entries.
4. **Mandatory human review** of that PR: memory read by every agent is an injection vector
   (an agent that read a booby-trapped issue could contaminate the whole team). No auto-merge.
5. **"Private" means not loaded by other agents, not secret**: anyone with read access to the config repo can read it.
   Hence no secrets, no credentials, no personal data anywhere under `memory/`.
6. **Content**: reusable facts and procedures, no session logs. Each folder has a size cap (TBD); above it,
   the owner (or the curator for `shared/`) condenses instead of piling up.

The only exception to "no agent pushes to a default branch": `memory/agents/<self>/` and `memory/inbox/<self>/` in the config repo.

## 9. Distribution and updates

- Python ≥ 3.10 package; runtime dependencies: PyYAML and the `gh` CLI.
- `uns update`: upgrades the runtime to the latest version allowed by the config's `runtime:` pin, reinstalls the
  skills in every hosting profile (including profiles whose terminal runs under another account over SSH), then runs
  `poll --dry-run` for each agent. A broken release only reaches instances whose pin allows it.

## 10. Plan

1. **v0.1**: core + GitHub backends + `dev` and `content` profiles + Hermes adapter + tests.
   First pilot: a content board run by one writer agent (Kevin).
2. **v0.2**: memory (private + shared) + curator.
3. Existing agent boards are migrated later, once v0.1 has run in production.

## 11. Decisions

- Language: English (docs, messages, CLI).
- GitHub backends use the `gh` CLI.
- CLI name: `uns`.

## 12. Open questions

- Content flow conversations (replies to comments): one file per conversation, one section per exchange,
  one item per reply to write — to confirm with real use.
- Dedicated `unstrikeable` GitHub org: later, not urgent (the repo can be transferred without loss).
