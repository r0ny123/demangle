# Playground

**[Open the playground](https://r0ny123.github.io/demangle/playground/)**

The playground is this library running in your browser, on
[Pyodide](https://pyodide.org/) in a Web Worker. The site serves the runtime itself, so
the page loads nothing from anyone else, and the symbols you paste never leave it.

Paste a name and it's read as you type, by whichever scheme claims it or the one you
pick. Next to the spelling you get:

- the output in either style, `llvm` or `gnu`, as the whole declaration, with the input
  beside it, as the base name only, or without the parameters;
- the parts `demangle.signature()` gives;
- the parse tree, collapsible, and the same tree as JSON to copy. A shared node (`$ref`)
  links to where it was first written.

**Batch** mode reads one symbol per line, keeps the order and the blank lines, and
reports a failure on its own row. A batch can be up to 500 lines and 256 KiB, with a
4 MiB output budget; lines past that are left as they are. The parts and the tree show
in single-symbol mode only.

**Copy link** puts the input, scheme, style, output view and mode in the URL fragment,
up to 8,000 characters. Anyone with the link can read the input.

| Shortcut | Action |
| --- | --- |
| `Ctrl`/`Cmd` + `K` | Focus the input |
| `Esc` | Clear the input |
| `Ctrl`/`Cmd` + `Enter` | Read the input again |
