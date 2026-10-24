# Disassembler integrations

Three scripts label symbols in IDA Pro, Binary Ninja and Ghidra with this library. Each
reads the symbols the tool holds, asks `demangle` which of them it can read, and writes
the readable name back as a comment or a name. By default each touches only the
symbols the tool's own demanglers left alone, which is where this library has something
to add: IDA's documentation lists Microsoft, Borland, Watcom, Visual Age and GNU C++
demanglers, and Ghidra's change history lists GNU, Microsoft and Rust. Swift, Go, D, Nim,
Free Pascal, Delphi, Ada, JNI and legacy Rust are not on either list.

!!! warning "Not tested inside the hosts"
    These scripts have never been run in IDA Pro, Binary Ninja or Ghidra. They are
    written against each tool's published API documentation, and
    [the calls they make](#the-calls-each-script-makes) are listed so they can be checked
    against the version you have. What is tested is the part that needs no host: the core
    that decides what a label is, and each script's logic against stand-ins for its host
    that were written from the same documentation. That shows a script does what it means
    to when the host behaves as documented, and nothing about whether it does.

    Try one first on a copy of the database or project, with `DRY_RUN = True`, which
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

## Binary Ninja

Each symbol gets a user symbol with the label as its short name and full name, and the
raw name unchanged, which is how Binary Ninja holds a symbol its own demangler read. The
command runs as a background task with a progress line and a Cancel button, and the whole
change is one undo step. Only functions and data are touched; imports and externals are
not.

A symbol counts as left mangled when its full name is still its raw name, so a second run
finds nothing to do.

## Ghidra

Each symbol is renamed with `Symbol.setName`, and the mangled name is added back as a
second label, so it stays searchable. Ghidra's own demangler leaves a symbol it has read
the same way, with the readable name primary and the mangled one behind it. That is how
the script tells: a mangled name that is not the primary one at its address has been
read, and is skipped. For that reason there is no `ONLY_LEFT_MANGLED` here.

Overloads share a short name, so a name another symbol has gets the address after it.
Ghidra replaces the characters it does not accept in a name, spaces among them, with `_`.
Ghidra runs a script's changes in one transaction, so Undo should take back a run whole.

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
| IDA | `idautils.Names`, `ida_name.demangle_name`, `ida_nalt.get_import_module_qty`, `ida_nalt.enum_import_names`, `ida_segment.getseg` | `ida_bytes.get_cmt` and `set_cmt` (repeatable); `ida_name.set_name` with `SN_NOCHECK`, `SN_NOWARN` and `SN_FORCE` | `ida_kernwin.show_wait_box`, `replace_wait_box`, `user_cancelled`, `hide_wait_box` |
| Binary Ninja | `BinaryView.get_symbols`; `Symbol.type`, `address`, `raw_name`, `full_name`, `auto`, `binding`, `namespace`, `ordinal` | `BinaryView.undefine_user_symbol`, `define_user_symbol`, inside `undoable_transaction` and `bulk_modify_symbols`; `Symbol(...)` | `BackgroundTaskThread` (`progress`, `cancelled`, `finish`); `PluginCommand.register`; `log_info`, `log_error` |
| Ghidra | `SymbolTable.getAllSymbols(False)`, `getPrimarySymbol`; `Symbol.isExternal`, `getSource`, `getSymbolType`, `isPrimary`, `getAddress`, `getName` | `Symbol.setName`; `SymbolTable.createLabel`; `SymbolUtilities.replaceInvalidChars` | `TaskMonitor.initialize`, `setMessage`, `setProgress`, `isCancelled` |
