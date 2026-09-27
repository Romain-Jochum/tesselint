"""`python -m tesselint` runs the CLI; JobManager's default `cli` (docs/ui/DESIGN.md §4.4)."""

import sys

from tesselint.cli import main

if __name__ == "__main__":
    sys.exit(main())
