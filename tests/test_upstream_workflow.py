"""The shape of `.github/workflows/upstream.yml`, which no CI run exercises before a Monday.

The newest-LLVM reference is a third party's native code, so the workflow scores the
fetched sources in the one step that holds a token and the `llvm-*` sources in a step that
holds none, and a step's exit status is read by a script that must not mistake a crash for
a clean week. The workflow is read as text (no YAML parser is a dependency of the tests), and
its combining script is run for real under bash.
"""

import importlib
import itertools
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
WORKFLOW = ROOT / ".github" / "workflows" / "upstream.yml"
if not (TOOLS / "llvm_oracle.py").exists() or not WORKFLOW.exists():  # pragma: no cover - wheel-only checkout
    pytest.skip("the weekly workflow is not part of this distribution", allow_module_level=True)
if str(TOOLS) not in sys.path:
    sys.path.append(str(TOOLS))

oracle = importlib.import_module("llvm_oracle")


def _steps():
    """The job's steps as `{name: text}`, in order, without the comments between them."""
    text = WORKFLOW.read_text(encoding="utf-8")
    body = text.split("\n    steps:\n", 1)[1]
    steps, current = {}, None
    for line in body.splitlines():
        if line.startswith("      - "):
            current = line.removeprefix("      - ").removeprefix("name: ")
            steps[current] = []
        if current is not None and not line.lstrip().startswith("#"):
            steps[current].append(line)
    return {name: "\n".join(lines) for name, lines in steps.items()}


def _run(step):
    """The step's `run:` as the shell receives it."""
    _, _, block = step.partition("        run: ")
    if not block.startswith("|"):
        return block.strip()
    return textwrap.dedent("\n".join(block.splitlines()[1:]))


def _command(step, tool="upstream_drift.py"):
    return next(line for line in _run(step).splitlines() if tool in line)


@pytest.fixture(scope="module")
def steps():
    return _steps()


FETCHED = "Score the fetched vectors"
INSTALL = "Install the LLVM reference"
LLVM = "Score the corpora against the newest LLVM"
COMBINE = "Combine the reports and the statuses"
ISSUE = "Open or update the issue"
FAIL = "Fail the run when a source could not be fetched or scored"


class TestShape:
    def test_the_steps_run_in_the_order_the_isolation_needs(self, steps):
        names = list(steps)
        assert names[-6:] == [FETCHED, INSTALL, LLVM, COMBINE, ISSUE, FAIL]

    def test_the_pin_is_the_one_the_workflow_installs_and_the_only_one(self, steps):
        assert oracle.PIN == "llvm-demangle-fxti==1.0.0"
        assert _run(steps[INSTALL]) == f'uv pip install --system "{oracle.PIN}"'
        assert WORKFLOW.read_text(encoding="utf-8").count(oracle.PACKAGE) == 1

    def test_only_the_scoring_and_the_issue_steps_are_given_the_token(self, steps):
        holding = [name for name, text in steps.items() if "secrets.GITHUB_TOKEN" in text]
        assert holding == [FETCHED, ISSUE]

    def test_the_fetched_sources_are_scored_by_a_step_that_never_asks_for_llvm(self, steps):
        command = _command(steps[FETCHED])
        assert "--no-llvm" in command
        assert "--require-llvm" not in command
        assert "--source" not in command, "the default is every fetched source; naming them would let one go missing"

    def test_the_llvm_sources_are_scored_by_a_step_with_no_environment_at_all(self, steps):
        step = steps[LLVM]
        assert "env:" not in step
        assert "secrets." not in step
        assert "github.token" not in step
        assert "id: llvm" in step

    def test_the_llvm_step_names_every_llvm_source_and_requires_the_package(self, steps):
        words = _command(steps[LLVM]).split()
        assert "--require-llvm" in words
        named = words[words.index("--source") + 1 :]
        named = list(itertools.takewhile(lambda word: not word.startswith("--"), named))
        assert sorted(named) == sorted(f"llvm-{scheme}" for scheme in oracle.ROUTES)

    def test_the_reports_are_kept_apart_until_they_are_combined(self, steps):
        assert "> fetched.md" in _command(steps[FETCHED])
        assert "> llvm.md" in _command(steps[LLVM])
        assert "drift.md" in steps[COMBINE]
        assert "drift.md" not in steps[FETCHED] + steps[LLVM]

    def test_the_two_reports_together_fit_in_an_issue(self, steps):
        limits = [int(_command(steps[name]).split("--limit ")[1].split()[0]) for name in (FETCHED, LLVM)]
        assert sum(limits) + 1000 <= 65536  # GitHub's limit on an issue body, and room for the run line

    def test_the_issue_and_the_failure_read_the_combined_status_and_nothing_else(self, steps):
        assert "steps.combined.outputs.status == '1'" in steps[ISSUE]
        assert "steps.combined.outputs.status == '2'" in steps[FAIL]
        for name in (ISSUE, FAIL, COMBINE):
            assert "steps.drift" not in steps[name]
        assert "id: combined" in steps[COMBINE]
        assert "steps.fetched.outputs.status" in steps[COMBINE]
        assert "steps.llvm.outputs.status" in steps[COMBINE]

    def test_a_scoring_step_that_exits_nonzero_does_not_stop_the_steps_that_read_it(self, steps):
        for name in (FETCHED, LLVM):
            assert _run(steps[name]).lstrip().startswith("set +e")
            assert 'echo "status=$?" >> "$GITHUB_OUTPUT"' in _run(steps[name])


