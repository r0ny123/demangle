"""Label the symbols Binary Ninja leaves mangled, using the `demangle` library.

Put this file and `demangle_labels.py` in Binary Ninja's user plugins folder and install
the `demangle` package for the Python that Binary Ninja runs; the page "Disassembler
integrations" in the demangle documentation has the steps. Restart Binary Ninja, then
choose Plugins > Demangle > Label mangled symbols. It runs in the background, with a
progress line and a Cancel button, and the result is one undoable change.

Each symbol read keeps its raw name and gains a full name and a short name, as the ones
Binary Ninja's own demanglers read have. Change the settings below before the plugin
loads.

Run headless in Binary Ninja 6.0; the documentation page has the results. Not yet run
from the Plugins menu. If a call here is not what your version has, the Log window names
it.
"""

import os
import sys

from binaryninja import BackgroundTaskThread, PluginCommand, Symbol, SymbolType, log_error, log_info

# ---------------------------------------------------------------------------------------
# Settings

#: Which spelling is the symbol's short name, the one the views show: "short" for
#: `std::vector<int>::push_back`, "full" to include the parameters. The full name is the
#: whole spelling either way.
FORM = "short"

#: The schemes allowed to read a name. None lets all of them try; a tuple such as
#: ("swift", "go", "rust") allows only those. Leave Objective-C out for a Mach-O image,
#: whose metadata Binary Ninja labels itself.
LANGUAGES = None

#: True labels only the symbols whose full name is still their raw name. Set it to False
#: to also relabel the ones Binary Ninja's demanglers read.
ONLY_LEFT_MANGLED = True

#: "llvm" or "gnu", the spelling of types and templates; None is the default, "llvm".
STYLE = None

#: Longest label in characters; longer ones are cut.
MAX_LENGTH = 1024

#: Log what would change and change nothing.
DRY_RUN = False

#: How many changes to log. The count at the end is always the whole of them.
REPORT_LIMIT = 50

# ---------------------------------------------------------------------------------------

#: Imports and externals are other binaries' symbols, defined by the loader; only the
#: binary's own functions and data are relabelled.
LABELLED_TYPES = (SymbolType.FunctionSymbol, SymbolType.DataSymbol)

#: The progress line is updated after this many symbols.
STRIDE = 500

# A plugin is not always imported with its own folder on `sys.path`.
_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
if _HERE and _HERE not in sys.path:
    sys.path.append(_HERE)

import demangle_labels  # noqa: E402


def collect(bv):
    """The entries for `bv`'s symbols, and the symbols by `(address, raw name)`."""
    by_address = {}
    for symbol in bv.get_symbols():
        if symbol.type in LABELLED_TYPES:
            by_address.setdefault(symbol.address, []).append(symbol)
    # Binary Ninja holds one user symbol per address, so a second alias defined there
    # displaces the first and the two would trade places on every run. One symbol is read
    # per address: the user symbol, which stands beside the auto symbol it was defined
    # for and is the one the views show, else one Binary Ninja left mangled.
    symbols = {}
    for group in by_address.values():
        chosen = next((s for s in group if not s.auto), None)
        if chosen is None:
            chosen = next((s for s in group if s.full_name == s.raw_name), group[0])
        symbols[chosen.address, chosen.raw_name] = chosen
    # Binary Ninja's demanglers leave `full_name` as `raw_name` for a name they cannot
    # read, and the plugin's own labels differ from it for the same reason.
    entries = [
        demangle_labels.Entry(address, raw_name, symbol.full_name if symbol.full_name != raw_name else None)
        for (address, raw_name), symbol in symbols.items()
    ]
    return entries, symbols


def relabel(bv, symbol, label):
    """Replace `symbol` with one that has the label's names and the same everything else."""
    if not symbol.auto:
        # A user symbol from an earlier run, or from a person, is not superseded by a
        # second one at its address: the two compete, and the name breaks the tie.
        bv.undefine_user_symbol(symbol)
    bv.define_user_symbol(
        Symbol(
            symbol.type,
            symbol.address,
            label.short if FORM == "short" else label.full,
            full_name=label.full,
            raw_name=symbol.raw_name,
            binding=symbol.binding,
            namespace=symbol.namespace,
            ordinal=symbol.ordinal,
        )
    )


def label_symbols(bv, task):
    """Label `bv`'s symbols, reporting progress and honouring a cancel through `task`."""
    if FORM not in ("short", "full"):
        raise ValueError(f'FORM is "short" or "full", not {FORM!r}')
    options = demangle_labels.Options(
        languages=LANGUAGES, only_left_mangled=ONLY_LEFT_MANGLED, style=STYLE, max_length=MAX_LENGTH
    )
    entries, symbols = collect(bv)

    def counted(items, what):
        total = len(items)
        for index, item in enumerate(items):
            if index % STRIDE == 0:
                task.progress = f"Demangle: {what} {index} of {total}"
            if task.cancelled:
                return
            yield item

    labels = list(demangle_labels.plan_labels(counted(entries, "reading"), options))
    if task.cancelled:
        log_info("Demangle: cancelled; nothing changed")
        return

    changed = failed = 0
    if DRY_RUN:
        changed = len(labels)
        for label in labels[:REPORT_LIMIT]:
            log_info(f"Demangle: {label.address:#x}  {label.name}  ->  {label.full}")
    elif labels:
        # One undo step, and the symbol table is rebuilt once at the end rather than for
        # each symbol. An error inside the transaction would revert all of it, so each
        # symbol answers for itself.
        with bv.undoable_transaction(), bv.bulk_modify_symbols():
            for label in counted(labels, "labelling"):
                try:
                    relabel(bv, symbols[label.address, label.name], label)
                except Exception as error:
                    failed += 1
                    log_error(f"Demangle: {label.address:#x}  {label.name}: {error}")
                else:
                    changed += 1
                    if changed <= REPORT_LIMIT:
                        log_info(f"Demangle: {label.address:#x}  {label.name}  ->  {label.full}")
    log_info(
        f"Demangle: {len(entries)} symbols read, {len(labels)} readable, "
        f"{changed} {'would change' if DRY_RUN else 'changed'}, {failed} failed"
        + ("" if changed <= REPORT_LIMIT else f"; {changed - REPORT_LIMIT} more not listed")
        + ("; cancelled" if task.cancelled else "")
    )


class DemangleTask(BackgroundTaskThread):
    """`label_symbols` off the main thread, as the Binary Ninja documentation asks of a long task."""

    def __init__(self, bv):
        super().__init__("Demangle: reading symbols", True)
        self.bv = bv

    def run(self):
        try:
            label_symbols(self.bv, self)
        except Exception as error:
            log_error(f"Demangle: {error}")
        finally:
            self.finish()


PluginCommand.register(
    "Demangle\\Label mangled symbols",
    "Read the symbols Binary Ninja left mangled with the demangle library and label them",
    lambda bv: DemangleTask(bv).start(),
)
