"""Prepare version metadata and changelog for a stable release."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class ReleaseError(ValueError):
    """Release metadata cannot be prepared safely."""


def _version_tuple(version: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise ReleaseError(f"version must be stable X.Y.Z, got {version!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def prepare_release(root: Path, version: str, release_date: str, validator=None) -> None:
    """Validate all release metadata, then update it as one rollback-safe operation."""
    requested = _version_tuple(version)
    try:
        parsed_date = dt.date.fromisoformat(release_date)
    except ValueError as error:
        raise ReleaseError(f"invalid release date: {release_date!r}") from error
    if parsed_date.isoformat() != release_date:
        raise ReleaseError(f"date must use YYYY-MM-DD, got {release_date!r}")

    paths = (root / "src/demangle/__init__.py", root / "CITATION.cff", root / "CHANGELOG.md")
    try:
        originals = {path: path.read_text(encoding="utf-8") for path in paths}
    except OSError as error:
        raise ReleaseError(f"cannot read release metadata: {error}") from error
    source, citation, changelog = (originals[path] for path in paths)

    source_matches = list(re.finditer(r'^__version__ = "([^"]+)"$', source, re.M))
    citation_matches = list(re.finditer(r'^version: "?([^"\s]+)"?\s*$', citation, re.M))
    if len(source_matches) != 1 or len(citation_matches) != 1:
        raise ReleaseError("expected exactly one version in package metadata and CITATION.cff")
    current = source_matches[0].group(1)
    if citation_matches[0].group(1) != current:
        raise ReleaseError("package and citation versions do not match")
    current_tuple = _version_tuple(current)
    if requested <= current_tuple:
        raise ReleaseError(f"new version {version} must be greater than current version {current}")

    headings = list(re.finditer(r"^## \[(Unreleased|\d+\.\d+\.\d+)\](?: - (\d{4}-\d{2}-\d{2}))?$", changelog, re.M))
    unreleased_headings = [match for match in headings if match.group(1) == "Unreleased"]
    if len(unreleased_headings) != 1 or headings[0] is not unreleased_headings[0]:
        raise ReleaseError("CHANGELOG.md must begin its release sections with exactly one ## [Unreleased]")
    comparison_links = list(re.finditer(r"^\[Unreleased\]:.*$", changelog, re.M))
    if len(comparison_links) != 1:
        raise ReleaseError("CHANGELOG.md must have exactly one Unreleased comparison link")
    if any(match.group(1) == version for match in headings) or re.search(
        rf"^\[{re.escape(version)}\]:", changelog, re.M
    ):
        raise ReleaseError(f"CHANGELOG.md already contains release {version}")
    unreleased_start = headings[0].end()
    next_heading = next((match for match in headings[1:]), None)
    unreleased_notes = changelog[unreleased_start : next_heading.start() if next_heading else len(changelog)]
    if not unreleased_notes.strip():
        raise ReleaseError("Unreleased section has no notes to release")

    release_sections = [match for match in headings[1:] if match.group(1) != "Unreleased"]
    if not release_sections:
        raise ReleaseError("CHANGELOG.md has no previous release")
    previous = release_sections[0].group(1)
    if previous != current:
        raise ReleaseError(f"latest changelog release {previous} does not match package version {current}")
    if requested <= _version_tuple(previous):
        raise ReleaseError(f"new version {version} must be greater than latest changelog release {previous}")
    if any(_version_tuple(match.group(1)) > _version_tuple(previous) for match in release_sections[1:]):
        raise ReleaseError("CHANGELOG.md releases are not in descending version order")

    new_source = source[: source_matches[0].start(1)] + version + source[source_matches[0].end(1) :]
    new_citation = citation[: citation_matches[0].start(1)] + version + citation[citation_matches[0].end(1) :]
    date_matches = list(re.finditer(r"^date-released:.*$", new_citation, re.M))
    if len(date_matches) > 1:
        raise ReleaseError("CITATION.cff has multiple date-released fields")
    if date_matches:
        match = date_matches[0]
        new_citation = new_citation[: match.start()] + f"date-released: {release_date}" + new_citation[match.end() :]

    # Put notes beneath the new version while keeping Unreleased as an empty heading.
    notes = unreleased_notes.strip()
    new_changelog = (
        changelog[:unreleased_start]
        + "\n\n## ["
        + version
        + f"] - {release_date}\n\n"
        + notes
        + "\n\n"
        + changelog[unreleased_start + len(unreleased_notes) :]
    )
    new_changelog = re.sub(
        r"^\[Unreleased\]:.*$",
        f"[Unreleased]: https://github.com/r0ny123/demangle/compare/v{version}...HEAD",
        new_changelog,
        count=1,
        flags=re.M,
    )
    definition = f"[{version}]: https://github.com/r0ny123/demangle/compare/v{previous}...v{version}"
    link = f"[Unreleased]: https://github.com/r0ny123/demangle/compare/v{version}...HEAD"
    new_changelog = new_changelog.replace(link, f"{link}\n{definition}", 1)
    updates = dict(zip(paths, (new_source, new_citation, new_changelog), strict=True))

    try:
        for path, content in updates.items():
            path.write_text(content, encoding="utf-8")
        if validator is not None:
            result = validator(root)
            if result not in (None, 0):
                raise ReleaseError("metadata validation failed")
    except BaseException:
        for path, content in originals.items():
            path.write_text(content, encoding="utf-8")
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="new stable release version (X.Y.Z)")
    parser.add_argument("--date", help="release date (YYYY-MM-DD); defaults to today")
    args = parser.parse_args(argv)
    release_date = args.date or dt.date.today().isoformat()

    def validate(root: Path) -> int:
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/test_docs.py"], cwd=root, check=False
        ).returncode

    try:
        prepare_release(ROOT, args.version, release_date, validator=validate)
    except (ReleaseError, OSError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    print(f"Prepared release {args.version} ({release_date})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
