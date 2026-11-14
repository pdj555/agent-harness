"""Command-line entry for ``harness.evals.paired_generator``, where the code now lives so that it ships.

    uv run python scripts/generate_paired.py --help
"""

import sys

from harness.evals.paired_generator import *  # noqa: F403
from harness.evals.paired_generator import main

if __name__ == "__main__":
    sys.exit(main())
