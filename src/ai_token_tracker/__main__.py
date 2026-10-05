"""`python -m ai_token_tracker`: GUI with no arguments (or --widget), CLI otherwise."""

import sys

if len(sys.argv) > 1 and sys.argv[1:] != ["--widget"]:
    from .cli import main
else:
    from .gui import main

sys.exit(main())
