"""The shape of `.github/workflows/upstream.yml`, which no CI run exercises before a Monday.

The newest-LLVM reference is a third party's native code, and a step with no `env:` is not
walled off from the steps after it: every step of a job shares GITHUB_ENV, GITHUB_OUTPUT and
GITHUB_PATH, so code that ran in one could set PATH or LD_PRELOAD for the one that holds the
token. The workflow therefore scores the `llvm-*` sources in a job of their own, with
`contents: read` and no secret, and the job that holds the token takes what that one
uploaded as data: a status that counts only as exactly 0, 1 or 2, and a report that is only
ever concatenated. A job that crashes leaves no artifact, which must read as a failed week and
not a clean one. The workflow is read as text (no YAML parser is a dependency of the tests),
and its combining script is run for real under bash.
"""

import importlib
import itertools
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
WORKFLOW = ROOT / ".github" / "workflows" / "upstream.yml"
REQUIREMENTS = TOOLS / "llvm-oracle-requirements.txt"
if not (TOOLS / "llvm_oracle.py").exists() or not WORKFLOW.exists():  # pragma: no cover - wheel-only checkout
    pytest.skip("the weekly workflow is not part of this distribution", allow_module_level=True)
if str(TOOLS) not in sys.path:
    sys.path.append(str(TOOLS))

oracle = importlib.import_module("llvm_oracle")


def _jobs():
    """The workflow's jobs as `{name: text}`, in order, without the comments between the lines."""
    text = WORKFLOW.read_text(encoding="utf-8")
    body = text.split("\njobs:\n", 1)[1]
    jobs, current = {}, None
    for line in body.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if found := re.fullmatch(r"  ([\w-]+):", line):
            current = found[1]
            jobs[current] = []
        if current is not None:
            jobs[current].append(line)
    return {name: "\n".join(lines) for name, lines in jobs.items()}


def _steps(job):
    """The job's steps as `{name: text}`, in order."""
    body = job.split("\n    steps:\n", 1)[1]
    steps, current = {}, None
    for line in body.splitlines():
        if line.startswith("      - "):
            current = line.removeprefix("      - ").removeprefix("name: ")
            steps[current] = []
        if current is not None:
            steps[current].append(line)
    return {name: "\n".join(lines) for name, lines in steps.items()}


def _block(text, key, indent):
    """The `key: value` lines indented under `key:` (at `indent` spaces), as a dict."""
    lines = text.splitlines()
    found = {}
    for line in lines[lines.index(" " * indent + key + ":") + 1 :]:
        if not line.startswith(" " * (indent + 2)):
            break
        name, _, value = line.strip().partition(": ")
        found[name] = value
    return found


def _run(step):
    """The step's `run:` as the shell receives it."""
    _, _, block = step.partition("        run: ")
    if not block.startswith("|"):
        return block.strip()
    return textwrap.dedent("\n".join(block.splitlines()[1:]))


def _command(step, tool="upstream_drift.py"):
    return next(line for line in _run(step).splitlines() if tool in line)


@pytest.fixture(scope="module")
def jobs():
    return _jobs()


@pytest.fixture(scope="module")
def llvm_steps(jobs):
    return _steps(jobs[LLVM_JOB])


@pytest.fixture(scope="module")
def steps(jobs):
    """The steps of the job that holds the token."""
    return _steps(jobs[DRIFT_JOB])


LLVM_JOB, DRIFT_JOB = "llvm", "drift"
INSTALL = "Install the LLVM reference"
LLVM = "Score the corpora against the newest LLVM"
UPLOAD = "Hand over the report and the status"
FETCHED = "Score the fetched vectors"
DOWNLOAD = "Download the LLVM report"
COMBINE = "Combine the reports and the statuses"
ISSUE = "Open or update the issue"
FAIL = "Fail the run when a source could not be fetched or scored"

#: What a step must not name if it is not to see a token: a secret, the token as a context, or
#: as the variable `gh` and the scoring script read it from, or as an input (`token:`,
#: `github-token:`).
TOKENS = ("secrets.", "github.token", "GITHUB_TOKEN", "GH_TOKEN", "token:")


