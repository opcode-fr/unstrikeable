"""Admin helpers: runtime version pin, `uns update`, GitHub App creation (manifest flow)."""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Callable

SKILLS = Path(__file__).parent / "skills"
PERMISSIONS = {
    "issues": "write",
    "pull_requests": "write",
    "contents": "write",
    "checks": "read",
    "statuses": "read",
    "metadata": "read",
    "organization_projects": "write",
}


# ---------------------------------------------------------------- version pin
def _release(v: str) -> tuple[int, ...]:
    m = re.match(r"\d+(\.\d+)*", v.strip())
    if not m:
        raise ValueError("bad version %r" % v)
    return tuple(int(x) for x in m.group(0).split("."))


def _cmp(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def version_ok(version: str, spec: str) -> bool:
    """Minimal PEP 440-like check: comma-separated >=, >, <=, <, ==, != on release numbers only."""
    v = _release(version)
    for clause in filter(None, (c.strip() for c in (spec or "").split(","))):
        m = re.fullmatch(r"(>=|<=|==|!=|>|<)\s*([\d.]+)", clause)
        if not m:
            raise ValueError("bad version spec %r" % clause)
        c = _cmp(v, _release(m.group(2)))
        if not {">=": c >= 0, "<=": c <= 0, "==": c == 0, "!=": c != 0, ">": c > 0, "<": c < 0}[m.group(1)]:
            return False
    return True


def installed_version() -> str:
    try:
        return metadata.version("unstrikeable")
    except metadata.PackageNotFoundError:
        return "0"


def _run(args: list[str]) -> str:
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("%s: %s" % (" ".join(args[:3]), (p.stderr or p.stdout).strip()[:500]))
    return p.stdout


# ---------------------------------------------------------------- update
def update(local: dict, pin: str, run: Callable[[list[str]], str] = _run,
           version: Callable[[], str] = installed_version) -> str:
    """Upgrade the runtime, reinstall the shipped skills, dry-run every hosted agent. Returns a report."""
    report = []
    run(["uv", "tool", "upgrade", "unstrikeable"])
    v = version()
    report.append("runtime %s %s %s" % (v, "matches" if version_ok(v, pin) else "does NOT match", pin or "(no pin)"))
    for d in local.get("skills_dirs") or []:
        dest = Path(os.path.expanduser(d))
        for skill in sorted(p for p in SKILLS.iterdir() if p.is_dir()):
            shutil.copytree(skill, dest / skill.name, dirs_exist_ok=True)
            report.append("skill %s -> %s" % (skill.name, dest))
    for agent in sorted(local.get("agents") or {}):
        try:
            run(["uns", "poll", "--agent", agent, "--dry-run"])
            report.append("poll --dry-run %s: ok" % agent)
        except RuntimeError as e:
            report.append("poll --dry-run %s: FAILED %s" % (agent, e))
    return "\n".join(report)


# ---------------------------------------------------------------- GitHub App (manifest flow)
def app_form(org: str, agent: str, out: Path) -> Path:
    """HTML page an org owner opens to create the agent's GitHub App in one click."""
    manifest = {
        "name": "%s-%s" % (agent, org),
        "url": "https://github.com/%s" % org,
        "description": "unstrikeable agent %s" % agent,
        "public": False,
        "redirect_url": "https://github.com/%s" % org,
        "hook_attributes": {"url": "https://example.invalid/unused", "active": False},
        "default_permissions": PERMISSIONS,
        "default_events": [],
    }
    out = Path(out)
    out.write_text("""<!doctype html><meta charset="utf-8"><title>Create %(name)s</title>
<p>Create the GitHub App <b>%(name)s</b> for <b>%(org)s</b>. Click, confirm on GitHub, then copy the
<code>code=</code> value from the address bar of the page you land on (valid 1 hour, single use).</p>
<form action="https://github.com/organizations/%(org)s/settings/apps/new" method="post">
<input type="hidden" name="manifest" value="%(m)s"><button type="submit">Create the App on GitHub</button></form>
""" % {"name": html.escape(manifest["name"]), "org": html.escape(org), "m": html.escape(json.dumps(manifest))})
    return out


def _post(url: str) -> dict:
    req = urllib.request.Request(url, method="POST", data=b"",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "unstrikeable"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def app_exchange(code: str, agent: str, home: Path, post: Callable[[str], dict] = _post) -> dict:
    """Run on the instance hosting the agent: the private key never leaves it. Prints no secret."""
    app = post("https://api.github.com/app-manifests/%s/conversions" % code)
    keys = Path(home) / "keys"
    keys.mkdir(parents=True, exist_ok=True)
    os.chmod(keys, 0o700)
    pem = keys / ("%s.pem" % agent)
    pem.write_text(app["pem"])
    os.chmod(pem, 0o600)
    return {"agent": agent, "app_id": app["id"], "slug": app["slug"], "app_key": str(pem),
            "install_url": "https://github.com/apps/%s/installations/new" % app["slug"]}
