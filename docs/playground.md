# Playground

**[Open the playground](https://r0ny123.github.io/demangle/playground/)**

The playground is this library running in the browser, on [Pyodide](https://pyodide.org/)
in a Web Worker. The runtime comes from a CDN on first load; after that nothing is sent
anywhere, and the symbols you paste stay on the page.

Paste a name and it is read as you type, by whichever of the fourteen schemes claims it,
or by the one you pick. Beside the spelling it shows:

- the output in either style, `llvm` or `gnu`, and as the whole declaration, the input
  beside it, the base name only, or the name without its parameters;
- the parts `demangle.signature()` gives: qualified name, base name, scope, parameters,
  return type, calling convention and qualifiers;
- the parse tree, collapsible, with a shared node (`$ref`) a link to where it was first
  written, and the whole tree as JSON to copy.

**Batch** mode reads one symbol per line, keeping the order and the blank lines, and
reports a failure on its own row. A batch is limited to 500 lines and 256 KiB of input,
with a 4 MiB output budget; past it, the remaining lines are left as they are. The parts
and the tree are shown in single-symbol mode only.

**Copy link** puts the input, scheme, style, output view and mode in the URL fragment, up
to 8,000 characters. Anyone with the link can read the input.

| Shortcut | Action |
| --- | --- |
| `Ctrl`/`Cmd` + `K` | Focus the input |
| `Esc` | Clear the input |
| `Ctrl`/`Cmd` + `Enter` | Read the input again |
