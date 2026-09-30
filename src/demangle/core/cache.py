"""Bounded memoisation.

Symbol tables repeat themselves relentlessly: one C++ binary can name
`std::allocator<char>` thousands of times, and every one costs the same parse. Caching
is worth more here than almost any micro-optimisation.

It also cannot be unbounded. A tool that walks a corpus of binaries would otherwise
accumulate an entry per distinct symbol ever seen, and there is no natural end to that.
"""

_MISSING = object()


class BoundedCache:
    """A dict of at most `max_size` entries, kept as two generations.

    New entries go into the young generation. When it holds half of `max_size`, it
    becomes the old one -- the previous old one is dropped whole -- and a new, empty
    young generation starts. A lookup tries the young generation, then the old; an entry
    found in the old one moves back to the young, so whatever is still in use survives
    every turnover while what went cold ages out within two.

    A hit in the young generation costs one lookup, and a hit in the old one a second
    lookup and a move. True LRU would cost a linked-list update on every hit, the
    operation being optimised, and served a warm name 15% slower for a few points of hit
    rate.

    `hits` and `misses` count lookups: a hit in either generation is a hit. Neither is
    guarded by a lock, so under free threading concurrent lookups can lose an increment
    and the figures are approximate; the entries themselves are never corrupted, since
    every change is one dictionary operation or one attribute store, and a race between
    two turnovers at worst drops a generation early.
    """

    __slots__ = ("_generation", "_old", "_young", "hits", "max_size", "misses")

    def __init__(self, max_size=8192):
        self.max_size = max_size
        #: Entries per generation; at least one, so a `max_size` under 2 still caches (two).
        self._generation = max(max_size // 2, 1)
        self._young = {}
        self._old = {}
        self.hits = 0
        self.misses = 0

    def get(self, key):
        value = self._young.get(key, _MISSING)
        if value is _MISSING:
            value = self._old.pop(key, _MISSING)
            if value is _MISSING:
                self.misses += 1
                return _MISSING
            self.put(key, value)
        self.hits += 1
        return value

    def put(self, key, value):
        young = self._young
        if len(young) >= self._generation:
            self._old = young
            self._young = young = {}
        young[key] = value
        return value

    def clear(self):
        self._young = {}
        self._old = {}
        self.hits = 0
        self.misses = 0

    @property
    def stats(self):
        total = self.hits + self.misses
        return {
            "size": len(self),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hits / total if total else 0.0,
        }

    def __len__(self):
        return len(self._young) + len(self._old)

    def __contains__(self, key):
        return key in self._young or key in self._old


MISSING = _MISSING
