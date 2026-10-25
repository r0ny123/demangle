"""The newest-LLVM reference in tools/llvm_oracle.py, with LLVM replaced by a stand-in.

Nothing here needs `llvm-demangle-fxti`: the package is optional and the library has no
runtime dependencies, so what is pinned is the routing, the classification and the skip.
"""

import gzip
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if not (TOOLS / "llvm_oracle.py").exists():  # pragma: no cover - wheel-only checkout
    pytest.skip("tools/llvm_oracle.py is not part of this distribution", allow_module_level=True)
if str(TOOLS) not in sys.path:
    sys.path.append(str(TOOLS))

# By name, so that this is the module `tools/upstream_drift.py` imports: one object to patch.
oracle = importlib.import_module("llvm_oracle")


def _package(versions=(12, 13, 14, 21)):
    """What `llvmdemangle` exposes, as far as the oracle uses it."""

    def backend(version):
        return SimpleNamespace(
            itanium_demangle=lambda name: f"it<{name}>" if name.startswith("_Z") else None,
            microsoft_demangle=lambda name: (f"ms:{name}", 1) if name.startswith("?") else None,
            rust_demangle=lambda name: f"rs:{name}",
            dlang_demangle=lambda name: f"d:{name}",
        )

    return SimpleNamespace(SUPPORTED_VERSIONS=list(versions), backend=backend)


class TestNames:
    def test_a_name_goes_to_the_reader_its_prefix_picks(self, tmp_path):
        (tmp_path / "reported").mkdir()
        (tmp_path / "a.txt").write_text(
            "# a comment\n_Z1fv\tf()\n__Z1fv\tf()\n___Z1fv\tf()\n____Z1fv\tf()\n_____Z1fv\tx\n"
            "_RNvC1a1b\ta::b\n_D4test3fooi\ttest.foo\n?x@@3HA\tint x\n"
            "$s1a1bV\ta.b\n_T1a\ta\n__imp__Z1fv\timport thunk\n.?AVfoo@@\tfoo\n",
            encoding="utf-8",
        )
        (tmp_path / "b.txt.gz").write_bytes(gzip.compress(b"_Z1gv\tg()\n"))
        (tmp_path / "reported" / "c.txt").write_text("?y@@3HA\tint y\n_Z1fv\tf()\n", encoding="utf-8")
        assert oracle.corpus_names(tmp_path) == {
            "itanium": ["_Z1fv", "_Z1gv", "__Z1fv", "___Z1fv", "____Z1fv"],
            "msvc": ["?x@@3HA", "?y@@3HA"],
            "rust": ["_RNvC1a1b"],
            "d": ["_D4test3fooi"],
        }

    def test_the_checked_in_corpora_reach_every_scheme(self):
        names = oracle.corpus_names()
        assert set(names) == {"itanium", "msvc", "rust", "d"}
        assert "_Zcv1BIRT_EIS1_E" in names["itanium"]
        assert "??_7A@@6BB@@C@@@" in names["msvc"]
        assert all(names.values())

    def test_the_reference_defects_are_the_two_declaration_corpora(self):
        assert "_Z10value_initIiEDTnw_T_piEES0_" in oracle.reference_defects("itanium")
        assert "?alpha@@YAX_L@Z" in oracle.reference_defects("msvc")
        assert oracle.reference_defects("rust") == frozenset()
        assert oracle.reference_defects("itanium", ROOT) == frozenset()


class TestReaders:
    def test_itanium_leaves_the_symbol_version_out_of_the_reader_and_puts_it_back(self):
        read = oracle.readers(_package(), 21)["itanium"]
        assert read("_Z1fv@@GLIBCXX_3.4") == "it<_Z1fv>@@GLIBCXX_3.4"
        assert read("_Z1fv") == "it<_Z1fv>"
        assert read("x@y") is None

    def test_msvc_keeps_the_text_and_not_the_count_of_bytes_read(self):
        read = oracle.readers(_package(), 21)["msvc"]
        assert read("?x@@3HA)") == "ms:?x@@3HA)"
        assert read("_Z1fv") is None

    def test_an_old_llvm_has_no_rust_or_d_reader(self):
        assert sorted(oracle.readers(_package(), 12)) == ["itanium", "msvc"]
        assert sorted(oracle.readers(_package(), 13)) == ["itanium", "msvc", "rust"]
        assert sorted(oracle.readers(_package(), 14)) == ["d", "itanium", "msvc", "rust"]

    def test_a_version_the_package_does_not_bundle_is_a_mistake(self):
        with pytest.raises(ValueError, match="LLVM 7 is not bundled"):
            oracle.readers(_package(), 7)

    def test_the_newest_is_the_largest_not_the_default(self):
        assert oracle.newest(SimpleNamespace(SUPPORTED_VERSIONS=[11, 21, 14])) == 21


