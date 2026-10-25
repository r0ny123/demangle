# Disassembler integrations

Three scripts label symbols in IDA Pro, Binary Ninja and Ghidra with this library. Each
reads the symbols the tool holds, asks `demangle` which of them it can read, and writes
the readable name back as a comment or a name. By default each touches only the
symbols the tool's own demanglers left alone, which is where this library has something
to add: IDA's documentation lists Microsoft, Borland, Watcom, Visual Age and GNU C++
demanglers, and Ghidra's change history lists GNU, Microsoft and Rust. Swift, Go, D, Nim,
Free Pascal, Delphi, Ada, JNI and legacy Rust are not on either list.

!!! warning "Run in one version of each tool"
    Each script has been run in its host: the IDA script in IDA 9.4, headless through
    idalib and from IDA's window (File > Script file, the wait box's Cancel button, Edit >
    Undo and Redo); the Binary Ninja script in Binary Ninja 6.0, headless; and the Ghidra
    script in Ghidra 12.1.4 and 12.2, headless through PyGhidra. The results are under
    [Run in IDA](#run-in-ida), [Run in Binary Ninja](#run-in-binary-ninja) and
    [Run in Ghidra](#run-in-ghidra). What has not been tried is Binary Ninja's Plugins
    menu and Ghidra's Script Manager, which need the window. Other versions
    have not been tried either, so [the calls each script makes](#the-calls-each-script-makes)
    are listed to check against the version you have. The repository's own tests run each
    script against stand-ins for its host, written from the same documentation.

    Try a script first on a copy of the database or project, with `DRY_RUN = True`, which
    prints what would change and changes nothing. A call that is not what your version has
    fails with a traceback naming it; [report it](https://github.com/r0ny123/demangle/issues).

## What is there

All four files are in `integrations/` in the
[repository](https://github.com/r0ny123/demangle/tree/main/integrations) and in the
source distribution. The wheel on PyPI holds only the package, so `pip install demangle`
does not install them.

| File | For | Does |
|---|---|---|
| [`demangle_labels.py`](https://github.com/r0ny123/demangle/blob/main/integrations/demangle_labels.py) | all three | Decides which names get a label, and what it says. Imports no host module. |
| [`demangle_ida.py`](https://github.com/r0ny123/demangle/blob/main/integrations/demangle_ida.py) | IDA Pro 9.x | A script for File > Script file. Writes repeatable comments, or renames. |
| [`demangle_binja.py`](https://github.com/r0ny123/demangle/blob/main/integrations/demangle_binja.py) | Binary Ninja | A plugin command, run in the background. Defines user symbols with a short, a full and a raw name. |
| [`demangle_ghidra.py`](https://github.com/r0ny123/demangle/blob/main/integrations/demangle_ghidra.py) | Ghidra 11.3 or later | A PyGhidra script. Renames the symbol and keeps the mangled name as a second label. |

A script is the host's file and `demangle_labels.py` beside it: copy both into the same
folder.

## Install

**The package.** `demangle` needs Python 3.11 or later, in the Python the tool runs. That
is not always the one on your path, and a tool's bundled Python may be older.

- IDA runs the interpreter `idapyswitch` registers; install with that interpreter's pip.
  IDA 9.5's documentation describes a virtual environment, `~/.idapro/venv`, for this.
- Binary Ninja runs its bundled Python unless the `python.interpreter` setting names
  another. Its command palette has an *Install python3 module* action, or use the pip of
  the interpreter you set.
- Ghidra's PyGhidra mode runs the Python environment you start it from, the one with the
  `pyghidra` module; install there.

Check from the tool's own Python console: `import demangle; demangle.__version__`.

**The scripts.**

- IDA: choose `demangle_ida.py` in File > Script file... (Alt+F7).
- Binary Ninja: put both files in the user plugins folder (`~/.binaryninja/plugins` on
  Linux, `~/Library/Application Support/Binary Ninja/plugins` on macOS,
  `%APPDATA%\Binary Ninja\plugins` on Windows) and restart. The command is under
  Plugins > Demangle > Label mangled symbols.
- Ghidra: start with `support/pyghidraRun`, put both files in a script directory, and run
  `demangle_ghidra.py` from the Script Manager, under Symbol. Its `# @runtime PyGhidra`
  header selects the CPython runtime; Ghidra 11.3 and 11.4 need it, and from 12.0 a
  Python script without one runs under PyGhidra anyway.

## Settings

Each script has its settings as constants at the top. Edit the file; there is no dialog.

| Setting | In | Meaning |
|---|---|---|
| `LANGUAGES` | all | The schemes allowed to read a name, as a tuple: `("swift", "go")`. `None` lets all of them try. |
| `ONLY_LEFT_MANGLED` | IDA, Binary Ninja | `True`: only names the tool's own demangler did not read. `False`: every name the library reads. |
| `STYLE` | all | `"llvm"` or `"gnu"`, or `None` for the default. |
| `MAX_LENGTH` | all | Longest label in characters; longer ones end in `...`. |
| `MODE` | IDA | `"comment"` (default) or `"name"`. |
| `FORM` | all | `"short"` (default) or `"full"`: which spelling a name is made from. |
| `OVERWRITE_COMMENTS` | IDA | Replace a repeatable comment that is already there. Off by default. |
| `KEEP_ORIGINAL` | Ghidra | Keep the mangled name as a second label. On by default. |
| `DRY_RUN` | all | Report what would change; change nothing. |

`LANGUAGES` is an allow-list in the sense of
[Labelling a symbol table](labelling-a-symbol-table.md#say-which-schemes-the-table-can-hold):
detection as usual among those schemes, not a scheme forced on every name. A table whose
origin you know should say so. On a Mach-O image, leave Objective-C out, since all three
tools label its metadata themselves.

`short` is the name without its parameters, `std::vector<int>::push_back`, and `full` is
the whole spelling. Where a symbol is a phrase rather than a name -- `vtable for Foo` --
both are the phrase. A suffix the symbol table added, `.cold` or `@@GLIBCXX_3.4`, stays on
`short`, because it is what tells two copies of one function apart.

## Cancelling

Each script reads every symbol and decides every label before it changes anything, so a
cancel while it is reading leaves the database or program as it was. A cancel once it has
started writing is seen at the next check, every 500 labels, and stops it there: what it
wrote before then stays written, unless the tool's Undo takes it back, as below.

- IDA: File > Script file makes an undo point for the run, and one Edit > Undo took back
  all of it, a run that was cancelled included ([tried](#run-in-ida) in 9.4). Run any
  other way -- `-S` on the command line, idalib -- the script makes no undo point of its
  own, so count on the labels written before the cancel staying.
- Binary Ninja: the run is one undo step, cancelled or not, and Undo takes back all of it
  ([tried](#run-in-binary-ninja) in 6.0).
- Ghidra: the run is one transaction, cancelled or not, so Undo should take back all of
  it. That has not been tried.

To try a run without that risk, use `DRY_RUN = True` first, or a copy.

## IDA Pro

The default writes the readable name as a **repeatable comment** at each address IDA's
demangler read nothing from, and changes no name. A repeatable comment is shown at every
reference to the address, so a call site shows what it calls.

`MODE = "name"` renames instead. IDA does not keep the old name, so the mangled form is
gone and IDA's own demangler can no longer read the symbol; names in the import table and
in the segment IDA keeps undefined symbols in are skipped. IDA replaces the characters it
does not accept in a name with `_`, and gives a name that is already taken a number.

`ONLY_LEFT_MANGLED = False` is for names IDA reads wrongly. Its C++ reading of a legacy
Rust symbol keeps the hash as a function name.

### Run in IDA

The script has been run in IDA 9.4 through idalib, headless, on a Mach-O arm64 program
holding C++, Swift, Rust v0, legacy Rust, MSVC and D names.

- Comment mode: a dry run changed nothing; a run set 209 comments; a second run set none
  and kept the 209.
- Name mode: it renamed 209 addresses, and a second run found nothing left to read. Names
  came out with IDA's replacement characters and its numbered suffix for a name already
  taken, as described above.
- Of the 20 imports `enum_import_names` listed, the 4 with mangled names were in the
  segment IDA keeps undefined symbols in, and were skipped.
- A cancel while writing, with `user_cancelled` made to answer yes, left the labels written
  before it.
- On `crashpad_handler` for arm64, 4,460 symbols of which about 2,900 are C++: comment mode
  set 2,048 comments and a second run set none, keeping 2,217; name mode renamed 2,217
  addresses and a second run found nothing left to read. Each run took about 0.1 s.

The same script was then run from IDA 9.4's window with File > Script file, on a freshly
built program of the same kind (203 names to label) and on the arm64 slice of Binary
Ninja's QtGui framework (36,994 names, 17,497 of them readable, about 20,000 C++ symbols):

- Comment mode set 203 comments and printed the summary line in the Output window. The
  Edit menu then read "Undo Script file...", and one Undo removed all 203. Redo put them
  back.
- Name mode renamed 203 functions, and one Undo gave each its mangled name back, with
  IDA's own demangled view of it.
- A second run after a completed run changed nothing (0 changed, 203 kept).
- On QtGui, a run to the end set 17,004 comments in about 13 s, behind a wait box that
  read "Reading symbols" and then "Labelling symbols". With Cancel clicked at
  "Labelling symbols: 9969 of 17497", the Output window printed `demangle: cancelled` and
  the summary line, 11,303 labels were written, and one Undo removed all 11,303. The
  wait box's counter runs behind the labels written.
- A command typed in IDA's console between the run and the Undo is an undo step of its
  own: Undo then takes that back first, and a new step ends the Redo of the run.

## Binary Ninja

Each symbol gets a user symbol with the label as its short name and full name, and the
raw name unchanged, which is how Binary Ninja holds a symbol its own demangler read. The
command runs as a background task with a progress line and a Cancel button, and the whole
change is one undo step. Only functions and data are touched; imports and externals are
not.

A symbol counts as left mangled when its full name is still its raw name, so a second run
finds nothing to do. Where an earlier run defined a user symbol beside Binary Ninja's own,
the user symbol is the one read and replaced. Binary Ninja keeps one user symbol per
address, so where several names share an address (aliases) only one of them gets the
label.

### Run in Binary Ninja

The script has been run in Binary Ninja 6.0 Ultimate, headless, on the same Mach-O arm64
program as in IDA.

- `Symbol(...)` with a full name, a raw name, a binding, a namespace and an ordinal, inside
  `bulk_modify_symbols()`, defined every symbol with none failed, and the binding,
  namespace and ordinal came through unchanged.
- A dry run changed nothing, and nor did a cancel before the run or while it was reading.
- A cancel while writing left part of the change, and one `undo()` took all of it back. A
  whole run followed by `undo()` left the symbols exactly as they were, and `redo()` put
  the run back.
- A second run changes nothing. Before two fixes in the same change as this page, it
  relabelled the names Binary Ninja cannot read itself (D and Nim here) on every run,
  because the symbol an earlier run defined was not the one read; and with
  `ONLY_LEFT_MANGLED = False`, aliases sharing an address took turns holding the label.
- On `crashpad_handler` for arm64, 4,460 symbols of which about 2,900 are C++, a second run
  changed nothing in either setting of `ONLY_LEFT_MANGLED`, and four undos restored the
  original exactly.

## Ghidra

Each symbol is renamed with `Symbol.setName`, and the mangled name is added back as a
second label, so it stays searchable. Ghidra's own demangler leaves a symbol it has read
the same way, with the readable name primary and the mangled one behind it. That is how
the script tells: a mangled name that is not the primary one at its address has been
read, and is skipped. For that reason there is no `ONLY_LEFT_MANGLED` here.

Overloads share a short name, so a name another symbol has gets the address after it.
Ghidra replaces the characters it does not accept in a name -- spaces, other whitespace,
control and invisible characters -- with `_`, as `SymbolUtilities.replaceInvalidChars` does.
Ghidra runs a script's changes in one transaction, so Undo should take back a run whole.

### Run in Ghidra

The script has been run in headless Ghidra 12.1.4 through PyGhidra 3.1.0, on programs
built from Rust (v0 and legacy), C++ and Go, with the results below. The Rust v0 and C++
cases were repeated in Ghidra 12.2, built from its release tag, through PyGhidra 3.2.0,
with the same results. Other versions have not been tried.

- With auto-analysis on, Ghidra's own Rust demangler had already read the Rust symbols,
  leaving each mangled name as a second label, and the script changed none of 2104.
- With analysis off, on a Rust v0 program, it renamed 577 of 988 symbols and none failed.
  No primary symbol was left mangled, each mangled name was kept as a second `IMPORTED`
  label, and a second run changed nothing.
- On a g++ program with analysis off it renamed 4 of 46 symbols.
- On a Go program with `LANGUAGES = ("go",)`, in a dry run, it found nothing to label in
  16216 symbols: Ghidra's names for Go were already readable.

`tests/test_integrations_ghidra_live.py` repeats the C++ case against your own Ghidra. It
is skipped unless asked for; [Contributing](CONTRIBUTING.md#getting-set-up) has the
steps.

## What they do not do

They set names and comments. They apply no function signature, parameter type, class or
namespace, which is what a host's own demangler does for the schemes it reads; a name
this library reads becomes a label, not a declaration. For that, [`signature()`](reference/api.md)
has the parts of a name for a script of your own.

## Using the core from another tool

`demangle_labels.py` is a function over `(address, name)` pairs. Anything that can list
its symbols can use it:

```python
import demangle_labels

options = demangle_labels.Options(languages=("swift", "go"), max_length=200)
symbols = [(0x1000, "$s10Foundation4DataV5countSivg"), (0x2000, "main")]

for label in demangle_labels.plan_labels(symbols, options):
    print(hex(label.address), label.language, label.short, "|", label.full)
```

A pair may carry a third item, what the tool's own demangler made of the name, and
`only_left_mangled` (the default) then skips the names it read. `plan_labels` is lazy, so
a generator around the pairs gives a progress count and a place to stop. The bytes of a
string table are not accepted: decode them first, or use `demangle.demangleb`.

## The calls each script makes

For checking against the version you have.

| Script | Reads | Writes | Reports |
|---|---|---|---|
| IDA | `idautils.Names`, `ida_name.demangle_name`, `ida_nalt.get_import_module_qty`, `ida_nalt.enum_import_names`, `ida_segment.get_segment_info` and `segment_info_t` (`ida_segment.getseg` where the first is missing) | `ida_bytes.get_cmt` and `set_cmt` (repeatable); `ida_name.set_name` with `SN_NOCHECK`, `SN_NOWARN` and `SN_FORCE` | `ida_kernwin.show_wait_box`, `replace_wait_box`, `user_cancelled`, `hide_wait_box` |
| Binary Ninja | `BinaryView.get_symbols`; `Symbol.type`, `address`, `raw_name`, `full_name`, `auto`, `binding`, `namespace`, `ordinal` | `BinaryView.undefine_user_symbol`, `define_user_symbol`, inside `undoable_transaction` and `bulk_modify_symbols`; `Symbol(...)` | `BackgroundTaskThread` (`progress`, `cancelled`, `finish`); `PluginCommand.register`; `log_info`, `log_error` |
| Ghidra | `SymbolTable.getAllSymbols(False)`, `getPrimarySymbol`; `Symbol.isExternal`, `getSource`, `getSymbolType`, `isPrimary`, `getAddress`, `getName` | `Symbol.setName`; `SymbolTable.createLabel`; `SymbolUtilities.replaceInvalidChars` | `TaskMonitor.initialize`, `setMessage`, `setProgress`, `isCancelled` |
