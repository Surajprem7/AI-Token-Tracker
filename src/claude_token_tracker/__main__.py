"""`python -m claude_token_tracker`: GUI with no arguments, CLI otherwise."""

import sys

if len(sys.argv) > 1:
    from .cli import main
else:
    from .gui import main

sys.exit(main())
