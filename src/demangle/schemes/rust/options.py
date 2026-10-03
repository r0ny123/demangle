"""Whether to spell the hash rustc writes into a symbol to keep it unique.

Both Rust manglings carry one and neither spells it by default, because it is noise to a
reader: `core::fmt::write` says what the function is and
`core::fmt::write::h05af221e174051e9` says the same thing with a machine's bookkeeping
after it. But it is the *only* thing telling two monomorphisations of one generic
function apart, so a caller symbolising a profile or diffing two builds needs it.

This is rustc-demangle's own choice, spelled as a formatting flag rather than an option:
`{}` on its `Demangle` prints the hash and `{:#}` suppresses it. `rustfilt` prints the
suppressed form, and so does this by default. What the flag reaches differs between the
two manglings, which is why it is one option and not two:

* legacy appends the path's trailing `17h<16 hex>` component -- `a::f::h05af221e174051e9`;
* v0 prints the crate's *disambiguator* wherever a crate root is spelled --
  `features[9f05e0465351d495]::apply_hrtb`, at every occurrence rather than the first.
"""

from dataclasses import dataclass

__all__ = ["DEFAULT_OPTIONS", "RustOptions"]


@dataclass(frozen=True, slots=True)
class RustOptions:
    """How much of a Rust name to spell."""

    keep_hash: bool = False
    """Spell the disambiguating hash rather than dropping it.

    Off by default, which is what `rustfilt` and every corpus here is scored against.
    """


DEFAULT_OPTIONS = RustOptions()
"""What both shipped styles use: the hash left out, as `rustfilt` prints it."""
