"""Release preparation operates on isolated metadata fixtures."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("prepare_release", ROOT / "tools" / "prepare_release.py")
if SPEC is None or SPEC.loader is None:
    pytest.skip("tools/prepare_release.py is not part of this distribution", allow_module_level=True)
prepare_release_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_release_module)
ReleaseError = prepare_release_module.ReleaseError
prepare_release = prepare_release_module.prepare_release


def fixture(tmp_path: Path, notes: str = "\n### Added\n\n- A feature.\n") -> Path:
    (tmp_path / "src/demangle").mkdir(parents=True)
    (tmp_path / "src/demangle/__init__.py").write_text('__version__ = "1.2.3"\n')
    (tmp_path / "CITATION.cff").write_text('version: "1.2.3"\ndate-released: 2026-01-02\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]" + notes + "\n## [1.2.3] - 2026-01-02\n\n- Prior change.\n\n"
        "[Unreleased]: https://github.com/r0ny123/demangle/compare/v1.2.3...HEAD\n"
        "[1.2.3]: https://github.com/r0ny123/demangle/compare/v1.2.2...v1.2.3\n"
    )
    return tmp_path


def contents(root: Path) -> dict[str, str]:
    return {p: (root / p).read_text() for p in ("src/demangle/__init__.py", "CITATION.cff", "CHANGELOG.md")}


@pytest.mark.parametrize(
    "version,date",
    [("1.2", "2026-02-03"), ("01.3.0", "2026-02-03"), ("1.3.0-rc1", "2026-02-03"), ("1.3.0", "2026-2-3")],
)
def test_malformed_version_or_date_leaves_files_untouched(tmp_path, version, date):
    root = fixture(tmp_path)
    before = contents(root)
    with pytest.raises(ReleaseError):
        prepare_release(root, version, date)
    assert contents(root) == before


def test_release_moves_notes_updates_links_and_is_not_repeatable(tmp_path):
    root = fixture(tmp_path)
    prepare_release(root, "1.3.0", "2026-02-03")
    changelog = (root / "CHANGELOG.md").read_text()
    assert "## [Unreleased]\n\n## [1.3.0] - 2026-02-03\n\n### Added\n\n- A feature." in changelog
    assert "[Unreleased]: https://github.com/r0ny123/demangle/compare/v1.3.0...HEAD" in changelog
    assert "[1.3.0]: https://github.com/r0ny123/demangle/compare/v1.2.3...v1.3.0" in changelog
    assert (root / "CITATION.cff").read_text() == 'version: "1.3.0"\ndate-released: 2026-02-03\n'
    after = contents(root)
    with pytest.raises(ReleaseError):
        prepare_release(root, "1.3.0", "2026-02-03")
    assert contents(root) == after


@pytest.mark.parametrize(
    ("path", "old", "replacement", "message"),
    [
        (
            "CHANGELOG.md",
            "[Unreleased]: https://github.com/r0ny123/demangle/compare/v1.2.3...HEAD\n",
            "[Unreleased]: old\n[Unreleased]: duplicate\n",
            "exactly one Unreleased comparison link",
        ),
        ("CHANGELOG.md", "## [Unreleased]", "## [Unreleased]\n\n## [Unreleased]", "exactly one"),
        ("CHANGELOG.md", "## [1.2.3] - 2026-01-02", "## [1.2.2] - 2026-01-02", "does not match package"),
        ("CITATION.cff", 'version: "1.2.3"', 'version: "1.2.2"', "do not match"),
    ],
)
def test_inconsistent_metadata_is_rejected_without_edits(tmp_path, path, old, replacement, message):
    root = fixture(tmp_path)
    metadata = root / path
    original = metadata.read_text()
    assert old in original
    metadata.write_text(original.replace(old, replacement, 1))
    before = contents(root)
    with pytest.raises(ReleaseError, match=message):
        prepare_release(root, "1.3.0", "2026-02-03")
    assert contents(root) == before


def test_empty_notes_and_validator_failure_leave_files_untouched(tmp_path):
    root = fixture(tmp_path, notes="\n")
    before = contents(root)
    with pytest.raises(ReleaseError, match="no notes"):
        prepare_release(root, "1.3.0", "2026-02-03")
    assert contents(root) == before

    root = fixture(tmp_path / "rollback")
    before = contents(root)
    with pytest.raises(ReleaseError, match="validation"):
        prepare_release(root, "1.3.0", "2026-02-03", validator=lambda _root: 1)
    assert contents(root) == before


def test_validator_exception_rolls_back_all_files(tmp_path):
    root = fixture(tmp_path)
    before = contents(root)

    def fail(_root):
        raise RuntimeError("validator crashed")

    with pytest.raises(RuntimeError, match="validator crashed"):
        prepare_release(root, "1.3.0", "2026-02-03", validator=fail)
    assert contents(root) == before
