"""hermes-otel-usage — one OTLP log event per Hermes model call and tool call.

Covers what Claude Code CLI telemetry cannot see: profiles calling the model API directly,
subagents, auxiliary calls (titles, compression...). Calls routed through the directsdk plugin
are skipped (the `claude` CLI already exports them: no double counting).

Event format mirrors Claude Code events so the same Loki dashboards work:
resource service.name=hermes; event.name=api_request with model, cost_usd, input_tokens,
output_tokens, cache_read_tokens, cache_creation_tokens, query_source, hermes.profile,
hermes.platform, hermes.user_name, hermes.chat_id, hermes.thread_id; event.name=tool_result
with tool_name, success, action (pr_created, pr_merged, commit, push, deploy,
ticket_created...), lines_added/lines_removed. No content (prompts, replies, commands) is sent.

Config: reuses the process' OTEL_EXPORTER_OTLP_ENDPOINT / OTEL_EXPORTER_OTLP_HEADERS.
HERMES_OTEL_USAGE=0 disables it. Fail-open: never raises into the agent loop.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SKIP_PROVIDER_PREFIXES = ("claude-subscription-directsdk",)
_FLUSH_S = 10.0
_MAX_BATCH = 200

_profile = "default"
_q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=10000)
_worker: Optional[threading.Thread] = None
_lock = threading.Lock()
_stats = {"sent": 0, "failed": 0, "dropped": 0}


def _enabled() -> bool:
    return os.environ.get("HERMES_OTEL_USAGE", "1") not in ("0", "false", "off") and bool(
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def _headers() -> Dict[str, str]:
    out = {"Content-Type": "application/json"}
    raw = os.environ.get("OTEL_EXPORTER_OTLP_LOGS_HEADERS") or os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[urllib.parse.unquote(k.strip())] = urllib.parse.unquote(v.strip())
    return out


def _endpoint() -> str:
    ep = os.environ.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
    if ep:
        return ep
    return os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/") + "/v1/logs"


def _session_vars() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        from gateway.session_context import get_session_env
        for k in ("PLATFORM", "CHAT_ID", "CHAT_TYPE", "THREAD_ID", "USER_ID", "USER_NAME"):
            v = get_session_env("HERMES_SESSION_" + k)
            if v:
                out["hermes." + k.lower()] = str(v)
    except Exception:
        pass
    return out


def _cost(model: str, usage: Dict[str, Any], provider: str, base_url: str) -> Optional[float]:
    try:
        from agent.usage_pricing import CanonicalUsage, estimate_usage_cost
        cu = CanonicalUsage(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
            cache_read_tokens=int(usage.get("cache_read_tokens") or 0),
            cache_write_tokens=int(usage.get("cache_write_tokens") or 0),
            reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
            request_count=int(usage.get("request_count") or 1),
        )
        res = estimate_usage_cost(model, cu, provider=provider, base_url=base_url, api_key="")
        return None if res.amount_usd is None else float(res.amount_usd)
    except Exception as exc:  # unknown pricing: tokens are still sent
        logger.debug("hermes-otel-usage: pricing failed: %s", exc)
        return None


def _attr(k: str, v: Any) -> Dict[str, Any]:
    if isinstance(v, bool):
        return {"key": k, "value": {"boolValue": v}}
    if isinstance(v, int):
        return {"key": k, "value": {"intValue": str(v)}}
    if isinstance(v, float):
        return {"key": k, "value": {"doubleValue": v}}
    return {"key": k, "value": {"stringValue": str(v)}}


def _record(*, model: str, provider: str, base_url: str, usage: Any, query_source: str,
            session_id: str, platform: str, duration_s: float, finish_reason: Any) -> None:
    if not _enabled() or not isinstance(usage, dict):
        return
    if any(str(provider or "").startswith(p) for p in _SKIP_PROVIDER_PREFIXES):
        return
    cost = _cost(model, usage, provider, base_url)
    attrs: Dict[str, Any] = {
        "event.name": "api_request",
        "event.timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
        "model": model or "",
        "provider": provider or "",
        "query_source": query_source,
        "session.id": session_id or "",
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
        "cache_creation_tokens": int(usage.get("cache_write_tokens") or 0),
        "duration_ms": int(max(0.0, float(duration_s or 0)) * 1000),
        "hermes.profile": _profile,
        "terminal_type": "hermes",
    }
    if cost is not None:
        attrs["cost_usd"] = cost
    if finish_reason:
        attrs["finish_reason"] = str(finish_reason)
    sv = _session_vars()
    if platform and "hermes.platform" not in sv:
        sv["hermes.platform"] = platform
    attrs.update(sv)
    try:
        _q.put_nowait({"t": time.time_ns(), "attrs": attrs})
    except queue.Full:
        _stats["dropped"] += 1
    _ensure_worker()


def _ensure_worker() -> None:
    global _worker
    if _worker and _worker.is_alive():
        return
    with _lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=_run, name="hermes-otel-usage", daemon=True)
        _worker.start()


def _payload(batch: List[Dict[str, Any]]) -> bytes:
    res = [_attr("service.name", "hermes"), _attr("service.instance.id", f"hermes-{_profile}"),
           _attr("hermes.profile", _profile)]
    recs = [{"timeUnixNano": str(e["t"]), "observedTimeUnixNano": str(e["t"]),
             "severityText": "INFO", "body": {"stringValue": "hermes.api_request"},
             "attributes": [_attr(k, v) for k, v in e["attrs"].items()]} for e in batch]
    return json.dumps({"resourceLogs": [{"resource": {"attributes": res}, "scopeLogs": [
        {"scope": {"name": "hermes.otel_usage", "version": "0.1.0"}, "logRecords": recs}]}]}).encode()


def _send(batch: List[Dict[str, Any]]) -> None:
    if not batch:
        return
    try:
        req = urllib.request.Request(_endpoint(), data=_payload(batch), headers=_headers(), method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        _stats["sent"] += len(batch)
    except Exception as exc:
        _stats["failed"] += len(batch)
        logger.warning("hermes-otel-usage: export failed (%d events): %s", len(batch), exc)


def _drain(max_n: int = _MAX_BATCH) -> List[Dict[str, Any]]:
    out = []
    while len(out) < max_n:
        try:
            out.append(_q.get_nowait())
        except queue.Empty:
            break
    return out


def _run() -> None:
    while True:
        try:
            first = _q.get(timeout=_FLUSH_S)
        except queue.Empty:
            continue
        time.sleep(1.0)  # batch bursts
        _send([first] + _drain(_MAX_BATCH - 1))


def flush() -> None:
    while not _q.empty():
        _send(_drain())


atexit.register(flush)


# ---------------------------------------------------------------- hooks
def on_post_api_request(*, model: str = "", provider: str = "", base_url: str = "", usage: Any = None,
                        session_id: str = "", platform: str = "", api_duration: float = 0.0,
                        finish_reason: Any = None, response_model: Any = None, **_: Any) -> None:
    try:
        m = response_model if isinstance(response_model, str) and response_model else model
        src = "subagent" if platform == "subagent" else "main"
        _record(model=m, provider=provider, base_url=base_url, usage=usage, query_source=src,
                session_id=session_id, platform=platform, duration_s=api_duration, finish_reason=finish_reason)
    except Exception:
        logger.debug("hermes-otel-usage: post_api_request failed", exc_info=True)


def on_post_auxiliary_call(*, model: str = "", provider: str = "", base_url: str = "", usage: Any = None,
                           session_id: str = "", platform: str = "", aux_task: str = "", started_at: float = 0.0,
                           finish_reason: Any = None, response_model: Any = None, **_: Any) -> None:
    try:
        m = response_model if isinstance(response_model, str) and response_model else model
        dur = time.time() - started_at if started_at else 0.0
        _record(model=m, provider=provider, base_url=base_url, usage=usage, query_source=f"aux:{aux_task or 'other'}",
                session_id=session_id, platform=platform, duration_s=dur, finish_reason=finish_reason)
    except Exception:
        logger.debug("hermes-otel-usage: post_auxiliary_call failed", exc_info=True)


import re as _re

# Classify "output" actions without sending the command itself.
_GIT_RULES = [
    ("pr_created", _re.compile(r"\bgh\s+pr\s+create\b|create_pull_request")),
    ("pr_merged", _re.compile(r"\bgh\s+pr\s+merge\b|merge_pull_request")),
    ("pr_review", _re.compile(r"\bgh\s+pr\s+(review|comment)\b|pull_request_review|add_.*comment.*pull")),
    ("issue_created", _re.compile(r"\bgh\s+issue\s+create\b|create_issue")),
    ("commit", _re.compile(r"\bgit\s+(-C\s+\S+\s+)?commit\b")),
    ("push", _re.compile(r"\bgit\s+(-C\s+\S+\s+)?push\b")),
    ("deploy", _re.compile(r"\b(wrangler\s+deploy|terraform\s+apply|kubectl\s+apply|gcloud\s+run\s+deploy)\b")),
]
_CLICKUP_RULES = [("ticket_created", _re.compile(r"clickup_create_task$")),
                  ("ticket_updated", _re.compile(r"clickup_update_task$|clickup_create_comment$|clickup_create_task_comment$"))]
_EDIT_TOOLS = {"patch", "write_file", "mcp__patch", "mcp__write_file"}


def _actions(tool_name: str, args: Any) -> List[str]:
    acts: List[str] = []
    txt = ""
    if isinstance(args, dict):
        txt = str(args.get("command") or args.get("code") or "")
    for name, rx in _GIT_RULES:
        if rx.search(txt) or rx.search(tool_name):
            acts.append(name)
    for name, rx in _CLICKUP_RULES:
        if rx.search(tool_name):
            acts.append(name)
    return acts


def _diff_lines(result: Any) -> tuple:
    try:
        d = json.loads(result) if isinstance(result, str) else result
        diff = d.get("diff") if isinstance(d, dict) else None
        if not isinstance(diff, str):
            return 0, 0
        add = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
        rem = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
        return add, rem
    except Exception:
        return 0, 0


def on_post_tool_call(*, tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "",
                      duration_ms: int = 0, status: Optional[str] = None, error_type: Optional[str] = None,
                      **_: Any) -> None:
    try:
        if not _enabled():
            return
        attrs: Dict[str, Any] = {
            "event.name": "tool_result",
            "tool_name": tool_name or "",
            "success": "false" if status == "error" else "true",
            "duration_ms": int(duration_ms or 0),
            "session.id": session_id or "",
            "hermes.profile": _profile,
            "terminal_type": "hermes",
        }
        if error_type:
            attrs["error_type"] = str(error_type)
        if tool_name.startswith("mcp__"):
            parts = tool_name.split("__")
            if len(parts) >= 3:
                attrs["mcp_server_name"], attrs["mcp_tool_name"] = parts[1], "__".join(parts[2:])
        acts = _actions(tool_name, args)
        if acts and status != "error":
            attrs["action"] = acts[0]
        if tool_name in _EDIT_TOOLS and status != "error":
            a, r = _diff_lines(result)
            attrs["lines_added"], attrs["lines_removed"] = a, r
        attrs.update(_session_vars())
        try:
            _q.put_nowait({"t": time.time_ns(), "attrs": attrs})
        except queue.Full:
            _stats["dropped"] += 1
        _ensure_worker()
    except Exception:
        logger.debug("hermes-otel-usage: post_tool_call failed", exc_info=True)


def register(ctx) -> None:
    global _profile
    try:
        _profile = ctx.profile_name or "default"
    except Exception:
        pass
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("post_auxiliary_call", on_post_auxiliary_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
