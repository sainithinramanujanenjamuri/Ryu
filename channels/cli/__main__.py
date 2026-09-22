"""CLI Channel entrypoint for `python -m channels.cli`."""

import sys
from channels.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
