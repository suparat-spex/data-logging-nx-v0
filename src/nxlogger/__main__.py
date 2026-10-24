"""Entry point: `python -m nxlogger`, the `nxlogger` command and the packaged EXE."""
import sys

from nxlogger.ui.app import main

if __name__ == "__main__":
    sys.exit(main())
