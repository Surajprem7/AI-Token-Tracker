"""Entry point for the standalone (PyInstaller) builds."""

import sys

from ai_token_tracker.gui import main

if __name__ == "__main__":
    sys.exit(main())
