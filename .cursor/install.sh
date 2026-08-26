#!/usr/bin/env bash
# Bootstrap Hatch and uv without touching Ubuntu's PEP 668-managed system Python.
set -euo pipefail

cd "$(dirname "$0")/.."

export PATH="${HOME}/.local/bin:${PATH}"

if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${PATH}"
fi

uv tool install hatch

# Warm the Hatch environment so lint, test, and check are ready to run.
hatch env create default 2>/dev/null || true
hatch run lint