needs_bash = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None, reason="needs bash")


@needs_bash
class TestCombine:
    """The combining step's script, run as the runner runs it (`bash -e`), over every pair of
    statuses a scoring step can leave behind."""

    FETCHED_MD = "| fetched | table |\n"
    LLVM_MD = "| llvm | table |\n"

    def combine(self, tmp_path, steps, fetched, llvm, fetched_md=FETCHED_MD, llvm_md=LLVM_MD):
        (tmp_path / "fetched.md").write_text(fetched_md, encoding="utf-8")
        (tmp_path / "llvm.md").write_text(llvm_md, encoding="utf-8")
        script = tmp_path / "combine.sh"
        script.write_text(_run(steps[COMBINE]), encoding="utf-8")
        output = tmp_path / "output"
        output.write_text("", encoding="utf-8")
        env = {
            "PATH": "/usr/bin:/bin",
            "FETCHED_STATUS": fetched,
            "LLVM_STATUS": llvm,
            "GITHUB_OUTPUT": str(output),
        }
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
    def test_any_other_status_from_either_step_is_a_loud_failure_never_a_clean_week(
        self, tmp_path, steps, crash, other
    ):
        for fetched, llvm in ((crash, other), (other, crash)):
            status, out = self.combine(tmp_path, steps, fetched, llvm)
            assert status == "2", (fetched, llvm)
            assert "::error::Scoring " in out
            assert f"exited with status '{crash}'" in out

    def test_the_error_says_which_step_crashed(self, tmp_path, steps):
        _, out = self.combine(tmp_path, steps, "0", "139")
        assert "::error::Scoring the llvm-* sources exited with status '139'" in out
        _, out = self.combine(tmp_path, steps, "139", "0")
        assert "::error::Scoring the fetched sources exited with status '139'" in out

    def test_both_crashing_is_reported_twice(self, tmp_path, steps):
        _, out = self.combine(tmp_path, steps, "139", "134")
        assert out.count("::error::") == 2

    def test_the_two_reports_become_one_with_a_blank_line_between(self, tmp_path, steps):
        self.combine(tmp_path, steps, "0", "0")
        assert (tmp_path / "drift.md").read_text(encoding="utf-8") == "| fetched | table |\n\n| llvm | table |\n"

    @pytest.mark.parametrize(("fetched_md", "llvm_md"), [("", "| llvm | table |\n"), ("| fetched | table |\n", "")])
    def test_a_step_that_printed_nothing_adds_no_blank_line(self, tmp_path, steps, fetched_md, llvm_md):
        self.combine(tmp_path, steps, "2", "0", fetched_md, llvm_md)
        assert (tmp_path / "drift.md").read_text(encoding="utf-8") == fetched_md + llvm_md

    def test_the_combined_report_is_what_the_issue_step_would_post(self, tmp_path, steps):
        self.combine(tmp_path, steps, "1", "1")
        body = (tmp_path / "drift.md").read_text(encoding="utf-8")
        assert body.index("fetched") < body.index("llvm")
