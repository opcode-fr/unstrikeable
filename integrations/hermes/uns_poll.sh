#!/bin/sh
# Hermes cron wrapper (no-agent): prints the next event for one agent, or nothing.
# Copy to <profile>/scripts/uns_poll___AGENT__.sh, replace __AGENT__ and __UNS__ (absolute path of `uns`,
# e.g. ~/.local/bin/uns: cron does not load your shell PATH).
# Never run it by hand to test: it consumes the event. Use `uns poll --agent __AGENT__ --dry-run`.
exec __UNS__ poll --agent __AGENT__