class TestScore:
    def score(self, scheme, answers, defects=frozenset(), opinion=None):
        return oracle.score(scheme, list(answers), answers.get, defects=defects, opinion=opinion)

    def test_each_name_lands_in_one_place(self):
        answers = {
            "_Z1fv": "f()",  # agree
            "_Z": None,  # both decline: agree
            "_Z1gv": "something else",  # differs, nothing accounts for it
            "_Z1hv": None,  # LLVM declines what this reads
            "_Zcv1BIRT_EIS1_E": "operator B<><>",  # this library declines what LLVM reads
            "_Z1iv": "i is wrong",  # differs, but a reference-defects corpus says so
        }
        result = self.score("itanium", answers, defects={"_Z1iv"})
        assert result["source"] == "llvm-itanium"
        assert (result["offered"], result["agree"], result["known"]) == (6, 2, 1)
        assert result["unaccounted"] == [("_Z1gv", "something else", "g()")]
        assert result["declined"] == [("_Z1hv", "h()")]
        assert result["refused"] == [("_Zcv1BIRT_EIS1_E", "operator B<><>")]

    def test_an_accept_rule_for_the_scheme_accounts_for_a_difference(self):
        """llvm-undname keeps one base of a vftable's path; `ACCEPTED["msvc"]` says why."""
        ours = "const A::`vftable'{for `B's `C'}"
        result = self.score("msvc", {"??_7A@@6BB@@C@@@": "const A::`vftable'{for `B'}"})
        assert (result["known"], result["unaccounted"]) == (1, [])
        assert oracle._ours("msvc", "??_7A@@6BB@@C@@@") == ours

    def test_the_second_opinion_is_asked_only_about_names_nothing_cheaper_accounted_for(self):
        asked = []

        def opinion(names):
            asked.append(names)
            return dict.fromkeys(names, (None, None))

        answers = {"_Z1fv": "f()", "_Z1gv": "other", "_Z1hv": "different", "_Z1iv": "i()"}
        result = self.score("itanium", answers, defects={"_Z1hv"}, opinion=opinion)
        assert asked == [["_Z1gv"]]
        assert [name for name, _, _ in result["unaccounted"]] == ["_Z1gv"]
        assert result["known"] == 1

    def test_the_second_opinion_reaches_the_accept_rule(self):
        """`ACCEPTED["itanium"]` takes both references agreeing with this library as an excuse."""
        answers = {"_Z1gv": "other"}
        for vote, known in (((None, None), 0), (("one", "other"), 0), (("same", "same"), 1)):
            result = self.score("itanium", answers, opinion=lambda names, vote=vote: {"_Z1gv": vote})
            assert result["known"] == known, vote

    def test_the_arm64ec_marker_is_asked_again_without_it(self):
        marked, plain = "?foo@@$$hYAXI@Z", "?foo@@YAXI@Z"
        answers = {marked: None, plain: "void __cdecl foo(unsigned int)"}
        result = oracle.score("msvc", [marked], answers.get)
        assert (result["agree"], result["declined"], result["unaccounted"]) == (1, [], [])

    def test_a_stand_in_that_is_declined_does_not_overwrite_a_reading_that_keeps_the_marker(self):
        name = "?foo$$hbar@@YAXXZ"
        assert "$$h" in oracle._ours("msvc", name)
        result = oracle.score("msvc", [name], {name: "void __cdecl foo$$hbar(void)"}.get)
        assert result["agree"] == 1

    def test_a_name_the_c_api_cannot_take_is_declined(self):
        def read(name):
            raise ValueError(name)

        assert oracle.score("itanium", ["_Z1fv"], read)["declined"] == [("_Z1fv", "f()")]


