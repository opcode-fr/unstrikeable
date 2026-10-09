"""uns init: a starter config repo that passes `uns check` as generated."""
import pytest

from unstrikeable.config import load_company
from unstrikeable.init import init


def test_generated_repo_loads_and_names_the_org(tmp_path):
    root = tmp_path / "acme-hq"
    written = init(root, org="acme", flow="dev")
    co = load_company(root)
    d = co.departments["rnd"]
    assert d.flow.name == "dev" and d.board["owner"] == "acme"
    assert {"config.yml", "culture.md", "README.md", ".gitignore"} <= {p.name for p in written}
    for sub in ("agents", "memory/shared", "memory/inbox", "memory/agents"):
        assert (root / sub).is_dir()


def test_content_flow_gets_a_marketing_department(tmp_path):
    init(tmp_path / "x", org="acme", flow="content")
    assert load_company(tmp_path / "x").departments["marketing"].flow.name == "content"


def test_secrets_are_ignored_by_git(tmp_path):
    init(tmp_path / "x", org="acme")
    ignored = (tmp_path / "x" / ".gitignore").read_text().split()
    assert "local.yml" in ignored and "*.pem" in ignored and ".env" in ignored


def test_config_is_block_style_and_commented(tmp_path):
    init(tmp_path / "x", org="acme")
    text = (tmp_path / "x" / "config.yml").read_text()
    assert "{" not in text.replace("{agent}", "") and "[" not in text
    assert "# " in text and "trusted_authors" in text


def test_refuses_a_non_empty_directory(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "keep.txt").write_text("mine")
    with pytest.raises(FileExistsError):
        init(tmp_path / "x", org="acme")
    assert (tmp_path / "x" / "keep.txt").read_text() == "mine"


def test_unknown_flow_is_refused(tmp_path):
    with pytest.raises(ValueError, match="flow"):
        init(tmp_path / "x", org="acme", flow="sales")
