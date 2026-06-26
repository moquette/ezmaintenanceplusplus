#!/usr/bin/env bash
# Build the installable Kodi zip for EZ Maintenance++.
# Output: dist/script.ezmaintenanceplusplus-<version>.zip (top-level folder = the add-on id).
set -euo pipefail
cd "$(dirname "$0")"

ADDON=script.ezmaintenanceplusplus
VERSION=$(grep '<addon ' "$ADDON/addon.xml" | grep -oE 'version="[0-9.]+"' | head -1 | sed 's/version="//; s/"//')
mkdir -p dist
OUT="dist/${ADDON}-${VERSION}.zip"
rm -f "$OUT"

zip -r -X "$OUT" "$ADDON" \
  -x '*/__pycache__/*' \
  -x '*/.ruff_cache/*' \
  -x '*/.pytest_cache/*' \
  -x '*/.mypy_cache/*' \
  -x '*.pyc' -x '*.pyo' -x '*.DS_Store' >/dev/null

echo "built $OUT"
unzip -l "$OUT" | tail -3
