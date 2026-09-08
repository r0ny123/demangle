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

// A struct tag reaches a symbol name quoted and verbatim -- in the `.eq` and `.hash`
// routines the compiler generates for a map key, and in the shape of a generic
// instantiation -- so a `%` inside one is not an escape, while the package path of a
// named type beside it is escaped as it is everywhere. `Tagged` is an alias, not a
// named type, so that the struct itself, tags and all, is what the symbols spell.
type Tagged = struct {
	S string `json:"50%" x:"q\"z"`
	K Ünïcødé
}

type Box[T any] struct{ V T }

func (b *Box[T]) Get() T { return b.V }

func Same[T comparable](a, b T) bool { return a == b }

var Seen = map[Tagged]int{}

func Count(t Tagged) (int, bool) {
	Seen[t]++
	box := &Box[Tagged]{V: t}
	return box.Get().K.X, Same(t, t)
}
