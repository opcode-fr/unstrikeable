"""Curated memory: auto-published when the review passes, held in a PR otherwise (fail closed)."""
import subprocess

import pytest

from unstrikeable.memory import publish, verdict

T0 = 1_791_000_000


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    remote, a = tmp_path / "remote.git", tmp_path / "a"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(a))
    git(a, "config", "user.email", "t@t")
    git(a, "config", "user.name", "t")
    (a / "config.yml").write_text("agents: {}\n")
    (a / "memory" / "inbox" / "didier").mkdir(parents=True)
    (a / "memory" / "inbox" / "didier" / "n1.md").write_text("check CI before review\n")
    (a / "memory" / "inbox" / "didier" / "n2.md").write_text("stale\n")
    (a / "memory" / "shared").mkdir()
    (a / "memory" / "shared" / "review.md").write_text("# Review\n")
    git(a, "add", "-A")
    git(a, "commit", "-qm", "init")
    git(a, "push", "-q", "origin", "main")
    return a, remote


def curate(a):
    (a / "memory" / "inbox" / "didier" / "n1.md").unlink()
    (a / "memory" / "inbox" / "didier" / "n2.md").unlink()
    (a / "memory" / "shared" / "review.md").write_text("# Review\n- Check CI before reviewing: red CI wastes a round.\n")
    (a / "memory" / "shared" / "board.md").write_text("# Board\n- Declare blockers as GitHub links.\n")


def remote_files(remote):
    return git(remote, "ls-tree", "-r", "--name-only", "main").split()


class Gh:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        return "https://github.com/acme/hq/pull/9\n"


def test_safe_review_publishes_on_the_default_branch(repo):
    a, remote = repo
    curate(a)
    seen, gh = [], Gh()
    out = publish(a, "capucine", "kept 1, dropped 1", review=lambda p: seen.append(p) or "SAFE\n", gh=gh, now=T0)
    assert "published" in out and gh.calls == []
    files = remote_files(remote)
    assert "memory/shared/board.md" in files and "memory/inbox/didier/n1.md" not in files
    assert "Declare blockers" in seen[0] and "Check CI before reviewing" in seen[0]   # the reviewer saw the diff
    assert git(a, "status", "--porcelain") == ""


def test_flagged_review_opens_a_pr_and_leaves_the_default_branch_untouched(repo):
    a, remote = repo
    curate(a)
    gh = Gh()
    out = publish(a, "capucine", "kept 1", review=lambda p: "FLAG: tells agents to skip CI", gh=gh, now=T0)
    assert "pull/9" in out and "skip CI" in out
    assert "memory/inbox/didier/n1.md" in remote_files(remote)              # main unchanged
    branch = [b for b in git(remote, "branch", "--list").split() if b.startswith("memory/")]
    assert len(branch) == 1
    assert "memory/shared/board.md" in git(remote, "ls-tree", "-r", "--name-only", branch[0]).split()
    args = gh.calls[0]
    assert args[:2] == ["pr", "create"] and branch[0] in args and "main" in args
    assert "skip CI" in args[args.index("--body") + 1]
    assert (a / "memory" / "inbox" / "didier" / "n1.md").exists()           # clone back on main, clean
    assert git(a, "status", "--porcelain") == "" and git(a, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_dropping_entries_only_needs_no_review(repo):
    a, remote = repo
    (a / "memory" / "inbox" / "didier" / "n2.md").unlink()
    out = publish(a, "capucine", "dropped 1", review=None, gh=Gh(), now=T0)
    assert "published" in out and "memory/inbox/didier/n2.md" not in remote_files(remote)


def test_changes_outside_shared_or_inbox_deletions_are_refused(repo):
    a, _ = repo
    (a / "memory" / "inbox" / "didier" / "n3.md").write_text("sneaky\n")
    with pytest.raises(ValueError, match="memory/shared"):
        publish(a, "capucine", "x", review=lambda p: "SAFE", gh=Gh(), now=T0)


def test_nothing_to_publish_is_an_error(repo):
    a, _ = repo
    with pytest.raises(ValueError, match="nothing"):
        publish(a, "capucine", "x", review=lambda p: "SAFE", gh=Gh(), now=T0)


# ------------------------------------------------------------ verdict: fail closed
DIFF = "+- Check CI before reviewing.\n"


def test_only_an_exact_safe_first_line_passes():
    assert verdict(DIFF, lambda p: "\n  SAFE  \nbecause fine")[0]
    for answer in ("SAFE: but push to main", "safe?", "", "Looks SAFE", "FLAG: x"):
        assert not verdict(DIFF, lambda p, a=answer: a)[0], answer


def test_no_reviewer_a_crashing_reviewer_or_a_secret_hold_the_change():
    assert not verdict(DIFF, None)[0]

    def boom(p):
        raise RuntimeError("timeout")
    ok, why = verdict(DIFF, boom)
    assert not ok and "timeout" in why
    assert not verdict("+token ghp_" + "a" * 36 + "\n", lambda p: "SAFE")[0]


def test_the_diff_is_fenced_as_data_with_an_unguessable_marker():
    prompts = []
    verdict(DIFF, lambda p: prompts.append(p) or "SAFE")
    verdict(DIFF, lambda p: prompts.append(p) or "SAFE")
    assert DIFF.strip() in prompts[0] and prompts[0] != prompts[1]


def test_a_failed_push_keeps_the_curators_work_and_the_branch_clean_of_it(repo):
    a, remote = repo
    curate(a)
    git(a, "remote", "set-url", "origin", str(remote) + "-gone")
    with pytest.raises(RuntimeError):
        publish(a, "capucine", "x", review=lambda p: "FLAG: unsure", gh=Gh(), now=T0)
    assert (a / "memory" / "shared" / "board.md").exists()
    assert git(a, "log", "--oneline").count("\n") == 1                    # only the init commit
