"""Bounded memoisation.

Symbol tables repeat themselves relentlessly: one C++ binary can name
`std::allocator<char>` thousands of times, and every one costs the same parse. Caching
is worth more here than almost any micro-optimisation.

It also cannot be unbounded. A tool that walks a corpus of binaries would otherwise
accumulate an entry per distinct symbol ever seen, and there is no natural end to that.
"""

import threading

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

    Adding an entry -- a miss, or a promotion from the old generation -- takes a lock,
    so under free threading the turnover and the weight are exact and the bound holds.
    A hit in the young generation takes none, being the path this exists to make cheap.

    `epoch` counts `clear()` calls. A caller that computes a value from state whose
    change clears the cache reads it before computing and passes it to `put`, which then
    drops the value if a clear came in between, rather than keep an answer from before.

    `hits` and `misses` count lookups: a hit in either generation is a hit. They are
    not guarded, so under free threading concurrent lookups can lose an increment and
    the figures are approximate.
    """

    __slots__ = (
        "_budget",
        "_generation",
        "_lock",
        "_old",
        "_weigh",
        "_young",
        "_young_weight",
        "epoch",
        "hits",
        "max_size",
        "max_weight",
        "misses",
    )

    def __init__(self, max_size=8192, max_weight=None, weigh=None):
        """`weigh(key, value)`, with `max_weight`, also bounds what the entries hold."""
        self.max_size = max_size
        self.max_weight = max_weight
        self._weigh = weigh
        #: Entries per generation; at least one, so a `max_size` under 2 still caches (two).
        self._generation = max(max_size // 2, 1)
        #: Weight per generation: a generation turns over once it reaches this, so the
        #: whole holds at most `max_weight` and two entries.
        self._budget = float("inf") if max_weight is None or weigh is None else max(max_weight // 2, 1)
        self._young = {}
        self._old = {}
        self._young_weight = 0
        self._lock = threading.Lock()
        self.epoch = 0
        self.hits = 0
        self.misses = 0

    def get(self, key):
        value = self._young.get(key, _MISSING)
        if value is _MISSING:
            # Read first, so an entry taken from before a `clear()` is not put back.
            epoch = self.epoch
            value = self._old.pop(key, _MISSING)
            if value is _MISSING:
                self.misses += 1
                return _MISSING
            self.put(key, value, epoch)
        self.hits += 1
        return value

    def put(self, key, value, epoch=None):
        """Store `value` and return it; not stored if `epoch` is given and out of date."""
        # `acquire` and `release`: `with` costs twice as much, and this runs on every miss.
        lock = self._lock
        lock.acquire()
        try:
            if epoch is not None and epoch != self.epoch:
                return value
            young = self._young
            if len(young) >= self._generation or self._young_weight >= self._budget:
                self._old = young
                self._young = young = {}
                self._young_weight = 0
            young[key] = value
            if self._weigh is not None:
                self._young_weight += self._weigh(key, value)
        finally:
            lock.release()
        return value

    def clear(self):
        with self._lock:
            self._young = {}
            self._old = {}
            self._young_weight = 0
            self.epoch += 1
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
