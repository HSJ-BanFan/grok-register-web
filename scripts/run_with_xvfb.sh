#!/usr/bin/env bash
set -euo pipefail

if ! command -v xvfb-run >/dev/null 2>&1; then
  echo "xvfb-run was not found. Install it with: sudo apt-get install -y xvfb xauth" >&2
  exit 1
fi

if ! command -v xauth >/dev/null 2>&1; then
  echo "xauth was not found. Install it with: sudo apt-get install -y xauth" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python executable was not found: ${PYTHON_BIN}" >&2
  exit 1
fi

# Chrome runs in normal headful mode inside the virtual X display. Keeping a
# single worker reduces Cloudflare pressure and matches the verified baseline.
export GROK_REGISTER_BROWSER_HEADLESS=false
export GROK_REGISTER_CONCURRENCY=1

exec xvfb-run \
  --auto-servernum \
  --server-args="-screen 0 1365x900x24 -nolisten tcp" \
  "${PYTHON_BIN}" app.py "$@"
