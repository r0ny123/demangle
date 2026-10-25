"""The disassembler scripts, run against stand-ins for the hosts they are written for.

IDA, Binary Ninja and Ghidra cannot be run here, so these tests do not show that the
scripts work in them; `test_integrations_ghidra_live.py` does that for Ghidra, when asked
to. What these show is narrower: that each script, given a host that
behaves as the host's documentation says, makes the calls it means to, in a sensible
order, and skips what it means to skip. The stand-ins are written from the same
documentation as the scripts, so a mistake about what a host call does is in both and
none of this will find it. `docs/integrations.md` says the same to the reader.

Each stand-in implements only what its script calls.
"""

import importlib.util
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

INTEGRATIONS = Path(__file__).parent.parent / "integrations"

SWIFT = "$s10Foundation4DataV5countSivg"
RUST = "_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E"
ITANIUM = "_ZNSt6vectorIiSaIiEE9push_backERKi"
OVERLOAD_INT = "_ZN3Foo3barEi"
OVERLOAD_CHAR = "_ZN3Foo3barEc"


def stand_in(name, **attributes):
    module = types.ModuleType(name)
    vars(module).update(attributes)
    return module


def load(script, monkeypatch, modules):
    """The script, run as a module with `modules` standing in for the host's."""
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location(f"{script}_under_test", INTEGRATIONS / f"{script}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeIda:
    """What `demangle_ida.py` calls of IDAPython, with a database of names and comments."""

    SN_NOCHECK, SN_NOWARN, SN_FORCE = 0x1, 0x100, 0x800
    SEG_XTRN = 7

    def __init__(self, names, *, reads=None, imports=(), external=(), comments=None):
        self.names = names
        self.reads = reads or {}
        self.imports = set(imports)
        self.external = set(external)
        self.comments = dict(comments or {})
        self.renames = {}
        self.wait_box = []
        self.cancel_at = None

    def modules(self):
        return {
            "ida_bytes": stand_in("ida_bytes", get_cmt=self.get_cmt, set_cmt=self.set_cmt),
            "ida_kernwin": stand_in(
                "ida_kernwin",
                show_wait_box=lambda text: self.wait_box.append(("show", text)),
                replace_wait_box=lambda text: self.wait_box.append(("replace", text)),
                hide_wait_box=lambda: self.wait_box.append(("hide", None)),
                user_cancelled=self.user_cancelled,
            ),
            "ida_nalt": stand_in("ida_nalt", get_import_module_qty=lambda: 2, enum_import_names=self.enum_import_names),
            "ida_name": stand_in(
                "ida_name",
                DQT_FULL=0,
                SN_NOCHECK=self.SN_NOCHECK,
                SN_NOWARN=self.SN_NOWARN,
                SN_FORCE=self.SN_FORCE,
                demangle_name=lambda name, mask, request: self.reads.get(name),
                set_name=self.set_name,
            ),
            "ida_segment": stand_in("ida_segment", SEG_XTRN=self.SEG_XTRN, getseg=self.getseg),
            "idautils": stand_in("idautils", Names=lambda: iter(self.names)),
        }

    def get_cmt(self, address, repeatable):
        assert repeatable is True
        return self.comments.get(address)

    def set_cmt(self, address, text, repeatable):
        assert repeatable is True
        self.comments[address] = text
        return True

    def set_name(self, address, name, flags):
        self.renames[address] = (name, flags)
        return True

    def enum_import_names(self, module_index, callback):
        # The imports are in the first of two modules, so a script has to ask for both.
        if module_index == 0:
            for address in sorted(self.imports):
                assert callback(address, "imported", 0)
        return 1

    def getseg(self, address):
        return types.SimpleNamespace(type=self.SEG_XTRN) if address in self.external else None

    def user_cancelled(self):
        if self.cancel_at is None:
            return False
        self.cancel_at -= 1
        return self.cancel_at < 0


@pytest.fixture
def ida(monkeypatch):
    """A function making a database of `names` and returning it with the script loaded against it."""

    def make(names, **kwargs):
        host = FakeIda(names, **kwargs)
        return types.SimpleNamespace(host=host, script=load("demangle_ida", monkeypatch, host.modules()))

    return make


IDA_TABLE = [(0x1000, SWIFT), (0x2000, "main"), (0x3000, ITANIUM), (0x4000, RUST)]

#: IDA reads the C++ name, the Rust one wrongly, and the Swift one not at all.
IDA_READS = {
    ITANIUM: "std::vector<int>::push_back(int const&)",
    RUST: "core::fmt::Formatter::pad::h9b2b3a0e5b4d1b31()",
}

SWIFT_READING = "Foundation.Data.count.getter : Swift.Int"


class TestIda:
    def test_the_default_comments_the_names_ida_did_not_read_and_renames_nothing(self, ida, capsys):
        session = ida(IDA_TABLE, reads=IDA_READS)
        session.script.run()
        assert session.host.comments == {0x1000: SWIFT_READING}
        assert session.host.renames == {}
        assert "1 changed" in capsys.readouterr().out

    def test_the_option_comments_the_names_ida_read_wrongly_too(self, ida, monkeypatch):
        session = ida(IDA_TABLE, reads=IDA_READS)
        monkeypatch.setattr(session.script, "ONLY_LEFT_MANGLED", False)
        session.script.run()
        assert session.host.comments[0x4000] == "core::fmt::Formatter::pad"
        assert session.host.comments[0x3000] == "std::vector<int, std::allocator<int>>::push_back(int const&)"

    def test_a_comment_somebody_wrote_is_kept_unless_asked_not_to(self, ida, monkeypatch, capsys):
        session = ida(IDA_TABLE, reads=IDA_READS, comments={0x1000: "my note"})
        session.script.run()
        assert session.host.comments == {0x1000: "my note"}
        assert "1 kept" in capsys.readouterr().out
        monkeypatch.setattr(session.script, "OVERWRITE_COMMENTS", True)
        session.script.run()
        assert session.host.comments == {0x1000: SWIFT_READING}

    def test_a_second_run_finds_nothing_to_change(self, ida, capsys):
        session = ida(IDA_TABLE, reads=IDA_READS)
        session.script.run()
        capsys.readouterr()
        session.script.run()
        assert "0 changed, 1 kept" in capsys.readouterr().out

    def test_name_mode_renames_and_leaves_ida_to_cope_with_what_it_does_not_accept(self, ida, monkeypatch):
        session = ida(IDA_TABLE, reads=IDA_READS)
        monkeypatch.setattr(session.script, "MODE", "name")
        session.script.run()
        # NOCHECK so IDA replaces what it rejects, NOWARN so it does not ask, FORCE so a taken name gets a suffix.
        assert session.host.renames == {0x1000: ("Foundation.Data.count.getter", 0x1 | 0x100 | 0x800)}
        assert session.host.comments == {}

    def test_name_mode_spells_the_parameters_when_asked(self, ida, monkeypatch):
        session = ida([(0x1000, OVERLOAD_INT)])
        monkeypatch.setattr(session.script, "MODE", "name")
        monkeypatch.setattr(session.script, "FORM", "full")
        session.script.run()
        assert session.host.renames[0x1000][0] == "Foo::bar(int)"

    def test_name_mode_leaves_the_import_table_and_the_external_segment_alone(self, ida, monkeypatch):
        session = ida([(0x1000, SWIFT), (0x2000, SWIFT), (0x3000, SWIFT)], imports={0x1000}, external={0x2000})
        monkeypatch.setattr(session.script, "MODE", "name")
        session.script.run()
        assert set(session.host.renames) == {0x3000}

    def test_comment_mode_has_no_reason_to_leave_them(self, ida):
        session = ida([(0x1000, SWIFT)], imports={0x1000})
        session.script.run()
        assert 0x1000 in session.host.comments

    def test_a_dry_run_changes_nothing_and_says_what_it_would(self, ida, monkeypatch, capsys):
        session = ida(IDA_TABLE, reads=IDA_READS)
        monkeypatch.setattr(session.script, "DRY_RUN", True)
        session.script.run()
        output = capsys.readouterr().out
        assert session.host.comments == {} and session.host.renames == {}
        assert "1 would change" in output and SWIFT in output

    def test_the_report_is_cut_at_the_limit_and_says_how_much_more(self, ida, monkeypatch, capsys):
        session = ida([(address, SWIFT) for address in range(0x1000, 0x1005)])
        monkeypatch.setattr(session.script, "REPORT_LIMIT", 2)
        session.script.run()
        output = capsys.readouterr().out
        assert output.count(" -> ") == 2
        assert "5 changed" in output and "3 more not listed" in output

    def test_cancelling_while_reading_leaves_the_database_as_it_was(self, ida, monkeypatch, capsys):
        session = ida([(address, SWIFT) for address in range(0x1000, 0x1010)])
        monkeypatch.setattr(session.script, "STRIDE", 4)
        session.host.cancel_at = 2
        session.script.run()
        assert session.host.comments == {}
        assert "cancelled" in capsys.readouterr().out
        assert session.host.wait_box[-1] == ("hide", None)

    def test_the_wait_box_is_closed_when_a_call_fails(self, ida):
        session = ida(IDA_TABLE, reads=IDA_READS)

        def fail(address, repeatable):
            raise RuntimeError("database is read only")

        session.script.ida_bytes.get_cmt = fail
        with pytest.raises(RuntimeError, match="read only"):
            session.script.run()
        assert session.host.wait_box[0][0] == "show" and session.host.wait_box[-1] == ("hide", None)

    @pytest.mark.parametrize(("setting", "value"), [("MODE", "rename"), ("FORM", "long")])
    def test_a_setting_that_means_nothing_is_refused_before_ida_is_touched(self, ida, monkeypatch, setting, value):
        session = ida(IDA_TABLE)
        monkeypatch.setattr(session.script, setting, value)
        with pytest.raises(ValueError, match=setting):
            session.script.run()
        assert session.host.wait_box == []

    def test_a_setting_the_library_refuses_is_refused_before_ida_is_touched(self, ida, monkeypatch):
        session = ida(IDA_TABLE)
        monkeypatch.setattr(session.script, "LANGUAGES", ("swfit",))
        with pytest.raises(ValueError, match="swfit"):
            session.script.run()
        assert session.host.wait_box == []


class FakeBinaryNinja:
    """What `demangle_binja.py` calls of the Binary Ninja API."""

    def __init__(self):
        self.commands = []
        self.log = []

    def modules(self):
        outer = self

        class BackgroundTaskThread:
            def __init__(self, initial_progress_text="", can_cancel=False):
                self.progress = initial_progress_text
                self.can_cancel = can_cancel
                self.cancelled = False
                self.finished = False

            def finish(self):
                self.finished = True

        class Symbol:
            # The constructor's signature as the API documents it
            # (https://api.binary.ninja/binaryninja.types-module.html), with no `**kwargs`:
            # a keyword the real one does not take is a TypeError here too, and a keyword
            # left out is seen in what reaches `define_user_symbol`.
            def __init__(
                self, sym_type, addr, short_name, full_name=None, raw_name=None, binding=None, namespace=None, ordinal=0
            ):
                self.type, self.address = sym_type, addr
                self.short_name = short_name
                self.full_name = full_name or short_name
                self.raw_name = raw_name or self.full_name
                self.binding, self.namespace, self.ordinal = binding, namespace, ordinal

        class PluginCommand:
            @staticmethod
            def register(name, description, action, is_valid=None):
                outer.commands.append((name, action))

        symbol_type = types.SimpleNamespace(
            FunctionSymbol="function", DataSymbol="data", ImportedFunctionSymbol="imported", ExternalSymbol="external"
        )
        return {
            "binaryninja": stand_in(
                "binaryninja",
                BackgroundTaskThread=BackgroundTaskThread,
                PluginCommand=PluginCommand,
                Symbol=Symbol,
                SymbolType=symbol_type,
                log_info=lambda text: self.log.append(("info", text)),
                log_error=lambda text: self.log.append(("error", text)),
            )
        }


class FakeView:
    """A `BinaryView`: symbols, and a record of what was done to them in what order."""

    def __init__(self, symbols, fail_on=()):
        self.symbols = symbols
        self.fail_on = set(fail_on)
        self.events = []

    def get_symbols(self):
        return self.symbols

    @contextmanager
    def undoable_transaction(self):
        self.events.append("begin transaction")
        yield
        self.events.append("end transaction")

    @contextmanager
    def bulk_modify_symbols(self):
        self.events.append("begin bulk")
        yield
        self.events.append("end bulk")

    def undefine_user_symbol(self, symbol):
        self.events.append(("undefine", symbol.raw_name))

    def define_user_symbol(self, symbol):
        if symbol.raw_name in self.fail_on:
            raise RuntimeError("refused")
        self.events.append(("define", symbol))

    def defined(self):
        return [event[1] for event in self.events if isinstance(event, tuple) and event[0] == "define"]

    def undefined(self):
        return [event[1] for event in self.events if isinstance(event, tuple) and event[0] == "undefine"]


class Task:
    def __init__(self, cancelled=False):
        self.progress = ""
        self.cancelled = cancelled


@pytest.fixture
def binja(monkeypatch):
    host = FakeBinaryNinja()
    script = load("demangle_binja", monkeypatch, host.modules())

    def symbol(kind, address, raw, *, full=None, auto=True):
        made = script.Symbol(kind, address, full or raw, full_name=full or raw, raw_name=raw)
        made.auto, made.binding, made.namespace, made.ordinal = auto, "global", "ns", 7
        return made

    return types.SimpleNamespace(host=host, script=script, symbol=symbol)


class TestBinaryNinja:
    def test_loading_it_registers_one_command_in_a_menu_of_its_own(self, binja):
        assert [name for name, _action in binja.host.commands] == ["Demangle\\Label mangled symbols"]

    def test_running_the_command_starts_a_background_task_on_the_view(self, binja, monkeypatch):
        started = []

        class Recording:
            def __init__(self, bv):
                self.bv = bv

            def start(self):
                started.append(self.bv)

        monkeypatch.setattr(binja.script, "DemangleTask", Recording)
        view = FakeView([])
        binja.host.commands[0][1](view)
        assert started == [view]

    def test_the_task_can_be_cancelled_and_says_what_it_is_doing(self, binja):
        task = binja.script.DemangleTask(FakeView([]))
        assert task.can_cancel and task.progress == "Demangle: reading symbols"

    def test_a_symbol_binary_ninja_left_mangled_gains_a_short_and_a_full_name(self, binja):
        view = FakeView([binja.symbol("function", 0x1000, SWIFT)])
        binja.script.label_symbols(view, Task())
        (defined,) = view.defined()
        assert defined.type == "function" and defined.address == 0x1000
        assert defined.short_name == "Foundation.Data.count.getter"
        assert defined.full_name == SWIFT_READING
        assert defined.raw_name == SWIFT
        assert (defined.binding, defined.namespace, defined.ordinal) == ("global", "ns", 7)

    def test_the_short_name_can_be_the_full_one(self, binja, monkeypatch):
        monkeypatch.setattr(binja.script, "FORM", "full")
        view = FakeView([binja.symbol("function", 0x1000, SWIFT)])
        binja.script.label_symbols(view, Task())
        (defined,) = view.defined()
        assert defined.short_name == defined.full_name == SWIFT_READING

    def test_what_binary_ninja_read_and_what_is_not_the_binarys_own_is_left_alone(self, binja):
        view = FakeView(
            [
                binja.symbol("function", 0x1000, ITANIUM, full="std::vector<int>::push_back(int const&)"),
                binja.symbol("imported", 0x2000, SWIFT),
                binja.symbol("external", 0x3000, SWIFT),
                binja.symbol("data", 0x4000, "main"),
            ]
        )
        binja.script.label_symbols(view, Task())
        assert view.events == []
        assert "0 readable" in binja.host.log[-1][1]

    def test_the_option_relabels_what_binary_ninja_read_too(self, binja, monkeypatch):
        monkeypatch.setattr(binja.script, "ONLY_LEFT_MANGLED", False)
        view = FakeView([binja.symbol("function", 0x1000, RUST, full="core::fmt::Formatter::pad::h9b2b3a0e5b4d1b31()")])
        binja.script.label_symbols(view, Task())
        assert [symbol.full_name for symbol in view.defined()] == ["core::fmt::Formatter::pad"]

    def test_a_user_symbol_is_undefined_first_and_an_auto_one_is_not(self, binja):
        view = FakeView([binja.symbol("function", 0x1000, SWIFT, auto=False), binja.symbol("function", 0x2000, RUST)])
        binja.script.label_symbols(view, Task())
        assert view.undefined() == [SWIFT]
        undefine = view.events.index(("undefine", SWIFT))
        define = next(i for i, event in enumerate(view.events) if isinstance(event, tuple) and event[0] == "define")
        assert undefine < define

    def test_it_is_one_undoable_step_with_the_symbol_table_rebuilt_once(self, binja):
        view = FakeView([binja.symbol("function", 0x1000, SWIFT), binja.symbol("function", 0x2000, RUST)])
        binja.script.label_symbols(view, Task())
        assert view.events[:2] == ["begin transaction", "begin bulk"]
        assert view.events[-2:] == ["end bulk", "end transaction"]
        assert len(view.defined()) == 2

    def test_a_symbol_that_fails_does_not_undo_the_others(self, binja):
        view = FakeView(
            [binja.symbol("function", 0x1000, SWIFT), binja.symbol("function", 0x2000, RUST)], fail_on={SWIFT}
        )
        binja.script.label_symbols(view, Task())
        assert view.events[-1] == "end transaction"
        assert [symbol.raw_name for symbol in view.defined()] == [RUST]
        assert ("error", f"Demangle: 0x1000  {SWIFT}: refused") in binja.host.log
        assert "1 failed" in binja.host.log[-1][1]

    def test_a_dry_run_opens_no_transaction(self, binja, monkeypatch):
        monkeypatch.setattr(binja.script, "DRY_RUN", True)
        view = FakeView([binja.symbol("function", 0x1000, SWIFT)])
        binja.script.label_symbols(view, Task())
        assert view.events == []
        assert "1 would change" in binja.host.log[-1][1]

    def test_a_cancel_changes_nothing(self, binja):
        view = FakeView([binja.symbol("function", 0x1000, SWIFT)])
        binja.script.label_symbols(view, Task(cancelled=True))
        assert view.events == []

    def test_a_setting_that_means_nothing_is_refused(self, binja, monkeypatch):
        monkeypatch.setattr(binja.script, "FORM", "long")
        with pytest.raises(ValueError, match="FORM"):
            binja.script.label_symbols(FakeView([]), Task())

    def test_the_task_always_finishes_and_reports_what_went_wrong(self, binja):
        task = binja.script.DemangleTask(FakeView(None))  # a view that cannot list its symbols
        task.run()
        assert task.finished
        assert binja.host.log[0][0] == "error"


class FakeAddress:
    def __init__(self, offset):
        self.offset = offset

    def getOffset(self):
        return self.offset

    def __eq__(self, other):
        return isinstance(other, FakeAddress) and other.offset == self.offset

    def __hash__(self):
        return hash(self.offset)

    def __str__(self):
        return f"{self.offset:08x}"


class FakeSymbol:
    def __init__(self, table, offset, name, *, kind="label", source="imported", external=False, primary=True):
        self.table, self.address = table, FakeAddress(offset)
        self.name, self.kind, self.source = name, kind, source
        self.external, self.primary = external, primary

    def getName(self):
        return self.name

    def setName(self, name, source):
        if any(other.name == name for other in self.table.symbols if other is not self):
            raise ValueError("DuplicateNameException")
        self.name = name
        self.table.renamed.append((self.address.offset, name, source))

    def getAddress(self):
        return self.address

    def isExternal(self):
        return self.external

    def isPrimary(self):
        return self.primary

    def getSource(self):
        return self.source

    def getSymbolType(self):
        return self.kind


class FakeTable:
    def __init__(self):
        self.symbols = []
        self.renamed = []
        self.created = []

    def add(self, *args, **kwargs):
        symbol = FakeSymbol(self, *args, **kwargs)
        self.symbols.append(symbol)
        return symbol

    def getAllSymbols(self, include_dynamic):
        assert include_dynamic is False
        return list(self.symbols)

    def getPrimarySymbol(self, address):
        return next(s for s in self.symbols if s.address == address and s.primary)

    def createLabel(self, address, name, source):
        self.created.append((address.offset, name, source))


class FakeMonitor:
    def __init__(self, cancel_after=None):
        self.messages, self.progress, self.cancel_after = [], [], cancel_after

    def initialize(self, maximum):
        self.maximum = maximum

    def setMessage(self, message):
        self.messages.append(message)

    def setProgress(self, value):
        self.progress.append(value)

    def isCancelled(self):
        if self.cancel_after is None:
            return False
        self.cancel_after -= 1
        return self.cancel_after < 0


@pytest.fixture
def ghidra(monkeypatch):
    sources = types.SimpleNamespace(DEFAULT="default", USER_DEFINED="user", IMPORTED="imported")
    kinds = types.SimpleNamespace(LABEL="label", FUNCTION="function", NAMESPACE="namespace")
    utilities = types.SimpleNamespace(replaceInvalidChars=lambda text, underscore: text.replace(" ", "_"))
    modules = {
        "ghidra": stand_in("ghidra"),
        "ghidra.program": stand_in("ghidra.program"),
        "ghidra.program.model": stand_in("ghidra.program.model"),
        "ghidra.program.model.symbol": stand_in(
            "ghidra.program.model.symbol", SourceType=sources, SymbolType=kinds, SymbolUtilities=utilities
        ),
    }
    table = FakeTable()
    return types.SimpleNamespace(
        script=load("demangle_ghidra", monkeypatch, modules),
        table=table,
        program=types.SimpleNamespace(getSymbolTable=lambda: table),
    )


class TestGhidra:
    def test_it_declares_the_runtime_ghidra_needs_to_run_it_as_python_3(self):
        header = (INTEGRATIONS / "demangle_ghidra.py").read_text(encoding="utf-8").splitlines()[:5]
        assert "# @runtime PyGhidra" in header and any(line.startswith("# @category ") for line in header)

    def test_a_mangled_label_is_renamed_and_the_mangled_name_kept_beside_it(self, ghidra):
        ghidra.table.add(0x1000, SWIFT, kind="function")
        ghidra.script.main(ghidra.program, FakeMonitor())
        assert ghidra.table.renamed == [(0x1000, "Foundation.Data.count.getter", "user")]
        assert ghidra.table.created == [(0x1000, SWIFT, "imported")]

    def test_the_original_can_be_let_go(self, ghidra, monkeypatch):
        monkeypatch.setattr(ghidra.script, "KEEP_ORIGINAL", False)
        ghidra.table.add(0x1000, SWIFT)
        ghidra.script.main(ghidra.program, FakeMonitor())
        assert ghidra.table.renamed and ghidra.table.created == []

    def test_what_is_not_a_name_of_the_binarys_own_is_left_alone(self, ghidra):
        ghidra.table.add(0x1000, SWIFT, external=True)
        ghidra.table.add(0x2000, SWIFT, source="default")
        ghidra.table.add(0x3000, SWIFT, kind="namespace")
        ghidra.table.add(0x4000, "main")
        ghidra.script.main(ghidra.program, FakeMonitor())
        assert ghidra.table.renamed == []

    def test_a_name_ghidra_has_already_put_a_readable_one_in_front_of_is_left_alone(self, ghidra):
        """Its demangler leaves the mangled name behind as a second label; that is not work to do."""
        ghidra.table.add(0x1000, "Foundation.Data.count.getter")
        ghidra.table.add(0x1000, SWIFT, primary=False)
        ghidra.script.main(ghidra.program, FakeMonitor())
        assert ghidra.table.renamed == [] and ghidra.table.created == []

    def test_the_full_name_has_its_spaces_cleaned_by_ghidra_and_not_by_the_script(self, ghidra, monkeypatch):
        monkeypatch.setattr(ghidra.script, "FORM", "full")
        ghidra.table.add(0x1000, "_ZN3Foo3barEPKc")
        ghidra.script.main(ghidra.program, FakeMonitor())
        assert ghidra.table.renamed[0][1] == "Foo::bar(char_const*)"

    def test_overloads_share_a_short_name_and_the_second_gets_its_address(self, ghidra):
        ghidra.table.add(0x1000, OVERLOAD_INT)
        ghidra.table.add(0x2000, OVERLOAD_CHAR)
        ghidra.script.main(ghidra.program, FakeMonitor())
        renamed = [(offset, name) for offset, name, _ in ghidra.table.renamed]
        assert renamed == [(0x1000, "Foo::bar"), (0x2000, "Foo::bar_00002000")]

    def test_a_failed_rename_is_counted_and_the_rest_go_on(self, ghidra, capsys):
        broken = ghidra.table.add(0x1000, SWIFT)

        def refuse(name, source):
            raise ValueError("InvalidInputException")

        broken.setName = refuse
        ghidra.table.add(0x2000, RUST)
        ghidra.script.main(ghidra.program, FakeMonitor())
        assert [offset for offset, _, _ in ghidra.table.renamed] == [0x2000]
        assert "1 failed" in capsys.readouterr().out

    def test_a_dry_run_renames_nothing(self, ghidra, monkeypatch, capsys):
        monkeypatch.setattr(ghidra.script, "DRY_RUN", True)
        ghidra.table.add(0x1000, SWIFT)
        ghidra.script.main(ghidra.program, FakeMonitor())
        assert ghidra.table.renamed == [] and ghidra.table.created == []
        assert "1 would change" in capsys.readouterr().out

    def test_a_cancel_while_reading_renames_nothing(self, ghidra, monkeypatch, capsys):
        monkeypatch.setattr(ghidra.script, "STRIDE", 2)
        for offset in range(0x1000, 0x1008):
            ghidra.table.add(offset, SWIFT)
        ghidra.script.main(ghidra.program, FakeMonitor(cancel_after=1))
        assert ghidra.table.renamed == []
        assert "cancelled" in capsys.readouterr().out

    def test_the_monitor_is_told_what_the_script_is_doing(self, ghidra):
        ghidra.table.add(0x1000, SWIFT)
        monitor = FakeMonitor()
        ghidra.script.main(ghidra.program, monitor)
        assert monitor.messages == ["Reading symbols", "Labelling symbols"]

    def test_a_setting_that_means_nothing_is_refused(self, ghidra, monkeypatch):
        monkeypatch.setattr(ghidra.script, "FORM", "long")
        with pytest.raises(ValueError, match="FORM"):
            ghidra.script.main(ghidra.program, FakeMonitor())
