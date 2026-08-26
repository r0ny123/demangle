#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python3 -m pip install --user hatch uv
export PATH="${HOME}/.local/bin:${PATH}"

# Warm the Hatch environment so lint, test, and check are ready to run.
hatch env create default 2>/dev/null || true
hatch run lint
