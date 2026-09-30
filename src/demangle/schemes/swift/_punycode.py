"""Punycode, as Swift uses it.

Swift mangles an identifier containing anything outside `[$_A-Za-z0-9]` by punycoding it
(RFC 3492) and marking the result with a leading `00`. Two deviations from the RFC matter,
and both are in the compiler's `Punycode.cpp`:

* the delimiter is `_` rather than `-`, and the digit alphabet is `a`-`z` then `A`-`J`,
  because a mangled name may only use characters an assembler accepts;
* scalars in `0xD800`-`0xD87F` -- the surrogate range, which real text cannot contain --
  are used to carry ASCII punctuation, and are decoded by subtracting `0xD800`. Without
  that, an operator like `+` would decode as an unassigned code point.
"""

__all__ = ["decode"]

_BASE = 36
_TMIN = 1
_TMAX = 26
_SKEW = 38
_DAMP = 700
_INITIAL_BIAS = 72
_INITIAL_N = 128
_DELIMITER = "_"

#: `a`-`z` are 0-25, `A`-`J` are 26-35. Anything else is not a digit.
_DIGITS = {chr(ord("a") + n): n for n in range(26)}
_DIGITS.update({chr(ord("A") + n): n + 26 for n in range(10)})


class PunycodeError(ValueError):
    """This is not a well-formed punycoded identifier."""


def _adapt(delta, points, first):
    delta = delta // _DAMP if first else delta // 2
    delta += delta // points
    k = 0
    while delta > ((_BASE - _TMIN) * _TMAX) // 2:
        delta //= _BASE - _TMIN
        k += _BASE
    return k + ((_BASE - _TMIN + 1) * delta) // (delta + _SKEW)


#: Largest code point, and the ceiling the running value is tested against: past it
#: nothing can become a character.
_MAX_SCALAR = 0x10FFFF
_MAX_INSERTION = _MAX_SCALAR * (_MAX_SCALAR + 1)


def _scalars(text):
    """RFC 3492 section 6.2, over Swift's alphabet."""
    n = _INITIAL_N
    i = 0
    bias = _INITIAL_BIAS
    out = []

    last = text.rfind(_DELIMITER)
    if last >= 0:
        for char in text[:last]:
            if ord(char) > 0x7F:
                raise PunycodeError("non-basic code point before the delimiter")
            out.append(ord(char))
        text = text[last + 1 :]

    # An index: re-slicing per digit is quadratic in an attacker-chosen length.
    at = 0
    length = len(text)
    while at < length:
        old = i
        weight = 1
        k = _BASE
        while True:
            if at >= length:
                raise PunycodeError("truncated punycode")
            digit = _DIGITS.get(text[at], -1)
            at += 1
            if digit < 0:
                raise PunycodeError("not a punycode digit")
            i += digit * weight
            if i > _MAX_INSERTION:
                # RFC 3492 tests overflow against the machine word; Python has none, so
                # the test is against the largest possible code point.
                raise PunycodeError("punycode value out of range")
            threshold = _TMIN if k <= bias else _TMAX if k >= bias + _TMAX else k - bias
            if digit < threshold:
                break
            weight *= _BASE - threshold
            k += _BASE
        bias = _adapt(i - old, len(out) + 1, old == 0)
        n += i // (len(out) + 1)
        i %= len(out) + 1
        if n < 0x80 or n > _MAX_SCALAR:
            raise PunycodeError("a basic code point cannot be inserted")
        out.insert(i, n)
        i += 1
    return out


def decode(text):
    """Decode a punycoded identifier body, or raise `PunycodeError`."""
    decoded = []
    for scalar in _scalars(text):
        if 0xD800 <= scalar < 0xD880:
            # Not a surrogate: the mangler parks ASCII punctuation here so an assembler
            # accepts it.
            scalar -= 0xD800
        elif not (scalar < 0xD800 or 0xE000 <= scalar <= 0x10FFFF):
            raise PunycodeError("not a Unicode scalar")
        decoded.append(chr(scalar))
    return "".join(decoded)