class TestSpellings:
    def test_llvm_sets_a_rust_symbols_suffix_in_parentheses(self):
        assert oracle._suffix_in_parentheses("foo", "foo (.llvm.9D1C9369)")
        assert oracle._suffix_in_parentheses("a::FORK.0.0", "a::FORK (.0.0)")
        assert not oracle._suffix_in_parentheses("bar", "foo (.llvm.9D1C9369)")
        assert not oracle._suffix_in_parentheses("foo", "foo (not a suffix)")

    def test_llvm_escapes_a_non_ascii_char_constant(self):
        assert oracle._non_ascii_char_escaped("f::<'ß'>", "f::<'\\u{df}'>")
        assert not oracle._non_ascii_char_escaped("f::<'a'>", "f::<'a'>")
        assert not oracle._non_ascii_char_escaped("f::<'ß'>", "f::<'\\u{e0}'>")

    def test_llvms_d_reader_leaves_a_template_instance_as_the_mangling_writes_it(self):
        assert oracle._template_instance_left_as_written('test.bar!("abc"w).x', "test.__T3barVG3uw3_616263Z.x")
        assert not oracle._template_instance_left_as_written("a.b", "a.c")

    def test_rust_and_d_each_have_their_own_rules_and_the_others_do_not(self):
        assert set(oracle.SPELLINGS) == {"rust", "d"}
        rust = oracle.score("rust", ["_RC3foo.llvm.9D1C9369"], {"_RC3foo.llvm.9D1C9369": "foo (.llvm.9D1C9369)"}.get)
        assert (rust["known"], rust["unaccounted"]) == (1, [])


class TestRun:
    def test_a_missing_package_is_unavailable_and_says_how_to_install_it(self, monkeypatch):
        monkeypatch.setattr(oracle, "binding", lambda: None)
        with pytest.raises(oracle.Unavailable, match=r"pip install llvm-demangle-fxti==1\.0\.0"):
            oracle.run(["itanium"])

    def test_a_version_nothing_bundles_is_the_version_error_whether_or_not_the_package_is_there(self, monkeypatch):
        """The version is settled first, and without the package: absent, 7 used to read as a skip."""

        def looked_for_the_package():
            raise AssertionError("the package was looked for before the version was checked")

        monkeypatch.setattr(oracle, "binding", looked_for_the_package)
        for version in (7, 10, 22, 0, -1):
            with pytest.raises(ValueError, match=rf"LLVM {version} is not bundled; llvm-demangle-fxti has \[11, 12,"):
                oracle.run(["itanium"], version)
        monkeypatch.setattr(oracle, "binding", lambda: None)
        with pytest.raises(ValueError, match="LLVM 7 is not bundled"):
            oracle.run(["itanium"], 7)
        monkeypatch.setattr(oracle, "binding", lambda: _package())
        with pytest.raises(ValueError, match="LLVM 7 is not bundled"):
            oracle.run(["itanium"], 7)

    def test_a_bundled_version_with_no_package_is_still_unavailable(self, monkeypatch):
        monkeypatch.setattr(oracle, "binding", lambda: None)
        for version in (None, 11, 18, 21):
            with pytest.raises(oracle.Unavailable):
                oracle.run(["itanium"], version)

    def test_the_releases_held_to_before_the_import_are_the_ones_the_package_has(self):
        """`BUNDLED` moves with the pin; where the package is installed, this says when it has not."""
        package = pytest.importorskip("llvmdemangle")
        assert list(oracle.BUNDLED) == sorted(package.SUPPORTED_VERSIONS)

    def test_the_newest_version_is_asked_unless_one_is_named(self, monkeypatch):
        monkeypatch.setattr(oracle, "binding", lambda: _package())
        monkeypatch.setattr(oracle, "corpus_names", lambda: {"itanium": ["_Z1fv"], "msvc": [], "rust": [], "d": []})
        monkeypatch.setattr(oracle, "gnu_opinion", lambda names: {})
        [result] = oracle.run(["itanium"])
        assert result["reference"] == "LLVM 21"
        assert result["declined"] == []
        assert result["unaccounted"] == [("_Z1fv", "it<_Z1fv>", "f()")]
        [result] = oracle.run(["itanium"], 12)
        assert result["reference"] == "LLVM 12"

    def test_a_scheme_the_version_cannot_read_is_skipped_aloud(self, monkeypatch, capsys):
        monkeypatch.setattr(oracle, "binding", lambda: _package())
        monkeypatch.setattr(oracle, "corpus_names", lambda: {"itanium": [], "msvc": [], "rust": [], "d": []})
        results = oracle.run(["itanium", "d"], 12)
        assert [r["source"] for r in results] == ["llvm-itanium"]
        assert "LLVM 12 has no d reader" in capsys.readouterr().err

    def test_no_c_plus_plus_filt_means_no_second_opinion(self, monkeypatch):
        monkeypatch.setattr(oracle.shutil, "which", lambda tool: None)
        assert oracle.gnu_opinion(["_Z1fv"]) == {}
        assert oracle.gnu_opinion([]) == {}
