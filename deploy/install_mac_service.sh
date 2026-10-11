#!/bin/bash
# Install the Mac fallback service (web + scheduler on 127.0.0.1:8787). Primary host is Railway (railway.json).
set -e
cd "$(dirname "$0")/.."
umask 077
mkdir -p data
[ -f data/admin-token ] || openssl rand -hex 24 > data/admin-token
mkdir -p data/baw
# service-local wallet CLI without the keychain module (file session in data/baw)
[ -x .tools/node_modules/.bin/baw ] || npm install --prefix .tools @binance/agentic-wallet@1.10.0
rm -rf .tools/node_modules/@github/keytar .tools/node_modules/@binance/agentic-wallet/node_modules/@github/keytar
sed "s#__HOME__#$HOME#g" deploy/com.warden.service.plist.template > ~/Library/LaunchAgents/com.warden.service.plist
launchctl bootout gui/$(id -u)/com.warden.service 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.warden.service.plist
echo "installed; health: curl -s http://127.0.0.1:8787/health"
