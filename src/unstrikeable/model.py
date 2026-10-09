"""Board data model, independent of any backend."""
from __future__ import annotations

from dataclasses import dataclass, field

VETTING = "needs:vetting"                                           # outside content waits for a member's go
BLOCKING = frozenset({"agent:pause", "needs:human", "agent:lost", VETTING})   # agents never touch these items
SPEC_QUESTION = "spec:question"
AGENT_MARK = "<!-- uns:agent=%s -->"                                  # every agent comment carries it


@dataclass(frozen=True)
class Comment:
    id: int
    author: str
    trusted: bool                  # member of the org, or one of our agents
    agent: str | None = None       # agent that wrote it (from its marker), None for a human
    status: bool = False           # status comment (heartbeat), never a conversation turn
    body: str = ""
    created: str = ""              # ISO 8601 UTC (sorts as text)


@dataclass(frozen=True)
class PR:
    number: int
    url: str
    head: str
    mergeable: str = "UNKNOWN"     # MERGEABLE | CONFLICTING | UNKNOWN
    ci: str | None = None          # SUCCESS | FAILURE | PENDING | None


@dataclass(frozen=True)
class Item:
    repo: str
    number: int
    title: str
    state: str | None              # logical state key (None = not on a known column)
    labels: list[str] = field(default_factory=list)
    url: str = ""
    author: str = ""                  # login, lowercase, without "[bot]"
    author_trusted: bool = True
    author_agent: str | None = None   # set when one of our agents created the item
    blocked_by: int = 0
    comments: list[Comment] = field(default_factory=list)
    prs: list[PR] = field(default_factory=list)
    outsider_at: str = ""             # newest content from a non-member (body, comment, linked PR), ISO 8601

    @property
    def ref(self) -> str:
        return "%s#%d" % (self.repo, self.number)
