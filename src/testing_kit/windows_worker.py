"""Wait for job assignment before spawning the user's command on Windows."""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if sys.stdin.buffer.read(1) != b"G":
        return 1
    try:
        return subprocess.call(sys.argv[2:], stdin=subprocess.DEVNULL, shell=False)
    except OSError as error:
        # A side channel distinguishes startup errors from any real command exit code.
        Path(sys.argv[1]).write_text(str(error), encoding="utf-8")
        return 1


if __name__ == "__main__":
    sys.exit(main())
