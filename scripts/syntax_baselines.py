"""Command-line entry for ``harness.evals.shortcuts``, where the code now lives so that it ships.

    uv run python scripts/syntax_baselines.py --help
"""

import sys

from harness.evals.shortcuts import *  # noqa: F403
from harness.evals.shortcuts import main

if __name__ == "__main__":
    sys.exit(main())
