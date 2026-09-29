"""Bounded memoisation.

Symbol tables repeat themselves relentlessly: one C++ binary can name
`std::allocator<char>` thousands of times, and every one costs the same parse. Caching
is worth more here than almost any micro-optimisation.

It also cannot be unbounded. A tool that walks a corpus of binaries would otherwise
accumulate an entry per distinct symbol ever seen, and there is no natural end to that.
"""

_MISSING = object()


class BoundedCache:
    """A dict that clears itself wholesale rather than growing without limit.

    Wholesale clearing rather than LRU eviction is deliberate. Tracking recency costs a
    linked-list update on every *hit*, which is the operation being optimised; dropping
    everything at a high-water mark costs nothing on hits and re-warms quickly, because
    symbol access in a binary is heavily clustered.
    """

    __slots__ = ("_data", "hits", "max_size", "misses")

    def __init__(self, max_size=8192):
        self._data = {}
        self.max_size = max_size
        self.hits = 0
        self.misses = 0

    def get(self, key):
        value = self._data.get(key, _MISSING)
        if value is _MISSING:
            self.misses += 1
            return _MISSING
        self.hits += 1
        return value

    def put(self, key, value):
        data = self._data
        if len(data) >= self.max_size:
            data.clear()
        data[key] = value
        return value

    def clear(self):
        self._data.clear()
        self.hits = 0
        self.misses = 0

    @property
    def stats(self):
        total = self.hits + self.misses
        return {
            "size": len(self._data),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hits / total if total else 0.0,
        }

    def __len__(self):
        return len(self._data)

    def __contains__(self, key):
        return key in self._data


MISSING = _MISSING
