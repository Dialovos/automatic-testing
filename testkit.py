"""Run the kit directly from a checkout without installing it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from testing_kit.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
