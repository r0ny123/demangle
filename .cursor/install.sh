#!/usr/bin/env bash
# Bootstrap uv, a uv-managed Python 3.13 and Hatch. Ubuntu 24.04's own python3 is 3.12,
# below `requires-python`, so nothing here uses it.
set -euo pipefail

cd "$(dirname "$0")/.."

export PATH="${HOME}/.local/bin:${PATH}"

if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${PATH}"
fi

uv python install 3.13
# Hatch builds its environments on the interpreter it runs on, so it has to be 3.13.
uv tool install --python 3.13 hatch

# The first `hatch run` creates the default environment, so lint and test are ready to
# run once this passes.
hatch run lint
