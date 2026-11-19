"""Check that every file of demangle on PyPI came from this repository's release workflow.

Trusted Publishing keeps the release path in CI, but PyPI still accepts an upload from anyone
holding the account or an API token, and such an upload carries no attestation. This lists
every file PyPI serves and fails on one without provenance from `release.yml` in the `pypi`
environment, or on a second owner or maintainer, which is how a hijacked package usually
looks before anyone has noticed it.

Exit status: 0 clean, 1 something needs a person, 2 PyPI could not be read.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

PROJECT = "demangle"
PUBLISHER = {"kind": "GitHub", "repository": "r0ny123/demangle", "workflow": "release.yml", "environment": "pypi"}


def _fetch(url: str, attempts: int = 4, pause: float = 5.0) -> Any:
    """Read one URL as JSON, trying again when the network fails.

    A run makes some fifty requests several times an hour, so one slow handshake would
    otherwise fail a run every few days, and a watch that cries wolf gets ignored.
    """
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.pypi.simple.v1+json, application/json"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == attempts:
                raise
        except OSError:
            if attempt == attempts:
                raise
        time.sleep(pause * attempt)


def problems(fetch: Callable[[str], Any] = _fetch) -> list[str]:
    found = []
    roles = fetch(f"https://pypi.org/pypi/{PROJECT}/json").get("ownership", {}).get("roles", [])
    if len(roles) != 1 or roles[0]["role"] != "Owner":
        listed = ", ".join(f"{role['user']} ({role['role']})" for role in roles)
        found.append(f"The project should have exactly one owner and no maintainers, but lists: {listed or 'nobody'}")
    for file in fetch(f"https://pypi.org/simple/{PROJECT}/")["files"]:
        name = file["filename"]
        if not file.get("provenance"):
            found.append(f"`{name}` has no provenance, so it was not uploaded by the release workflow")
            continue
        for bundle in fetch(file["provenance"])["attestation_bundles"]:
            publisher = {key: bundle["publisher"].get(key) for key in PUBLISHER}
            if publisher != PUBLISHER:
                found.append(f"`{name}` was attested by an unexpected publisher: {publisher}")
    return found


def main() -> int:
    try:
        found = problems()
    except (OSError, urllib.error.URLError, KeyError, ValueError) as error:
        print(f"Could not read PyPI: {error}", file=sys.stderr)
        return 2
    if not found:
        print("Every file on PyPI has provenance from the release workflow, and there is one owner.")
        return 0
    print("## PyPI uploads that did not come from the release workflow\n")
    print("\n".join(f"- {line}" for line in found))
    print("\nIf you did not make these, treat the PyPI account as compromised: yank the files and rotate access.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
