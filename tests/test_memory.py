"""Memory: private notes, inbox of proposals, curated shared knowledge (all in the config repo)."""
import subprocess

import pytest

from unstrikeable.memory import commit_and_push, curation_due, read_memory, slugify, write_entry

T0 = 1_791_000_000          # 2026-10-03


def test_slugify():
    assert slugify("SageMaker: g5 quota is 0 in 4 regions!") == "sagemaker-g5-quota-is-0-in-4-regions"


def test_private_entry_goes_to_the_agent_folder(tmp_path):
    p = write_entry(tmp_path, "kevin", "LinkedIn hook length", "Keep hooks under 2 lines.", share=False, now=T0)
    assert p.parent == tmp_path / "memory" / "agents" / "kevin"
    assert p.name.endswith("-linkedin-hook-length.md")
    assert "Keep hooks under 2 lines." in p.read_text()


def test_shared_entry_goes_to_the_inbox(tmp_path):
    p = write_entry(tmp_path, "kevin", "X limit", "280 chars.", share=True, now=T0)
    assert p.parent == tmp_path / "memory" / "inbox" / "kevin"


def test_same_title_twice_never_overwrites(tmp_path):
    a = write_entry(tmp_path, "kevin", "note", "one", share=False, now=T0)
    b = write_entry(tmp_path, "kevin", "note", "two", share=False, now=T0)
    assert a != b and a.read_text() != b.read_text()


def test_secrets_are_refused(tmp_path):
    with pytest.raises(ValueError, match="looks like a secret"):
        write_entry(tmp_path, "kevin", "token", "ghp_" + "a" * 36, share=False, now=T0)


def test_agent_reads_shared_and_its_own_notes_only(tmp_path):
    write_entry(tmp_path, "kevin", "mine", "kevin private", share=False, now=T0)
    write_entry(tmp_path, "brandon", "his", "brandon private", share=False, now=T0)
    write_entry(tmp_path, "brandon", "proposal", "brandon inbox", share=True, now=T0)
    (tmp_path / "memory" / "shared").mkdir(parents=True)
    (tmp_path / "memory" / "shared" / "channels.md").write_text("Team knowledge")
    shared, private, warnings = read_memory(tmp_path, "kevin", {"shared_max_words": 100, "private_max_words": 100})
    assert "Team knowledge" in shared and "kevin private" in private
    assert "brandon" not in shared + private and warnings == []


def test_memory_over_its_cap_warns_the_owner(tmp_path):
    write_entry(tmp_path, "kevin", "long", "word " * 50, share=False, now=T0)
    _, _, warnings = read_memory(tmp_path, "kevin", {"shared_max_words": 100, "private_max_words": 10})
    assert warnings and "condense" in warnings[0]


def test_curation_is_due_when_the_inbox_is_full_enough(tmp_path):
    for i in range(3):
        write_entry(tmp_path, "kevin", "n%d" % i, "x", share=True, now=T0)
    assert curation_due(tmp_path, pending=[], inbox_max=3, max_age_h=24, now=T0) is not None
    assert curation_due(tmp_path, pending=[], inbox_max=4, max_age_h=24, now=T0) is None


def test_old_entries_are_curated_even_if_few(tmp_path):
    write_entry(tmp_path, "kevin", "lonely", "x", share=True, now=T0)
    assert curation_due(tmp_path, pending=[], inbox_max=10, max_age_h=24, now=T0 + 25 * 3600) is not None


def test_entries_already_handed_to_the_curator_do_not_count(tmp_path):
    files = [str(write_entry(tmp_path, "kevin", "n%d" % i, "x", share=True, now=T0)
                 .relative_to(tmp_path)) for i in range(3)]
    assert curation_due(tmp_path, pending=files, inbox_max=3, max_age_h=24, now=T0) is None


# ---------------------------------------------------------------- git (real repos)
def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def clones(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    a, b = tmp_path / "a", tmp_path / "b"
    for c in (a, b):
        git(tmp_path, "clone", "-q", str(remote), str(c))
        git(c, "config", "user.email", "t@t")
        git(c, "config", "user.name", "t")
    (a / "config.yml").write_text("agents: {}\n")
    git(a, "add", "-A")
    git(a, "commit", "-qm", "init")
    git(a, "push", "-q", "origin", "main")
    git(b, "pull", "-q", "origin", "main")
    return a, b


def test_two_agents_push_memory_concurrently_without_conflict(clones):
    a, b = clones
    pa = write_entry(a, "kevin", "from a", "a", share=True, now=T0)
    pb = write_entry(b, "brandon", "from b", "b", share=True, now=T0)
    commit_and_push(a, [pa], "memory: kevin")
    commit_and_push(b, [pb], "memory: brandon")          # behind origin: rebases, then pushes
    git(a, "pull", "-q", "--rebase", "origin", "main")
    assert (a / pb.relative_to(b)).exists()


def test_commit_refuses_paths_outside_memory(clones):
    a, _ = clones
    with pytest.raises(ValueError, match="outside memory/"):
        commit_and_push(a, [a / "config.yml"], "nope")
