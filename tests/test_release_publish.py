"""Release recovery must not restart an active or successful upload."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "ensure_release_publish.py"
if not SCRIPT.is_file():
    pytest.skip("release tooling is not part of this distribution", allow_module_level=True)
spec = importlib.util.spec_from_file_location("ensure_release_publish", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SHA = "abc123"
TAG = "v1.0.0"
REPOSITORY = "owner/repo"


def publishing_run(status="completed", conclusion="failure", **changes):
    return {
        "id": 42,
        "head_sha": SHA,
        "head_branch": TAG,
        "event": "workflow_dispatch",
        "status": status,
        "conclusion": conclusion,
        **changes,
    }


class GitHub:
    def __init__(self, runs=(), *, ci_changes=None, annotated=False, release_changes=None):
        self.runs = list(runs)
        self.ci_changes = ci_changes or {}
        self.annotated = annotated
        self.release_changes = release_changes or {}
        self.commands = []
        self.dispatch_failures = 0

    def __call__(self, *arguments):
        self.commands.append(arguments)
        if arguments[0] != "api":
            if self.dispatch_failures:
                self.dispatch_failures -= 1
                raise RuntimeError("injected dispatch failure")
            return ""
        endpoint = arguments[-1]
        if "/releases/tags/" in endpoint:
            return {"draft": False, "prerelease": False, **self.release_changes}
        if "/git/ref/tags/" in endpoint:
            return {
                "object": {"type": "tag" if self.annotated else "commit", "sha": "annotated" if self.annotated else SHA}
            }
        if "/git/tags/" in endpoint:
            return {"object": {"type": "commit", "sha": SHA}}
        if "/ci.yml/" in endpoint:
            return {
                "workflow_runs": [
                    publishing_run(
                        **{"conclusion": "success", "head_branch": "main", "event": "push", **self.ci_changes}
                    )
                ]
            }
        if "/release.yml/" in endpoint:
            # Include an empty initial page to verify recovery searches all pages.
            return [{"workflow_runs": []}, {"workflow_runs": self.runs}]
        raise AssertionError(endpoint)

    @property
    def mutations(self):
        return [command for command in self.commands if command[0] != "api"]


def test_failed_dispatch_is_retried_even_when_release_and_tag_already_exist():
    gh = GitHub()
    gh.dispatch_failures = 1
    with pytest.raises(RuntimeError, match="dispatch failure"):
        module.ensure_publishing(REPOSITORY, TAG, gh)
    assert "started" in module.ensure_publishing(REPOSITORY, TAG, gh)
    assert gh.mutations == [("workflow", "run", "release.yml", "--ref", TAG, "--repo", REPOSITORY)] * 2


@pytest.mark.parametrize(
    "status,conclusion", [("completed", "success"), ("queued", None), ("in_progress", None), ("waiting", None)]
)
def test_successful_and_active_runs_are_not_duplicated(status, conclusion):
    gh = GitHub([publishing_run(status, conclusion)])
    module.ensure_publishing(REPOSITORY, TAG, gh)
    assert not gh.mutations


def test_successful_publication_wins_over_a_later_failed_duplicate():
    gh = GitHub([publishing_run(conclusion="failure", id=43), publishing_run(conclusion="success")])
    module.ensure_publishing(REPOSITORY, TAG, gh)
    assert not gh.mutations


def test_only_failed_jobs_in_the_latest_run_are_retried():
    gh = GitHub([publishing_run(id=41), publishing_run(conclusion="timed_out", id=43)])
    module.ensure_publishing(REPOSITORY, TAG, gh)
    assert gh.mutations == [("run", "rerun", "43", "--failed", "--repo", REPOSITORY)]


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped"])
def test_cancelled_or_skipped_publication_requires_manual_recovery(conclusion):
    gh = GitHub([publishing_run(conclusion=conclusion)])
    with pytest.raises(ValueError, match="restart it manually"):
        module.ensure_publishing(REPOSITORY, TAG, gh)
    assert not gh.mutations


@pytest.mark.parametrize(
    "ci_changes",
    [
        {"head_sha": "other"},
        {"head_branch": "other"},
        {"event": "pull_request"},
        {"status": "in_progress"},
        {"conclusion": "failure"},
    ],
)
def test_publishing_requires_successful_main_push_ci_for_the_tag_commit(ci_changes):
    gh = GitHub(ci_changes=ci_changes)
    with pytest.raises(ValueError, match="no successful push CI"):
        module.ensure_publishing(REPOSITORY, TAG, gh)
    assert not gh.mutations


def test_annotated_tag_is_resolved_and_unrelated_runs_are_ignored():
    gh = GitHub(
        [
            publishing_run(conclusion="success", head_sha="other"),
            publishing_run(conclusion="success", head_branch="other"),
        ],
        annotated=True,
    )
    module.ensure_publishing(REPOSITORY, TAG, gh)
    assert any(command[-1].endswith("/git/tags/annotated") for command in gh.commands)
    assert len(gh.mutations) == 1


def test_release_event_publication_is_recognized():
    gh = GitHub([publishing_run(conclusion="success", event="release", head_branch="main")])
    module.ensure_publishing(REPOSITORY, TAG, gh)
    assert not gh.mutations


@pytest.mark.parametrize("field", ["draft", "prerelease"])
def test_unpublished_or_prerelease_release_is_not_published_to_pypi(field):
    gh = GitHub(release_changes={field: True})
    with pytest.raises(ValueError, match="published stable"):
        module.ensure_publishing(REPOSITORY, TAG, gh)
    assert not gh.mutations
