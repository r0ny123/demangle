package v25

type Ünïcødé struct{ X int }
func (Ünïcødé) Método() int { return 1 }
func (*Ünïcødé) Pointeró() int { return 2 }
func Frëe[T any](x T) T { return x }
type Iface interface{ Método() int }
func Closure() func() int { return func() int { return 3 } }

// The package directory is deliberately named `v2.5`: Go escapes a `.` that falls after
// the last `/` of an import path, so this is what produces `v2%2e5` in every symbol the
// package contributes. Identifiers carry non-ASCII for the same reason -- to prove the
// decoder handles UTF-8 as bytes, the way Go's own PathToPrefix does.
