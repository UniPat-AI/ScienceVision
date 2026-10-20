#!/usr/bin/env bash
# Self-contained launcher: sets up Mindra auth (without the interactive
# mindra_on shell function), clears the SOCKS proxy, then runs the pilot
# orchestrator. Safe to nohup.
set +e
export MINDRACODE_API_KEY="${MINDRACODE_API_KEY:-$(bash -c 'source ~/.bashrc 2>/dev/null; printf %s "$MINDRACODE_API_KEY"')}"
export ANTHROPIC_API_KEY="$MINDRACODE_API_KEY"
export ANTHROPIC_BASE_URL="https://api.mindracode.com"
unset ALL_PROXY all_proxy HTTP_PROXY http_proxy HTTPS_PROXY https_proxy NO_PROXY no_proxy

cd /home/matianyi/Science-Vision/.claude/worktrees/verify-sam-tool-env
exec bash scripts/sam_service/run_pilot_all.sh "${1:-8}" "${2:-claude-sonnet-4-6}" "${3:-18}"
