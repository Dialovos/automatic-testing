# Automatic Testing Kit

A shared testing setup for any project that can run its tests from a command.
Install or reference the kit once; each project keeps a small `testing.toml` file
with its own commands. Improvements to execution and reporting stay in one place.

The first version runs real commands, selects checks by name or tag, enforces
timeouts, captures logs, and produces JSON and JUnit reports. The runner uses
Python 3.11+ and has no third-party runtime dependencies. The projects being
tested can use any language or test framework.

## Use it immediately

Clone this repository, then point the runner at a project that already has a test
command. `--command` must be the last option:

```powershell
git clone https://github.com/Dialovos/automatic-testing.git
cd automatic-testing

# Example: adopt it in an existing Rust project.
python testkit.py init --project ../my-project --command cargo test --locked
python testkit.py run --project ../my-project --dry-run
python testkit.py run --project ../my-project

# Run the kit's own integration tests as a working demonstration.
python testkit.py run
```

Replace `../my-project` with an existing project directory and supply its actual
test command. `init` creates `testing.toml` and appends `/artifacts/testkit/` to
that project's `.gitignore`. It refuses to overwrite an existing configuration.
It does not install dependencies. Without `--command` or `--preset`, it writes
a template that refuses to run until its placeholder command is replaced.

For a command available from any directory, install the kit in a tooling
environment from this repository's root:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/testkit run
```

On Linux/macOS, use `.venv/bin/python` and `.venv/bin/testkit`. The direct
`python path/to/testkit.py` entry point works without a package installation.
The kit stays separate from each target project's dependencies.

## Configure a project

This example tests two components of a mixed-language project. Create those
component directories and install their dependencies before running it:

```toml
version = 1
timeout = 300
artifacts_dir = "artifacts/testkit"

[env]
CI = "true"

[[checks]]
name = "backend"
command = ["{python}", "-m", "pytest", "-q"]
cwd = "backend"
tags = ["unit", "backend"]

[[checks]]
name = "frontend"
command = ["npm", "test"]
cwd = "frontend"
tags = ["unit", "frontend"]
timeout = 120

[checks.env]
NODE_ENV = "test"
```

Commands are argument arrays, with one argument per string. The kit adds no shell
wrapper, variable interpolation, pipes, or command chaining. For a multi-step
check, put those steps in a project-owned script and invoke it explicitly. On
Windows, executable lookup resolves tools such as `npm` to `npm.cmd`; Windows
batch scripts retain their own shell parsing rules. Configurations execute code,
so run commands from projects you trust.

| Field | Meaning |
| --- | --- |
| `version` | Required; currently `1` |
| `timeout` | Positive seconds per check; default `300`; can be overridden per check |
| `artifacts_dir` | Output directory within the project; default `artifacts/testkit` |
| `env` | String environment overrides, inherited by every check |
| `checks[].name` | Unique identifier using letters, digits, `.`, `_`, `-` |
| `checks[].command` | Required nonempty array of executable and arguments |
| `checks[].cwd` | Working directory within the project; default `.` |
| `checks[].tags` | Optional labels such as `smoke`, `unit`, `integration`, `e2e` |
| `checks[].env` | Per-check environment overrides, applied after global overrides |

`{python}` as a complete argument selects the target project's root `.venv`,
then the interpreter running the kit. `--python PATH` overrides that choice;
relative override paths are relative to your terminal's working directory.
Components with separate environments can specify their interpreter directly
in `command`. Other tools must be installed and available on `PATH`, or named
by an explicit path. Paths in command arguments are relative to the check's
`cwd`; the `cwd` and output directory themselves are relative to `--project`.

## Select what runs

```powershell
python testkit.py validate --project ../my-project
python testkit.py run --project ../my-project --tag smoke
python testkit.py run --project ../my-project --check backend --check frontend
python testkit.py run --project ../my-project --tag unit --dry-run
python testkit.py run --project ../my-project --fail-fast
```

Checks run sequentially in configuration order. Repeated tags match any of those
tags; combining tags and names requires both filters to match. Unknown names,
unknown tags, and empty selections fail before executing anything. `--dry-run`
validates and displays the selection without running commands or creating output.
Validation checks the configuration; it does not verify that tools are installed.

By default, remaining checks still run after a failure. `--fail-fast` stops after
the first unsuccessful check and records later checks as skipped. There are no
automatic retries. Test commands must finish on their own; use a framework's CI
or single-run mode when its default starts a watcher.

## Reuse across stacks

These presets are shortcuts for an initial command, with no framework-specific
logic in the runner. Use `init --preset NAME` instead of `init --command ...`:

| Preset / project | Command |
| --- | --- |
| `python` | `python -m pytest` (through `{python}`) |
| `node` | `npm test` |
| `go` | `go test ./...` |
| `rust` | `cargo test` |
| `dotnet` | `dotnet test` |
| Java, C++, mobile, API, AI/data, or another stack | Supply its existing command with `--command` |

Unit tests, API tests, browser checks, data validation, and model evaluations all
use the same command interface. Each project still owns its assertions, fixtures,
service setup, dependencies, and acceptance thresholds. The kit automates running
those checks; it does not generate tests or infer whether coverage is sufficient.
See [adoption and CI examples](docs/adoption.md).

## Read results

Every run creates a separate directory:

```text
artifacts/testkit/<timestamp>-<id>/
  summary.json        Overall result, commands, original exit codes, durations
  junit.xml           One test case per configured command
  001-backend.log     Combined stdout/stderr for that command
  002-frontend.log
```

The terminal prints progress and paths to logs. Logs are written directly to disk
so large output does not accumulate in runner memory. Reports count configured
checks, not individual assertions inside pytest, Jest, or another framework.
For individual test results or coverage, configure the underlying framework to
write its own reports as well.

| Kit exit code | Meaning |
| --- | --- |
| `0` | All selected commands exited successfully |
| `1` | A command failed, could not start, or timed out |
| `2` | Invalid configuration, selection, or file operation |
| `130` | Interrupted by the user |

A command succeeds only with exit code zero. The kit preserves each command's
original exit code in JSON. Timeout and interruption terminate the launched
process tree using process groups on POSIX and Windows Job Objects.
Detached services should have their own cleanup in the project's test scripts.
Runs are retained until you remove them; there is no automatic retention policy.
If you change `artifacts_dir`, update your ignore rule accordingly. Logs and
command arguments may contain project data; keep generated output out of Git.

## Development

```powershell
python -m unittest discover -s tests -v
python testkit.py run
```

The [CI workflow](.github/workflows/ci.yml) tests the kit on Windows and Linux
with Python 3.11 and 3.12 and verifies the installed entry point. The repository
contains the shared runner; consuming projects keep their own test configuration.

Implementation references: [Python subprocess](https://docs.python.org/3.11/library/subprocess.html),
[Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
[pytest exit codes](https://docs.pytest.org/en/stable/reference/exit-codes.html),
and [npm test](https://docs.npmjs.com/cli/v11/commands/npm-test).