class TestJobs:
    def test_the_workflow_is_the_llvm_job_and_the_job_that_holds_the_token(self, jobs):
        assert list(jobs) == [LLVM_JOB, DRIFT_JOB]

    def test_the_job_with_the_token_waits_for_the_llvm_job_and_runs_whatever_became_of_it(self, jobs):
        header = jobs[DRIFT_JOB].split("\n    steps:\n", 1)[0].splitlines()
        assert "    needs: llvm" in header
        # A job that failed, was killed or timed out would otherwise skip this one, and with it
        # the issue the fetched vectors may call for.
        assert "    if: ${{ !cancelled() }}" in header

    def test_the_llvm_job_is_given_contents_read_and_nothing_more(self, jobs):
        assert _block(jobs[LLVM_JOB], "permissions", 4) == {"contents": "read"}

    def test_only_the_job_that_files_the_issue_is_given_to_write_one(self, jobs):
        assert _block(jobs[DRIFT_JOB], "permissions", 4) == {"contents": "read", "issues": "write"}

    def test_the_llvm_job_references_no_secret_and_reads_no_token(self, jobs):
        text = jobs[LLVM_JOB]
        assert [marker for marker in TOKENS if marker in text] == []
        assert "env:" not in text, "nothing is passed in to the process that imports the binding"

    def test_only_the_scoring_and_the_issue_steps_of_the_other_job_are_given_the_token(self, jobs):
        holding = [
            (job, name)
            for job, text in jobs.items()
            for name, step in _steps(text).items()
            if any(marker in step for marker in TOKENS)
        ]
        assert holding == [(DRIFT_JOB, FETCHED), (DRIFT_JOB, ISSUE)]
        assert "secrets.GITHUB_TOKEN" in _steps(jobs[DRIFT_JOB])[FETCHED]

    def test_both_jobs_check_out_without_leaving_a_credential_behind(self, jobs):
        for text in jobs.values():
            [checkout] = [step for name, step in _steps(text).items() if "actions/checkout@" in name]
            assert _block(checkout, "with", 8) == {"persist-credentials": "false"}

    def test_every_action_is_pinned_to_a_commit_and_says_which_release(self, jobs):
        text = "\n".join(jobs.values())
        pinned = re.findall(r"uses: [\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$", text, re.MULTILINE)
        assert pinned and len(pinned) == text.count("uses: ")


class TestLlvmJob:
    def test_the_steps_run_in_the_order_the_isolation_needs(self, llvm_steps):
        assert list(llvm_steps)[-3:] == [INSTALL, LLVM, UPLOAD]

    def test_the_package_is_installed_from_a_file_that_pins_it_by_hash(self, llvm_steps):
        assert _run(llvm_steps[INSTALL]).strip() == (
            "uv pip install --system --require-hashes --only-binary :all: -r tools/llvm-oracle-requirements.txt"
        )
        assert REQUIREMENTS.exists()

    def test_that_file_holds_the_pin_the_oracle_names_and_the_hash_of_its_wheel(self):
        lines = [line for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines() if line.strip()]
        words = " ".join(line.removesuffix("\\").strip() for line in lines if not line.startswith("#")).split()
        assert oracle.PIN == "llvm-demangle-fxti==1.0.0"
        assert words[0] == oracle.PIN
        assert words[1:], "a requirement with no hash is refused under --require-hashes"
        assert all(re.fullmatch(r"--hash=sha256:[0-9a-f]{64}", word) for word in words[1:]), words

    def test_the_package_is_named_nowhere_else_in_the_workflow_and_not_in_pyproject(self, jobs):
        assert oracle.PACKAGE not in "\n".join(jobs.values())
        assert oracle.PACKAGE not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    def test_the_llvm_step_names_every_llvm_source_and_requires_the_package(self, llvm_steps):
        words = _command(llvm_steps[LLVM]).split()
        assert "--require-llvm" in words
        assert "--no-llvm" not in words
        named = words[words.index("--source") + 1 :]
        named = list(itertools.takewhile(lambda word: not word.startswith("--"), named))
        assert sorted(named) == sorted(f"llvm-{scheme}" for scheme in oracle.ROUTES)

    def test_the_report_and_the_exit_status_are_written_where_the_artifact_is_taken_from(self, llvm_steps):
        script = _run(llvm_steps[LLVM])
        assert "> llvm-report/llvm.md" in _command(llvm_steps[LLVM])
        assert 'echo "$?" > llvm-report/status' in script
        assert _block(llvm_steps[UPLOAD], "with", 8) == {
            "name": "llvm-report",
            "path": "llvm-report/",
            "if-no-files-found": "error",
            "overwrite": "true",
        }

    def test_a_scoring_step_that_exits_nonzero_does_not_stop_the_upload_that_follows(self, llvm_steps):
        script = _run(llvm_steps[LLVM])
        assert script.lstrip().startswith("set +e")
        lines = script.splitlines()
        # The status is the very next thing written after the command whose status it is.
        assert lines[lines.index(_command(llvm_steps[LLVM])) + 1] == 'echo "$?" > llvm-report/status'


