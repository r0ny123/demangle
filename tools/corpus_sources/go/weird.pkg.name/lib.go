package weird
type T struct{ A int }
func (T) M() int { return 1 }
func (*T) P() int { return 2 }
func G[K comparable, V any](m map[K]V) int { return len(m) }
