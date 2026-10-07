"""`python -m demangle`: the `demangle` command, for an environment with no script on PATH."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
