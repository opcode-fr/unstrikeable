#!/bin/sh
# Kiro CLI cron wrapper: one unstrikeable turn for __AGENT__ (nothing happens when there is no event).
# Copy to ~/.unstrikeable/bin/uns_run___AGENT__.sh, replace __AGENT__, __UNS__ (absolute path of `uns`),
# __KIRO__ (absolute path of `kiro-cli`) and __WORKDIR__ (where the agent keeps its clones), chmod 700.
# Cron does not load your shell: PATH and UNS_HOME are set here, the Kiro API key comes from a chmod 600 file
# (one line: KIRO_API_KEY=...), never from the crontab.
# Never run it by hand to test: it consumes the event. Use `uns poll --agent __AGENT__ --dry-run`.
set -eu
export UNS_HOME="${UNS_HOME:-$HOME/.unstrikeable}"
export PATH="$(dirname __UNS__):$(dirname __KIRO__):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
set -a
. "$UNS_HOME/kiro.env"
set +a
cd "__WORKDIR__"
exec __UNS__ run --agent __AGENT__ -- __KIRO__ chat --no-interactive --agent __AGENT__ --trust-all-tools
