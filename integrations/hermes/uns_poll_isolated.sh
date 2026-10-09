#!/bin/sh
# Hermes cron wrapper (no-agent) for an agent whose terminal runs in its own OS account (over SSH).
# The Hermes owner's account is the single reader of the profile's state.db: it pipes the Bot Chat counters
# (six numbers, no conversation) to the agent's poll. Unreadable database = unknown cost; the poll still runs.
# Copy to <profile>/scripts/uns_poll___AGENT__.sh and replace __UNS__ (absolute path of `uns` in the Hermes
# owner's account), __PROFILE__, __KEY__ (SSH key of the profile), __ACCOUNT__ and __AGENT__.
# Never run it by hand to test: it consumes the event. Dry-run instead:
#   __UNS__ usage --profile __PROFILE__ | ssh … '~/.local/bin/uns poll --agent __AGENT__ --dry-run --usage-from -'
U=$(__UNS__ usage --profile __PROFILE__ 2>/dev/null)
printf '%s' "$U" | ssh -i __KEY__ -o BatchMode=yes __ACCOUNT__@127.0.0.1 \
    '~/.local/bin/uns poll --agent __AGENT__ --usage-from -'
