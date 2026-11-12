#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export PATH="${HOME}/.local/bin:${PATH}"

# The uv that CI runs, by hash (tools/uv-requirements.txt), not whatever an installer script
# piped into a shell would fetch today.
if ! command -v uv >/dev/null 2>&1; then
    python3 -m pip install --user --break-system-packages --disable-pip-version-check \
        --require-hashes --only-binary :all: -r tools/uv-requirements.txt
fi

uv python install 3.13
# Hatch builds the test environments itself; its own interpreter is the ceiling's. Nothing
# pins what hatch depends on, so it is resolved as of a week ago: the cooldown Dependabot
# keeps for everything else.
uv tool install --python 3.13 --exclude-newer "7 days" hatch

hatch run lint
