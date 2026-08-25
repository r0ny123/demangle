package main

import (
	"fmt"
	a "example.com/corpus/v2.5"
	b "example.com/corpus/weird.pkg.name"
	c "example.com/corpus/plain"
)

func main() {
	var u a.Ünïcødé
	var i a.Iface = u
	var t b.T
	var s c.S
	fmt.Println(u.Método(), u.Pointeró(), a.Frëe(1), a.Closure()(), i.Método(),
		t.M(), t.P(), b.G(map[string]int{}), s.Go())
}
