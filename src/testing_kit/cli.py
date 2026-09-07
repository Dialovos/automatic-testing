"""Command-line interface for configuring and running any project's checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ConfigError, load_config, select_checks
from .runner import python_for, run_checks
from .scaffold import PRESETS, initialize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    init = subparsers.add_parser("init", help="Create testing.toml and ignore generated reports")
    validate = subparsers.add_parser(
        "validate", help="Validate configuration without running commands"
    )
    run = subparsers.add_parser("run", help="Run selected checks and write JSON/JUnit reports")
    for subparser in (init, validate, run):
        subparser.add_argument("--project", type=Path, default=Path.cwd())
    source = init.add_mutually_exclusive_group()
    source.add_argument("--preset", choices=sorted(PRESETS))
    source.add_argument("--command", nargs=argparse.REMAINDER, help="Test command; must come last")
    run.add_argument(
        "--check", action="append", default=[], help="Check name; repeat to select several"
    )
    run.add_argument(
        "--tag", action="append", default=[], help="Match any listed tag; repeat as needed"
    )
    run.add_argument(
        "--python", help="Interpreter for {python}; defaults to target .venv, then this Python"
    )
    run.add_argument("--fail-fast", action="store_true")
    run.add_argument(
        "--dry-run", action="store_true", help="Show selection without running or writing"
    )
    args = parser.parse_args(argv)
    try:
        if args.action == "init":
            if args.command == []:
                raise ConfigError("--command requires an executable and optional arguments")
            target = initialize(args.project, args.command, args.preset)
            print(f"Created {target}")
            print("Review test commands, then run: testkit run --project <project-path>")
            return 0
        config = load_config(args.project)
        if args.action == "validate":
            print(f"Valid configuration: {len(config.checks)} check(s) in {config.root}")
            return 0
        checks = select_checks(config, args.check, args.tag)
        interpreter = python_for(config.root, args.python)
        if args.dry_run:
            print(
                json.dumps(
                    [
                        {
                            "name": check.name,
                            "command": [
                                interpreter if arg == "{python}" else arg for arg in check.command
                            ],
                            "cwd": str(check.cwd),
                            "timeout": check.timeout,
                            "tags": list(check.tags),
                        }
                        for check in checks
                    ],
                    indent=2,
                )
            )
            return 0
        report, directory = run_checks(config, checks, interpreter, args.fail_fast)
        print(f"{report['status'].upper()}: reports in {directory}")
        return report["exit_code"]
    except (ConfigError, OSError, ValueError) as error:
        print(f"testkit: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("testkit: interrupted", file=sys.stderr)
        return 130
