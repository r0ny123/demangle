#!/bin/sh
# Install uv, the named dependency groups and this project on a CI runner, with every file
# checked against a hash that is in the repository: uv against tools/uv-requirements.txt,
# everything else against uv.lock. `--locked` fails if uv.lock no longer matches
# pyproject.toml, so nothing is resolved on the runner, and a release that appeared on PyPI
# this morning reaches CI only through a reviewed change to the lockfile.
#
# The project itself is built by the hatchling in the `build` group, without isolation: an
# isolated build would fetch whatever hatchling is newest, unpinned.
#
#   tools/ci-install.sh test          # the `test` group, and the project
#   tools/ci-install.sh               # the project alone
set -eu

python -m pip install --disable-pip-version-check --require-hashes --only-binary :all: \
    -r tools/uv-requirements.txt

groups="--only-group build"
for group in "$@"; do
    groups="$groups --only-group $group"
done
locked="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/locked-requirements.txt"
# shellcheck disable=SC2086
uv export --locked $groups --output-file "$locked" > /dev/null
uv pip install --system --require-hashes -r "$locked"
uv pip install --system --offline --no-deps --no-build-isolation -e .
