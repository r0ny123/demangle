"""Start or recover publishing for a release tag without duplicating a successful run."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import Any


def _gh(*arguments: str) -> Any:
    result = subprocess.run(["gh", *arguments], check=True, capture_output=True, text=True)
    return json.loads(result.stdout) if arguments[0] == "api" else result.stdout.strip()


def ensure_publishing(repository: str, tag: str, gh=_gh) -> str:
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("expected a stable release tag vX.Y.Z")
    base = f"repos/{repository}"
    release = gh("api", f"{base}/releases/tags/{tag}")
    if release["draft"] or release["prerelease"]:
        raise ValueError("publishing requires a published stable GitHub release")
    target = gh("api", f"{base}/git/ref/tags/{tag}")["object"]
    while target["type"] == "tag":
        target = gh("api", f"{base}/git/tags/{target['sha']}")["object"]
    if target["type"] != "commit":
        raise ValueError("release tag must resolve to a commit")
    sha = target["sha"]
    ci = gh(
        "api",
        f"{base}/actions/workflows/ci.yml/runs?head_sha={sha}&event=push&branch=main&status=completed&per_page=100",
    )["workflow_runs"]
    if not any(
        run["head_sha"] == sha
        and run["head_branch"] == "main"
        and run["event"] == "push"
        and run["status"] == "completed"
        and run["conclusion"] == "success"
        for run in ci
    ):
        raise ValueError(f"no successful push CI for release commit {sha}")

    pages = gh("api", "--paginate", "--slurp", f"{base}/actions/workflows/release.yml/runs?head_sha={sha}&per_page=100")
    runs = [
        run
        for page in pages
        for run in page["workflow_runs"]
        if run["head_sha"] == sha
        and (run["event"] == "release" or (run["event"] == "workflow_dispatch" and run["head_branch"] == tag))
    ]
    if any(run["conclusion"] == "success" for run in runs):
        return f"{tag}: publishing already succeeded"
    if any(run["status"] != "completed" for run in runs):
        return f"{tag}: publishing is already queued, running, or waiting for approval"
    if runs:
        latest = max(runs, key=lambda run: run["id"])
        if latest["conclusion"] not in ("failure", "timed_out"):
            raise ValueError(f"publishing run {latest['id']} ended as {latest['conclusion']}; restart it manually")
        gh("run", "rerun", str(latest["id"]), "--failed", "--repo", repository)
        return f"{tag}: retried failed jobs in publishing run {latest['id']}"
    gh("workflow", "run", "release.yml", "--ref", tag, "--repo", repository)
    return f"{tag}: publishing started"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository")
    parser.add_argument("tag")
    args = parser.parse_args()
    try:
        print(ensure_publishing(args.repository, args.tag))
    except (ValueError, subprocess.CalledProcessError) as error:
        message = (
            error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        )
        parser.exit(1, f"{message}\n")


if __name__ == "__main__":
    main()
