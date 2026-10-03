#!/usr/bin/env python3
"""Determine empirically what the reference demangler puts in the substitution table.

The ABI's prose on which components are substitution candidates is genuinely ambiguous
in places -- notably whether a function template's name and its specialisation are
candidates, given that 5.1.10 excludes "function and operator names" but also lists
<unscoped-template-name> as a candidate.

Rather than guess, ask. Appending `S_`, `S0_`, `S1_` ... as extra parameters to a name
and demangling it makes the reference implementation print its own table back at us.
"""

import subprocess
import sys

REFERENCE = "llvm-cxxfilt"


def reference_demangle(name):
    result = subprocess.run([REFERENCE, name], capture_output=True, text=True)
    return result.stdout.strip()


def probe(prefix, suffix="", limit=8):
    """Print what each substitution index resolves to for a name under construction."""
    print(f"\n=== {prefix}...{suffix} ===")
    entries = []
    for index in range(limit):
        token = "S_" if index == 0 else f"S{index - 1}_"
        candidate = prefix + token + suffix
        output = reference_demangle(candidate)
        if output == candidate or not output:
            print(f"  {token:6} -> (out of range)")
            break
        entries.append((token, output))
        print(f"  {token:6} -> {output}")
    return entries


if __name__ == "__main__":
    # Does a function template's name become an entry? Its specialisation?
    probe("_ZSt4sortIPiEvT_")
    probe("_Z1fIiEvT_")
    # The specification's own worked example, as a control.
    probe("_ZN1N1TIiiE2mfE")
    # A class template used as a type: how many entries does St4lessIiE contribute?
    probe("_Z1fSt4lessIiE")
    probe("_Z1fSt3foo")
    probe("_ZN1A1B1CE")
    for name in sys.argv[1:]:
        probe(name)
