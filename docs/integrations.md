# Disassembler integrations

Three scripts label symbols in IDA Pro, Binary Ninja and Ghidra with this library. Each
one reads the symbols the tool holds, asks `demangle` which it can read, and writes the
readable name back as a comment or a name.

By default a script only touches the symbols the tool's own demangler left alone,
because that's where this library adds something. How much that is depends on the tool
and its version. In the [runs below](#what-they-were-run-on), IDA 9.5 read C++, MSVC, D,
Delphi and nearly all Rust v0 names itself, and Binary Ninja 6.0 read C++, MSVC, Swift
and Rust v0 on ELF. Ghidra's change history lists GNU, Microsoft and Rust demanglers.
What's left for the scripts is usually some of Swift, D, Nim, Free Pascal, Delphi, Ada,
JNI and GCC 2 names.

!!! tip "Try it on a copy first"
    Use a copy of the database or project, or set `DRY_RUN = True`, which prints what a
    script would change and changes nothing. Each script has only been run in the
    versions [listed below](#what-they-were-run-on). If a call isn't what your version
    has, the script names it (a traceback in IDA and Ghidra, a line in Binary Ninja's
    log): check it against [the calls each script makes](#the-calls-each-script-makes)
    and [report it](https://github.com/r0ny123/demangle/issues).

## What is there

The four files are in
[`integrations/`](https://github.com/r0ny123/demangle/tree/main/integrations) in the
repository and in the source distribution. `pip install demangle` doesn't install them.

| File | For | Does |
|---|---|---|
| [`demangle_labels.py`](https://github.com/r0ny123/demangle/blob/main/integrations/demangle_labels.py) | all three | Decides which names get a label, and what it says. Imports no host module. |
| [`demangle_ida.py`](https://github.com/r0ny123/demangle/blob/main/integrations/demangle_ida.py) | IDA Pro 9.x | A script for File > Script file. Writes repeatable comments, or renames. |
| [`demangle_binja.py`](https://github.com/r0ny123/demangle/blob/main/integrations/demangle_binja.py) | Binary Ninja | A plugin command, run in the background. Defines user symbols with a short, a full and a raw name. |
| [`demangle_ghidra.py`](https://github.com/r0ny123/demangle/blob/main/integrations/demangle_ghidra.py) | Ghidra 11.3 or later | A PyGhidra script. Renames the symbol and keeps the mangled name as a second label. |

Copy the host's file and `demangle_labels.py` into the same folder.

## Install

**The package.** `demangle` needs Python 3.11 or later in the Python the tool runs,
which isn't always the one on your path, and a tool's bundled Python may be older.

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
| `LEADING_UNDERSCORE` | all | Read each name without the one leading underscore a Mach-O symbol carries. `None` (default): on for a Mach-O file, off otherwise. |
| `MODE` | IDA | `"comment"` (default) or `"name"`. |
| `FORM` | all | `"short"` (default) or `"full"`: which spelling a name is made from. |
| `OVERWRITE_COMMENTS` | IDA | Replace a repeatable comment that is already there. Off by default. |
| `KEEP_ORIGINAL` | Ghidra | Keep the mangled name as a second label. On by default. |
| `DRY_RUN` | all | Report what would change; change nothing. |
| `REPORT_LIMIT` | all | How many changes to list; the count at the end is always the whole of them. Default 50. |

`LANGUAGES` is an
[allow-list](labelling-a-symbol-table.md#say-which-schemes-the-table-can-hold):
detection as usual, among those schemes only. If you know where the binary came from,
say so. On a Mach-O image leave Objective-C out, since all three tools label its
metadata themselves.

A Mach-O symbol is the compiler's name with one underscore in front, and all three
tools keep it: Nim's `area__mymod_u10` is `_area__mymod_u10` there. Read as it stands
that comes out as `mymod._area`, a JNI `_Java_...` name isn't read at all, and Free
Pascal, Ada and GCC 2 names go wrong the same way. So on a Mach-O file the scripts read
each name without that underscore. C++, Swift, Rust, D
and Objective-C deal with the underscore themselves and are read as they stand, and a
name that only reads with the underscore keeps that reading. `LEADING_UNDERSCORE`
forces this on or off whatever the file is.

`short` is the name without its parameters, `std::vector<int>::push_back`, and `full`
is the whole spelling. For a symbol that's a phrase, like `vtable for Foo`, both are
the phrase. A suffix the symbol table added, `.cold` or `@@GLIBCXX_3.4`, stays on
`short`, because it's what tells two copies of a function apart.

## Cancelling and undo

A script reads every symbol and decides every label before it changes anything, so
cancelling while it reads leaves the database as it was. Once it's writing, IDA and
Ghidra check for a cancel every 500 labels and Binary Ninja before each one. What was
written by then stays unless you undo it, and it can be more than the progress counter
showed: in IDA 9.4 a Cancel clicked at 9,969 left 11,303 labels.

- IDA: File > Script file makes one undo point for the run, cancelled or not. The Edit
  menu calls it "Undo Script file...", and in name mode it gives the mangled names
  back. A command typed in the console between the run and the Undo is a step of its
  own, so Undo takes that back first, and it ends the Redo of the run. Run any other way
  (`-S` on the command line, idalib) there's no undo point.
- Binary Ninja and Ghidra: the run is one undo step, cancelled or not. Ghidra names the
  step after PyGhidra's script class, not the script. It also asks you to confirm a
  Cancel, and the script keeps writing until you answer.

Redo puts a finished run back in all three.

## IDA Pro

The default writes the readable name as a **repeatable comment** at each address IDA's
demangler read nothing from, and changes no name. A repeatable comment is shown at every
reference to the address, so a call site shows what it calls.

`MODE = "name"` renames instead. IDA does not keep the old name, so the mangled form is
gone and IDA's own demangler can no longer read the symbol. Both modes skip names in the
segment IDA keeps undefined symbols in; name mode also skips the addresses in the import
table, which a rename would detach from it. IDA replaces the characters it
does not accept in a name with `_`, and gives a name that is already taken a number.

`ONLY_LEFT_MANGLED = False` is for names IDA reads wrongly. Its C++ reading of a legacy
Rust symbol keeps the hash as a function name.

## Binary Ninja

Each symbol gets a user symbol with the label as its short name and full name, and the
raw name unchanged, which is how Binary Ninja holds a symbol its own demangler read. The
command runs as a background task with a progress line and a Cancel button. Only
functions and data are touched; imports and externals are not.

A symbol counts as left mangled when its full name is still its raw name, so a second run
finds nothing to do. Where an earlier run defined a user symbol beside Binary Ninja's own,
the user symbol is the one read and replaced. Defining a user symbol replaces the one at
its address, so where several names share an address (aliases) only one of them gets the
label.

An address that already holds more than one user symbol is left alone, and the summary
line counts it. Binary Ninja's DWARF import makes such an address: it defines a user
symbol for each function the linker discarded, all at the image base beside
`__elf_header` (1,468 of them in a small Rust program built with the standard library's
debug information). Defining a symbol there drops one of the others, and Undo does not
bring it back.

## Ghidra

Each symbol is renamed with `Symbol.setName`, and the mangled name is added back as a
second label, so it stays searchable. Ghidra's own demangler leaves a symbol it has read
the same way, with the readable name primary and the mangled one behind it. That is how
the script tells: a mangled name that is not the primary one at its address has been
read, and is skipped. For that reason there is no `ONLY_LEFT_MANGLED` here.

Overloads share a short name, so a name another symbol has gets the address after it.
Ghidra replaces the characters it does not accept in a name (whitespace, control and
invisible characters) with `_`, as `SymbolUtilities.replaceInvalidChars` does.

## What they were run on

| Script | Versions | How |
|---|---|---|
| IDA | 9.4 | idalib, and the window with File > Script file |
| IDA | 9.5 | idalib, `idat -A -S`, and the window |
| Binary Ninja | 6.0 | Headless through the command the plugin registers, and from the Plugins menu |
| Ghidra | 12.1.4 and 12.2 | Headless through PyGhidra |
| Ghidra | 12.1.2 | The CodeBrowser's Script Manager |

Every run was on macOS or Linux, none on Windows. Ghidra 12.2 was built from its
release tag, and Ghidra's window steps were driven from a thread inside Ghidra, not by
mouse.

In each tool a second run changed nothing, and from its window one Undo took back a
full run or a cancelled one. A dry run and a cancel while reading changed nothing in IDA
and Binary Ninja. The inputs were small ELF and Mach-O programs built for the purpose,
carrying C++, MSVC, Swift, Rust, D, Nim, Free Pascal, Delphi, Ada, JNI and GCC 2 names,
plus a few big ones. The repository's own tests run each script against stand-ins for
its host instead, written from the tools' documentation. Some numbers from the real
runs:

- **A g++ program with the other schemes' names** (ELF x86-64). IDA 9.5 read the C++,
  D, MSVC, Rust v0 and Delphi names itself and the script labelled the 5 it left: Swift
  twice, Nim, Free Pascal and JNI. Binary Ninja 6.0 read the C++, Swift, MSVC and Rust
  v0 names and the script labelled 6: D twice, Nim, Free Pascal, JNI and Delphi. Both
  tools kept the hash in their reading of the legacy Rust name.
- **IDA 9.5, a rustc 1.97 program** (1,571 names). IDA left 5 Rust v0 names unread and
  the script labelled those.
- **IDA 9.5, `libLLVM.so` from LLVM 18** (103,772 names, auto-analysis off). IDA left
  800 C++ names unread, such as templates with `enable_if` in the return type, and the
  script labelled them in under a second. With `ONLY_LEFT_MANGLED = False` it set
  33,511 comments in about 5 s.
- **IDA 9.4 window, QtGui** (36,994 names). 17,004 comments in about 13 s. Cancelled
  partway, 11,303 labels had been written and one Undo removed them all.
- **Binary Ninja 6.0, `libLLVM.so`** (analysis off). Binary Ninja had read every name,
  so nothing changed. With `ONLY_LEFT_MANGLED = False` it relabelled 41,485 symbols in
  about 6.5 s.
- **Ghidra 12.1.4, Rust programs**. With auto-analysis on, Ghidra's own Rust demangler
  had read the Rust symbols and the script changed none of 2,104. With analysis off, on
  a Rust v0 program, it renamed 577 of 988.
- **Ghidra 12.1.2 window** (126,896 symbols). Cancel at 20,000 written stopped the run
  at 26,000, once its confirmation was answered, and one Undo restored the symbols.
- **Go programs**. Nothing to label in IDA or Ghidra: the names are already readable.
- **A Mach-O arm64 dylib** with Nim, JNI, Free Pascal, Ada and GCC 2 names. All three
  scripts recognised the format (IDA's `f_MACHO`, Binary Ninja's `Mach-O` view type,
  Ghidra's `Mac OS X Mach-O`) and read those names without the underscore.

`tests/test_integrations_ghidra_live.py` repeats a C++ case against your own Ghidra. It
is skipped unless asked for; [Contributing](CONTRIBUTING.md#getting-set-up) has the
steps.

## What they do not do

They set names and comments. They don't apply a function signature, parameter types, a
class or a namespace, which a host's own demangler does for the schemes it reads. If
you need those, [`signature()`](reference/api.md) has the parts of a name for a script
of your own.

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
