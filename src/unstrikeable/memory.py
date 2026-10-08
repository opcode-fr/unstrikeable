"""Memory in the config repo: memory/agents/<a>/ (private), memory/inbox/<a>/ (to share), memory/shared/ (curated)."""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

SECRET_RE = re.compile(
    r"(ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|xox[abpr]-[A-Za-z0-9-]{10,}"
    r"|sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
CREATED_RE = re.compile(r"^created: (\d+)$", re.M)


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "note"


def mem(root: Path) -> Path:
    return Path(root) / "memory"


def write_entry(root: Path, agent: str, title: str, body: str, share: bool, now: int | None = None) -> Path:
    """One file per entry, never overwritten. Refuses anything that looks like a secret."""
    if SECRET_RE.search(title + "\n" + body):
        raise ValueError("this looks like a secret: memory is readable by anyone with access to the config repo")
    now = int(time.time()) if now is None else now
    folder = mem(root) / ("inbox" if share else "agents") / agent
    folder.mkdir(parents=True, exist_ok=True)
    base = "%s-%s" % (time.strftime("%Y-%m-%d", time.localtime(now)), slugify(title))
    path, n = folder / (base + ".md"), 2
    while path.exists():
        path, n = folder / ("%s-%d.md" % (base, n)), n + 1
    path.write_text("---\ntitle: %s\nauthor: %s\ncreated: %d\n---\n%s\n" % (title, agent, now, body.strip()))
    return path


def _files(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*.md")) if folder.exists() else []


def _section(files: list[Path], root: Path) -> str:
    return "\n\n".join("### %s\n%s" % (f.relative_to(root), f.read_text().strip()) for f in files)


def read_memory(root: Path, agent: str, caps: dict) -> tuple[str, str, list[str]]:
    """(shared text, the agent's private text, warnings). Never another agent's notes or the inbox."""
    root = Path(root)
    shared = _section(_files(mem(root) / "shared"), root)
    private = _section(_files(mem(root) / "agents" / agent), root)
    warnings = []
    if len(private.split()) > caps.get("private_max_words", 1500):
        warnings.append("Your private memory is over %d words: condense it (merge, drop what is stale)."
                        % caps.get("private_max_words", 1500))
    if len(shared.split()) > caps.get("shared_max_words", 3000):
        warnings.append("Shared memory is over %d words: the curator must condense it."
                        % caps.get("shared_max_words", 3000))
    return shared, private, warnings


def inbox(root: Path) -> list[str]:
    root = Path(root)
    return [str(f.relative_to(root)) for f in _files(mem(root) / "inbox")]


def _created(path: Path) -> int:
    m = CREATED_RE.search(path.read_text())
    return int(m.group(1)) if m else int(path.stat().st_mtime)


def curation_due(root: Path, pending: list[str], inbox_max: int, max_age_h: float, now: int) -> list[str] | None:
    """Inbox entries the curator should process now (None = not yet). `pending` = already handed over."""
    todo = [f for f in inbox(root) if f not in set(pending)]
    if not todo:
        return None
    oldest = min(_created(Path(root) / f) for f in todo)
    if len(todo) >= inbox_max or now - oldest > max_age_h * 3600:
        return todo
    return None


def _git(root: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args[:2]), (p.stderr or p.stdout).strip()[:400]))
    return p.stdout


def pull(root: Path) -> None:
    """Best effort: keep the local clone of the config repo fresh (no-op outside a git repo)."""
    if (Path(root) / ".git").exists():
        try:
            _git(Path(root), "pull", "-q", "--rebase", "--autostash")
        except RuntimeError:
            pass


def commit_and_push(root: Path, paths: list[Path], message: str, retries: int = 2) -> None:
    """Commit memory files straight to the default branch. Unique file names: a rebase never conflicts."""
    root = Path(root).resolve()
    rels = []
    for p in paths:
        rel = Path(p).resolve().relative_to(root)
        if rel.parts[0] != "memory":
            raise ValueError("%s is outside memory/: agents only push their memory" % rel)
        rels.append(str(rel))
    _git(root, "add", "--", *rels)
    _git(root, "commit", "-q", "-m", message, "--", *rels)
    for attempt in range(retries + 1):
        try:
            _git(root, "pull", "-q", "--rebase", "--autostash")
            _git(root, "push", "-q")
            return
        except RuntimeError:
            if attempt == retries:
                raise
