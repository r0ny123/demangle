"""The PyPI watch must flag an unattested upload or an extra owner, and pass a clean project."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "check_pypi_releases.py"
if not SCRIPT.is_file():
    pytest.skip("release tooling is not part of this distribution", allow_module_level=True)
spec = importlib.util.spec_from_file_location("check_pypi_releases", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

OWNER = {"role": "Owner", "user": "someone"}


def pypi(roles=(OWNER,), files=None, publisher=None):
    files = files or [{"filename": "demangle-1.0.0.tar.gz", "provenance": "https://pypi.test/p"}]
    responses = {
        "https://pypi.org/pypi/demangle/json": {"ownership": {"roles": list(roles)}},
        "https://pypi.org/simple/demangle/": {"files": files},
        "https://pypi.test/p": {"attestation_bundles": [{"publisher": publisher or dict(module.PUBLISHER)}]},
    }
    return responses.__getitem__


def test_clean_project_passes():
    assert module.problems(pypi()) == []


def test_file_without_provenance_is_flagged():
    found = module.problems(pypi(files=[{"filename": "demangle-1.0.1-py3-none-any.whl", "provenance": None}]))
    assert len(found) == 1
    assert "demangle-1.0.1-py3-none-any.whl" in found[0]


def test_other_publisher_is_flagged():
    found = module.problems(pypi(publisher={**module.PUBLISHER, "workflow": "evil.yml"}))
    assert len(found) == 1
    assert "evil.yml" in found[0]


@pytest.mark.parametrize(
    "roles",
    [(OWNER, {"role": "Owner", "user": "intruder"}), (OWNER, {"role": "Maintainer", "user": "intruder"}), ()],
)
def test_extra_or_missing_owner_is_flagged(roles):
    assert len(module.problems(pypi(roles=roles))) == 1
