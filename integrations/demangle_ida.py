"""Label the names IDA leaves mangled, using the `demangle` library.

Run it with File > Script file... (Alt+F7) in IDA 9.x. It needs `demangle_labels.py` in
the same folder and the `demangle` package installed for the Python that IDA runs; the
page "Disassembler integrations" in the demangle documentation has the steps.

Change the settings below first. The default writes a repeatable comment with the
readable name at each address IDA left mangled, which changes no name and so cannot
break anything IDA does with the one it has.

Run headless in IDA 9.4 through idalib, in both modes; the documentation page has the
results. Not yet run from IDA's window, where Cancel and Undo are. If a call here is not
what your IDA has, the traceback names it.
"""

import os
import sys

import ida_bytes
import ida_kernwin
import ida_nalt
import ida_name
import ida_segment
import idautils

# ---------------------------------------------------------------------------------------
# Settings

#: "comment" writes a repeatable comment and changes no name. "name" renames the address:
#: IDA keeps no record of the name it had, so the mangled form is gone and IDA's own
#: demangler can no longer read the symbol. A rename skips imports and external symbols.
MODE = "comment"

#: With MODE = "name": "short" names the symbol `std::vector<int>::push_back`, "full"
#: adds the parameters. IDA replaces what it does not accept in a name with `_`, and
#: gives a name that is already taken a numeric suffix.
FORM = "short"

#: The schemes allowed to read a name. None lets all of them try; a tuple such as
#: ("swift", "go", "rust") allows only those. Leave Objective-C out for a Mach-O image:
#: IDA labels its metadata itself.
LANGUAGES = None

#: True labels only the names IDA's demangler does not read. Set it to False to also
#: label the ones it reads wrongly: IDA's C++ reading of a legacy Rust symbol keeps the
#: hash as a function name.
ONLY_LEFT_MANGLED = True

#: "llvm" or "gnu", the spelling of types and templates; None is the default, "llvm".
STYLE = None

#: Longest label in characters; longer ones are cut. IDA limits the length of a name.
MAX_LENGTH = 255

#: With MODE = "comment": replace a repeatable comment that is already there. Off, an
#: address with a different comment is left as it is.
OVERWRITE_COMMENTS = False

#: Print what would change and change nothing.
DRY_RUN = False

#: How many changes to print. The count at the end is always the whole of them.
REPORT_LIMIT = 50

# ---------------------------------------------------------------------------------------

#: IDA is checked for a request to cancel after this many symbols.
STRIDE = 500

# A script run from a file does not always have its own folder on `sys.path`.
_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
if _HERE and _HERE not in sys.path:
    sys.path.append(_HERE)

import demangle_labels  # noqa: E402


class Cancelled(Exception):
    """The person pressed Cancel in the wait box."""


def host_reading(name):
    """What IDA's demangler makes of `name`, or None where it makes nothing."""
    # A mask of 0 turns none of the parts of the output off; only whether IDA reads the
    # name matters here. `idc.demangle_name` makes the same call.
    return ida_name.demangle_name(name, 0, ida_name.DQT_FULL) or None


def import_addresses():
    """The addresses of the import table's entries, which a rename would detach from it."""
    found = set()

    def note(address, _name, _ordinal):
        found.add(address)
        return True  # a falsy result stops the enumeration

    for index in range(ida_nalt.get_import_module_qty()):
        ida_nalt.enum_import_names(index, note)
    return found


def is_external(address):
    """Whether `address` is in the segment IDA keeps undefined (imported) symbols in."""
    segment = ida_segment.getseg(address)
    return segment is not None and segment.type == ida_segment.SEG_XTRN


def collect(skip):
    """Every named address except those in `skip` and in the external segment, as entries."""
    return [
        demangle_labels.Entry(address, name, host_reading(name))
        for address, name in idautils.Names()
        if address not in skip and not is_external(address)
    ]


def ticks(items, what):
    """`items`, updating the wait box as it goes. Raises `Cancelled` if the person cancels."""
    total = len(items)
    for index, item in enumerate(items):
        if index % STRIDE == 0:
            ida_kernwin.replace_wait_box(f"{what}: {index} of {total}")
            if ida_kernwin.user_cancelled():
                raise Cancelled
        yield item


def apply_comment(label):
    """Write `label.full` as the repeatable comment. Returns "set", "kept" or "failed"."""
    current = ida_bytes.get_cmt(label.address, True)
    if current == label.full:
        return "kept"
    if current and not OVERWRITE_COMMENTS:
        return "kept"
    if DRY_RUN:
        return "set"
    return "set" if ida_bytes.set_cmt(label.address, label.full, True) else "failed"


def apply_name(label):
    """Rename the address to `label.short` or `label.full`. Returns "set" or "failed"."""
    if DRY_RUN:
        return "set"
    flags = ida_name.SN_NOCHECK | ida_name.SN_NOWARN | ida_name.SN_FORCE
    return (
        "set" if ida_name.set_name(label.address, label.short if FORM == "short" else label.full, flags) else "failed"
    )


def run():
    if MODE not in ("comment", "name"):
        raise ValueError(f'MODE is "comment" or "name", not {MODE!r}')
    if FORM not in ("short", "full"):
        raise ValueError(f'FORM is "short" or "full", not {FORM!r}')
    options = demangle_labels.Options(
        languages=LANGUAGES, only_left_mangled=ONLY_LEFT_MANGLED, style=STYLE, max_length=MAX_LENGTH
    )
    apply = apply_comment if MODE == "comment" else apply_name
    counts = {"set": 0, "kept": 0, "failed": 0}
    entries = labels = ()
    shown = 0

    ida_kernwin.show_wait_box("Reading symbols")
    try:
        entries = collect(import_addresses() if MODE == "name" else set())
        # Planned in full before anything is written: the table is not being edited
        # while it is read, and a cancel during the read leaves the database as it was.
        labels = list(demangle_labels.plan_labels(ticks(entries, "Reading symbols"), options))
        for label in ticks(labels, "Labelling symbols"):
            result = apply(label)
            counts[result] += 1
            if result == "set" and shown < REPORT_LIMIT:
                shown += 1
                print(f"demangle: {label.address:#x}  {label.name}  ->  {label.full}")
    except Cancelled:
        print("demangle: cancelled")
    finally:
        ida_kernwin.hide_wait_box()

    changed = counts["set"]
    print(
        f"demangle: {len(entries)} names read, {len(labels)} readable, "
        f"{changed} {'would change' if DRY_RUN else 'changed'}, {counts['kept']} kept, {counts['failed']} failed"
    )
    if changed > shown:
        print(f"demangle: {changed - shown} more not listed; REPORT_LIMIT is {REPORT_LIMIT}")


if __name__ == "__main__":
    run()
