# hermes-otel-usage

Hermes plugin: one OTLP log event per model call (`api_request`) and per tool call (`tool_result`) of a
Hermes profile, so agent cost and output show up next to Claude Code telemetry (Grafana, any OTLP backend).

Why: Hermes profiles that call the model API directly (not through the Claude Code CLI) emit no telemetry,
so dashboards built on Claude Code events miss them. `uns report` covers board work from `state.db`; this
plugin covers everything the profile does (Slack, cron, subagents), live.

## What is sent

- `api_request`: `model`, `cost_usd` (Hermes' list-price estimate, not the bill), `input_tokens`,
  `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `duration_ms`, `query_source`
  (`main`, `subagent`, `aux:<task>`), `hermes.profile`, `hermes.platform`, `hermes.user_name`,
  `hermes.chat_id`, `hermes.thread_id`.
- `tool_result`: `tool_name`, `success`, `duration_ms`, `mcp_server_name`/`mcp_tool_name`, `action`
  (`pr_created`, `pr_merged`, `pr_review`, `issue_created`, `commit`, `push`, `deploy`, `ticket_created`,
  `ticket_updated`), `lines_added`/`lines_removed` for file edits.
- Resource: `service.name=hermes`, `service.instance.id=hermes-<profile>`.

Never sent: prompts, replies, tool arguments or results. Commands are only matched against fixed patterns.
Calls routed through the `claude-subscription-directsdk` provider are skipped (the CLI exports them already).

## Install (per Hermes home)

```sh
cp -r integrations/hermes/otel-usage <hermes home>/plugins/hermes-otel-usage
# for each profile: link it and enable it
ln -sfn <hermes home>/plugins/hermes-otel-usage <hermes home>/profiles/<p>/plugins/hermes-otel-usage
```
In each profile's `config.yaml` (and the root one for the default profile):
```yaml
plugins:
  enabled:
    - hermes-otel-usage
```
The process needs `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_EXPORTER_OTLP_HEADERS` (same values as for Claude
Code; a write-only token). Restart the gateway, or reload plugins without restart through the gateway control
socket (`gateway.control_socket.reload_gateway_plugins`). `HERMES_OTEL_USAGE=0` turns it off.

Check it is live under the profile (a reload can report success while config disables the plugin):
```sh
HERMES_HOME=<hermes home>/profiles/<p> python -c "from hermes_cli.plugins import discover_plugins, get_plugin_manager; \
from hermes_cli.lifecycle import has_hook; discover_plugins(force=True); \
print(get_plugin_manager()._plugins['hermes-otel-usage'].enabled, has_hook('post_api_request'))"
```

## Limits

- No history: Grafana Cloud Loki rejects events older than a few hours, so past usage stays in `state.db`.
- Profile distributions that own `config.yaml` overwrite the `plugins.enabled` entry on reinstall: put it in the
  distribution's own `config.yaml`.
- A new profile needs the link and the config entry.
