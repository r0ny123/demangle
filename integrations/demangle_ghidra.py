# Label the symbols Ghidra's demanglers leave mangled, using the `demangle` library.
# @category Symbol
# @runtime PyGhidra
"""Label the symbols Ghidra's demanglers leave mangled, using the `demangle` library.

A PyGhidra script: Ghidra 11.3 or later, started in PyGhidra mode (`support/pyghidraRun`)
from a Python environment that has the `demangle` package. Put this file and
`demangle_labels.py` in a Ghidra script directory, find the script in the Script Manager
under Symbol, and run it with a program open. The page "Disassembler integrations" in
the demangle documentation has the steps.

Each symbol read is renamed to the readable name, and the mangled name is kept as a
second label at the same address, which is how Ghidra's own demangler leaves a symbol it
has read. Change the settings below before running.

Run headless in Ghidra 12.1.4 through PyGhidra 3.1.0, on Rust, C++ and Go programs; the
documentation page has the results. Other versions are untested. If a call here is not
what your Ghidra has, the script's console names it.
"""

import os
import sys

from ghidra.program.model.symbol import SourceType, SymbolType, SymbolUtilities

# ---------------------------------------------------------------------------------------
# Settings

#: "short" names the symbol `std::vector<int>::push_back`, "full" adds the parameters.
#: Ghidra replaces the characters it does not accept in a name, spaces among them, with
#: `_`. A name another symbol already has gets the address after it.
FORM = "short"

#: Leave the mangled name behind as a second label at the address. Off, it is gone.
KEEP_ORIGINAL = True

#: The schemes allowed to read a name. None lets all of them try; a tuple such as
#: ("swift", "go", "rust") allows only those. Leave Objective-C out for a Mach-O image.
LANGUAGES = None

#: "llvm" or "gnu", the spelling of types and templates; None is the default, "llvm".
STYLE = None

#: Longest label in characters; longer ones are cut. Ghidra limits a name to 2000.
MAX_LENGTH = 255

#: Print what would change and change nothing.
DRY_RUN = False

#: How many changes to print. The count at the end is always the whole of them.
REPORT_LIMIT = 50

# ---------------------------------------------------------------------------------------

#: Namespaces, classes, parameters and local variables are symbols too, and not names of
#: anything the mangled name described.
LABELLED_TYPES = (SymbolType.LABEL, SymbolType.FUNCTION)

#: The monitor is updated and checked for a cancel after this many symbols.
STRIDE = 500

# A script is not always run with its own folder on `sys.path`.
_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
if _HERE and _HERE not in sys.path:
    sys.path.append(_HERE)

import demangle_labels  # noqa: E402


class Cancelled(Exception):
    """The monitor was cancelled."""


def collect(program):
    """The entries for the program's symbols, and the symbols by `(offset, name)`.

    Left out: external symbols, which are other libraries' and are what the imports
    resolve to; symbols Ghidra named itself (`FUN_00401000`); and anything that is not a
    label or a function.
    """
    table = program.getSymbolTable()
    symbols = {}
    entries = []
    for symbol in table.getAllSymbols(False):
        if symbol.isExternal() or symbol.getSource() == SourceType.DEFAULT:
            continue
        if symbol.getSymbolType() not in LABELLED_TYPES:
            continue
        address = symbol.getAddress()
        name = symbol.getName()
        # A symbol that is not primary has another name that Ghidra shows instead. When
        # Ghidra's demangler has read a name, the readable one is primary and the mangled
        # one has been kept behind it; this is how that is recognised.
        shown = None if symbol.isPrimary() else table.getPrimarySymbol(address).getName()
        symbols[address.getOffset(), name] = symbol
        entries.append(demangle_labels.Entry(address.getOffset(), name, shown))
    return entries, symbols


def ticks(items, what, monitor):
    """`items`, moving the progress bar as it goes. Raises `Cancelled` if the monitor is cancelled."""
    monitor.initialize(len(items))
    monitor.setMessage(what)
    for index, item in enumerate(items):
        if index % STRIDE == 0:
            monitor.setProgress(index)
            if monitor.isCancelled():
                raise Cancelled
        yield item


def relabel(table, symbol, label):
    """Rename `symbol` to the label, and keep the name it had as a second label."""
    name = SymbolUtilities.replaceInvalidChars(label.short if FORM == "short" else label.full, True)
    address = symbol.getAddress()
    try:
        symbol.setName(name, SourceType.USER_DEFINED)
    except Exception:
        # Most likely a name another symbol in the namespace has: overloads share a short
        # name. Anything else fails again here, and is reported.
        symbol.setName(f"{name}_{address}", SourceType.USER_DEFINED)
    if KEEP_ORIGINAL:
        try:
            table.createLabel(address, label.name, SourceType.IMPORTED)
        except Exception as error:
            print(f"demangle: renamed {address} but could not keep {label.name}: {error}")


def main(program, monitor):
    if FORM not in ("short", "full"):
        raise ValueError(f'FORM is "short" or "full", not {FORM!r}')
    # Only the names Ghidra left mangled: see `collect` for how one it has read is told.
    options = demangle_labels.Options(languages=LANGUAGES, only_left_mangled=True, style=STYLE, max_length=MAX_LENGTH)
    table = program.getSymbolTable()
    entries = labels = ()
    changed = failed = 0

    try:
        entries, symbols = collect(program)
        # Planned in full before anything is renamed: the table is not being edited while
        # it is read, and a cancel during the read leaves the program as it was.
        labels = list(demangle_labels.plan_labels(ticks(entries, "Reading symbols", monitor), options))
        for label in ticks(labels, "Labelling symbols", monitor):
            try:
                if not DRY_RUN:
                    relabel(table, symbols[label.address, label.name], label)
            except Exception as error:
                failed += 1
                print(f"demangle: {label.name}: {error}")
            else:
                changed += 1
                if changed <= REPORT_LIMIT:
                    # The offset is a signed Java long; the top half of a 64-bit space is negative.
                    print(f"demangle: {label.address & 0xFFFFFFFFFFFFFFFF:#x}  {label.name}  ->  {label.full}")
    except Cancelled:
        print("demangle: cancelled")

    print(
        f"demangle: {len(entries)} symbols read, {len(labels)} readable, "
        f"{changed} {'would change' if DRY_RUN else 'changed'}, {failed} failed"
    )
    if changed > REPORT_LIMIT:
        print(f"demangle: {changed - REPORT_LIMIT} more not listed; REPORT_LIMIT is {REPORT_LIMIT}")


if __name__ == "__main__":
    # `currentProgram` and `monitor` are what Ghidra puts in a script's namespace.
    main(currentProgram, monitor)  # noqa: F821  # ty: ignore[unresolved-reference]
