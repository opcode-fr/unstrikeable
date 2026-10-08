"""Admin helpers: version pin, update, GitHub App manifest."""
import json

import pytest

from unstrikeable.admin import app_form, app_exchange, update, version_ok


@pytest.mark.parametrize("version,spec,ok", [
    ("0.1.0", ">=0.1,<0.2", True),
    ("0.1.3", ">=0.1,<0.2", True),
    ("0.2.0", ">=0.1,<0.2", False),
    ("0.1.0.dev0", ">=0.1,<0.2", True),         # pre-releases compare on their release numbers
    ("0.1.0", "==0.1.0", True),
    ("1.0", "", True),
])
def test_version_pin(version, spec, ok):
    assert version_ok(version, spec) is ok


def test_bad_pin_is_an_error():
    with pytest.raises(ValueError, match="bad version spec"):
        version_ok("0.1", "~0.1")


def test_app_form_posts_a_manifest_to_the_org(tmp_path):
    out = app_form("acme", "kevin", tmp_path / "f.html")
    page = out.read_text()
    assert 'action="https://github.com/organizations/acme/settings/apps/new"' in page
    assert "kevin-acme" in page and "organization_projects" in page


def test_app_exchange_stores_the_key_privately_and_prints_no_secret(tmp_path):
    def fake_post(url):
        assert url.endswith("/app-manifests/CODE/conversions")
        return {"id": 42, "slug": "kevin-acme", "pem": "-----BEGIN RSA PRIVATE KEY-----\nxx"}

    info = app_exchange("CODE", "kevin", tmp_path, post=fake_post)
    pem = tmp_path / "keys" / "kevin.pem"
    assert pem.read_text().startswith("-----BEGIN") and oct(pem.stat().st_mode)[-3:] == "600"
    assert info["app_id"] == 42 and "BEGIN" not in json.dumps(info)
    assert info["install_url"] == "https://github.com/apps/kevin-acme/installations/new"


def test_update_upgrades_copies_skills_and_dry_runs_each_agent(tmp_path):
    calls = []
    skills = tmp_path / "profile-skills"
    local = {"agents": {"kevin": {}, "jeanmichel": {}}, "skills_dirs": [str(skills)]}
    report = update(local, pin=">=0.1,<0.2", run=lambda args: calls.append(args) or "",
                    version=lambda: "0.1.2")
    assert calls[0][:3] == ["uv", "tool", "upgrade"]
    assert (skills / "unstrikeable-agent" / "SKILL.md").exists()
    assert ["uns", "poll", "--agent", "kevin", "--dry-run"] in calls
    assert "runtime 0.1.2 matches >=0.1,<0.2" in report


def test_update_reports_a_runtime_outside_the_pin(tmp_path):
    report = update({"agents": {}}, pin="<0.1", run=lambda args: "", version=lambda: "0.1.2")
    assert "does NOT match" in report