class TestDriftJob:
    def test_the_steps_run_in_the_order_the_data_needs(self, steps):
        assert list(steps)[-5:] == [FETCHED, DOWNLOAD, COMBINE, ISSUE, FAIL]

    def test_this_job_never_installs_or_asks_for_the_llvm_package(self, jobs):
        text = jobs[DRIFT_JOB]
        assert "llvm-oracle-requirements" not in text
        assert "--require-hashes" not in text
        assert "--require-llvm" not in text

    def test_the_fetched_sources_are_scored_by_a_step_that_never_asks_for_llvm(self, steps):
        command = _command(steps[FETCHED])
        assert "--no-llvm" in command
        assert "--source" not in command, "the default is every fetched source; naming them would let one go missing"

    def test_the_download_is_the_artifact_the_other_job_uploads_and_a_missing_one_is_not_fatal_there(
        self, steps, llvm_steps
    ):
        download, upload = _block(steps[DOWNLOAD], "with", 8), _block(llvm_steps[UPLOAD], "with", 8)
        assert download == {"name": upload["name"], "path": upload["path"].rstrip("/")}
        assert "        uses: actions/download-artifact@" in steps[DOWNLOAD]
        # The combining step is the one that says why the week failed.
        assert "        continue-on-error: true" in steps[DOWNLOAD].splitlines()

    def test_the_reports_are_kept_apart_until_they_are_combined(self, steps, llvm_steps):
        assert "> fetched.md" in _command(steps[FETCHED])
        assert "> llvm-report/llvm.md" in _command(llvm_steps[LLVM])
        assert "drift.md" in steps[COMBINE]
        assert "drift.md" not in steps[FETCHED] + steps[DOWNLOAD] + "".join(llvm_steps.values())

    def test_the_two_reports_together_fit_in_an_issue(self, steps, llvm_steps):
        limits = [int(_command(step).split("--limit ")[1].split()[0]) for step in (steps[FETCHED], llvm_steps[LLVM])]
        assert sum(limits) + 1000 <= 65536  # GitHub's limit on an issue body, and room for the run line

    def test_the_issue_and_the_failure_read_the_combined_status_and_nothing_else(self, steps):
        assert "steps.combined.outputs.status == '1'" in steps[ISSUE]
        assert "steps.combined.outputs.status == '2'" in steps[FAIL]
        for name in (ISSUE, FAIL, COMBINE):
            assert "steps.drift" not in steps[name]
            assert "steps.llvm" not in steps[name], "the other job's status is read from its artifact"
        assert "id: combined" in steps[COMBINE]
        assert "steps.fetched.outputs.status" in steps[COMBINE]

    def test_what_came_from_the_artifact_is_never_run_or_sourced(self, steps):
        for name in (COMBINE, ISSUE):
            assert not re.search(r"(^|[\s;&|(])(source|eval|exec)\s|^\s*\.\s", _run(steps[name]), re.MULTILINE), name
        assert "cat drift.md" in _run(steps[ISSUE]), "the report reaches the issue as text"

    def test_a_scoring_step_that_exits_nonzero_does_not_stop_the_steps_that_read_it(self, steps):
        assert _run(steps[FETCHED]).lstrip().startswith("set +e")
        assert 'echo "status=$?" >> "$GITHUB_OUTPUT"' in _run(steps[FETCHED])


@pytest.fixture(scope="module")
def parsed():
    return pytest.importorskip("yaml").safe_load(WORKFLOW.read_text(encoding="utf-8"))


class TestAsYaml:
    """The same workflow through a YAML parser, where one is installed (the tests need none): the
    text the other tests take apart is also a document, and says what they take it to say. A
    plain scalar that holds `: ` is text to them and an error to GitHub."""

    def test_the_jobs_and_what_they_are_given_are_what_the_text_says(self, parsed):
        llvm, drift = parsed["jobs"][LLVM_JOB], parsed["jobs"][DRIFT_JOB]
        assert list(parsed["jobs"]) == [LLVM_JOB, DRIFT_JOB]
        assert parsed["permissions"] == {"contents": "read"}
        assert llvm["permissions"] == {"contents": "read"}
        assert drift["permissions"] == {"contents": "read", "issues": "write"}
        assert (drift["needs"], drift["if"]) == ("llvm", "${{ !cancelled() }}")
        assert "needs" not in llvm and "if" not in llvm
        assert "env" not in parsed and "env" not in llvm, "no environment, at the workflow or the job"
        assert not [step for step in llvm["steps"] if "env" in step or "if" in step or "continue-on-error" in step]

    def test_every_named_script_and_input_reads_the_same_either_way(self, parsed, jobs):
        compared = 0
        for job, text in jobs.items():
            for step in parsed["jobs"][job]["steps"]:
                if "name" not in step:
                    continue
                read = _steps(text)[step["name"]]
                if "run" in step:
                    script = "\n".join(line for line in step["run"].splitlines() if not line.lstrip().startswith("#"))
                    assert _run(read).strip() == script.strip(), step["name"]
                    compared += 1
                if "with" in step:
                    as_text = {
                        key: str(value).lower() if isinstance(value, bool) else str(value)
                        for key, value in step["with"].items()
                    }
                    assert _block(read, "with", 8) == as_text, step["name"]
                    compared += 1
        assert compared == 8, "three in the llvm job, five in the other: a step that went unread would be missed"


