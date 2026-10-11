#!/bin/bash
# Mac fallback tier for the Warden service (web + scheduler). Fly.io is the primary host; this runs when it is not.
# Lessons from Helmsman (2026-06): launchd strips PATH, so node (for baw) is added explicitly; DNS flakiness is a known failure, logged not hidden.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
cd "$HOME/Projects/warden" || exit 1
export ADMIN_TOKEN="$(cat data/admin-token)"
export RUN_SCHEDULER=1 WARDEN_EXECUTOR=agentic-wallet WARDEN_DATA="$HOME/Projects/warden/data"
# The service uses its own wallet CLI without the keychain module (a background process cannot answer a Keychain prompt and
# the global CLI hung on it, measured 2026-10-05). The session lives as an encrypted file in data/baw, as on the server.
export BAW_BIN="$HOME/Projects/warden/.tools/node_modules/.bin/baw"
export BINANCE_BAW_DIR="$HOME/Projects/warden/data/baw"
exec .venv/bin/uvicorn warden.web:app --host 127.0.0.1 --port 8787 --log-level info --no-access-log