needs_bash = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None, reason="needs bash")


@needs_bash
class TestCombine:
    """The combining step's script, run as the runner runs it (`bash -e`), over every pair of
    statuses the two jobs can leave behind, and over an artifact that is missing or hostile."""

    FETCHED_MD = "| fetched | table |\n"
    LLVM_MD = "| llvm | table |\n"

    def combine(self, tmp_path, steps, fetched, llvm, fetched_md=FETCHED_MD, llvm_md=LLVM_MD):
        """Run the script in `tmp_path`, laid out as the download leaves it, and return (status, stdout).

        `fetched` is the fetching step's status. `llvm` is what the status file holds: a status as
        `echo` writes it (a str, newline and all), the bytes of a file that is not one, or None for
        no file; `llvm_md` is the report, or None for no file. Neither is no artifact at all.
        """
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "fetched.md").write_text(fetched_md, encoding="utf-8")
        if llvm is not None or llvm_md is not None:
            (tmp_path / "llvm-report").mkdir()
        if llvm is not None:
            raw = f"{llvm}\n".encode() if isinstance(llvm, str) else llvm
            (tmp_path / "llvm-report" / "status").write_bytes(raw)
        if llvm_md is not None:
            (tmp_path / "llvm-report" / "llvm.md").write_text(llvm_md, encoding="utf-8")
        script = tmp_path / "combine.sh"
        script.write_text(_run(steps[COMBINE]), encoding="utf-8")
        output = tmp_path / "output"
        output.write_text("", encoding="utf-8")
        env = {"PATH": "/usr/bin:/bin", "FETCHED_STATUS": fetched, "GITHUB_OUTPUT": str(output)}
        done = subprocess.run(
            ["bash", "-e", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, check=False
        )
        assert done.returncode == 0, done.stderr
        lines = output.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1 and lines[0].startswith("status="), lines
        return lines[0].removeprefix("status="), done.stdout

    @pytest.mark.parametrize(
        ("fetched", "llvm", "expected"),
        [
            ("0", "0", "0"),
            ("1", "0", "1"),
            ("0", "1", "1"),
            ("1", "1", "1"),
            ("2", "0", "2"),
            ("0", "2", "2"),
            ("1", "2", "2"),
            ("2", "1", "2"),
            ("2", "2", "2"),
        ],
    )
    def test_the_statuses_the_scripts_define_combine_as_the_issue_logic_reads_them(
        self, tmp_path, steps, fetched, llvm, expected
    ):
        status, out = self.combine(tmp_path, steps, fetched, llvm)
        assert status == expected
        assert "::error::" not in out

    @pytest.mark.parametrize("crash", ["139", "134", "137", "3", "255", "", "-1", "ok"])
    @pytest.mark.parametrize("other", ["0", "1", "2"])
    def test_any_other_status_from_either_job_is_a_loud_failure_never_a_clean_week(self, tmp_path, steps, crash, other):
        for fetched, llvm in ((crash, other), (other, crash)):
            status, out = self.combine(tmp_path / f"{fetched}-{llvm}", steps, fetched, llvm)
            assert status == "2", (fetched, llvm)
            assert "::error::Scoring " in out
            assert f"exited with status '{crash}'" in out

    def test_the_error_says_which_one_crashed(self, tmp_path, steps):
        _, out = self.combine(tmp_path / "a", steps, "0", "139")
        assert "::error::Scoring the llvm-* sources exited with status '139'" in out
        _, out = self.combine(tmp_path / "b", steps, "139", "0")
        assert "::error::Scoring the fetched sources exited with status '139'" in out

    def test_both_crashing_is_reported_twice(self, tmp_path, steps):
        _, out = self.combine(tmp_path, steps, "139", "134")
        assert out.count("::error::") == 2

    @pytest.mark.parametrize("fetched", ["0", "1", "2"])
    def test_no_artifact_at_all_is_a_failed_week_and_not_a_clean_one(self, tmp_path, steps, fetched):
        """The llvm job crashed, was killed or timed out before it uploaded anything."""
        status, out = self.combine(tmp_path, steps, fetched, None, llvm_md=None)
        assert status == "2"
        assert "::error::Scoring the llvm-* sources exited with status '', which is none of 0, 1 or 2" in out
        assert (tmp_path / "drift.md").read_text(encoding="utf-8") == self.FETCHED_MD

    def test_a_report_with_no_status_is_a_failed_week(self, tmp_path, steps):
        status, out = self.combine(tmp_path, steps, "0", None)
        assert status == "2"
        assert "exited with status ''" in out
        # What it did print is still there for the person who reads the run.
        assert (tmp_path / "drift.md").read_text(encoding="utf-8") == "| fetched | table |\n\n| llvm | table |\n"

    def test_a_status_with_no_report_is_scored_on_the_status_alone(self, tmp_path, steps):
        assert self.combine(tmp_path / "a", steps, "0", "1", llvm_md=None)[0] == "1"
        assert self.combine(tmp_path / "b", steps, "0", "0", llvm_md=None)[0] == "0"
        assert (tmp_path / "a" / "drift.md").read_text(encoding="utf-8") == self.FETCHED_MD

    @pytest.mark.parametrize(
        "garbled",
        [
            b"",
            b"\n",
            b"3\n",
            b"00\n",
            b"0 \n",
            b" 0\n",
            b"0\r\n",
            b"01\n",
            b"0\n1\n",
            b"1\n0\n",
            b"0\x00\n",
            b"\xff\xfe\n",
            b"1;0\n",
            b"$(echo 0)\n",
            b"0" * 16,
            b"2" * 100_000,
        ],
        ids=lambda raw: repr(raw)[:24],
    )
    def test_a_status_file_that_is_not_exactly_one_of_the_three_is_a_failed_week(self, tmp_path, steps, garbled):
        status, out = self.combine(tmp_path, steps, "0", garbled)
        assert status == "2"
        assert "::error::Scoring the llvm-* sources exited with status '" in out

    def test_a_status_the_other_job_wrote_cannot_start_a_workflow_command_of_its_own(self, tmp_path, steps):
        forged = b"0\n::warning::x\n::error::forged\n::stop-commands::x\n"
        status, out = self.combine(tmp_path, steps, "0", forged)
        assert status == "2"
        commands = [line for line in out.splitlines() if line.startswith("::")]
        assert len(commands) == 1
        assert commands[0].startswith("::error::Scoring the llvm-* sources exited with status '0")
        assert "::warning::" not in out
        assert "::stop-commands::" not in out

    def test_the_artifact_is_data_and_nothing_in_it_runs(self, tmp_path, steps):
        marks = [tmp_path / f"ran{n}" for n in range(1, 4)]
        hostile = (
            f"$(touch {marks[0]})\n`touch {marks[1]}`\n; touch {marks[2]}\n"
            '::error::forged\necho "status=0" >> "$GITHUB_OUTPUT"\n'
        )
        status, out = self.combine(tmp_path, steps, "0", "1", llvm_md=hostile)
        assert status == "1"
        assert [mark for mark in marks if mark.exists()] == []
        assert (tmp_path / "drift.md").read_text(encoding="utf-8") == self.FETCHED_MD + "\n" + hostile
        assert "forged" not in out

    def test_the_two_reports_become_one_with_a_blank_line_between(self, tmp_path, steps):
        self.combine(tmp_path, steps, "0", "0")
        assert (tmp_path / "drift.md").read_text(encoding="utf-8") == "| fetched | table |\n\n| llvm | table |\n"

    @pytest.mark.parametrize(("fetched_md", "llvm_md"), [("", "| llvm | table |\n"), ("| fetched | table |\n", "")])
    def test_a_job_that_printed_nothing_adds_no_blank_line(self, tmp_path, steps, fetched_md, llvm_md):
        self.combine(tmp_path, steps, "2", "0", fetched_md, llvm_md)
        assert (tmp_path / "drift.md").read_text(encoding="utf-8") == fetched_md + llvm_md

    def test_the_combined_report_is_what_the_issue_step_would_post(self, tmp_path, steps):
        self.combine(tmp_path, steps, "1", "1")
        body = (tmp_path / "drift.md").read_text(encoding="utf-8")
        assert body.index("fetched") < body.index("llvm")
