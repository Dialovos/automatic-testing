# Adopt the shared testing setup

Keep the kit in one checkout or tooling environment. Commit only a project's
`testing.toml`, its tests/scripts, and the ignore rule needed for generated
reports. Existing projects can adopt it one at a time.

## Any language

From this kit's directory, for a project that already uses a Gradle wrapper:

```powershell
python testkit.py init --project ../java-service --command ./gradlew.bat test
python testkit.py run --project ../java-service
```

On Linux, use `./gradlew`. Use platform-neutral tools where possible, or a small
project-owned script that selects the right platform command. A command is
reusable when its program, dependencies, and input data exist on the host.

For CMake projects, separate configure, build, and test commands if they need
independent reports, then use `--fail-fast` so a failed build stops the run.
For API/browser tests, let the project's script start the service, wait for
readiness, run assertions, and clean up in a `finally` block or equivalent.

For AI/data projects, use fast synthetic fixtures for routine checks. Add model
evaluations or hardware-dependent checks under explicit tags such as `gpu` or
`evaluation`. A check script should exit nonzero when its quality threshold is
missed. The kit does not interpret model scores or provision GPUs.

## Integrate with a workspace

First create and verify a target project's `testing.toml`. A workspace command
catalog can then invoke the shared kit. For example, if this checkout and the
target project are sibling directories:

```json
"checks": [
  ["{python}", "../automatic-testing/testkit.py", "run", "--tag", "unit"]
]
```

This example requires a `unit` tag and a catalog runner that resolves `{python}`
to an installed Python interpreter. Run from the target project's directory so
no `--project` is necessary. Keep dependency installation in the target project's
CI job. Catalog registration does not install runtimes or dependencies.

## CI

The same command works locally and in any CI provider. Install the target
project's dependencies, then invoke the shared checkout:

```text
python /path/to/automatic-testing/testkit.py run --project /path/to/YOUR_PROJECT
```

For an independent repository, provision a checkout of the kit at a reviewed
revision, install it with `python -m pip install /path/to/automatic-testing`, and
run `testkit run` from the target repository. The kit is not published to a public
package registry. A local editable install picks up shared changes immediately;
a checkout pinned to a revision lets a CI consumer control upgrades.

Retain `artifacts/testkit/` using the CI provider's artifact mechanism even when
the command fails. Import `junit.xml` if the provider supports JUnit. The kit
does not upload logs or publish results itself.

## Scope of this first version

Implemented: command configuration, optional starter presets, filtering,
sequential execution, per-check timeouts, process cleanup, logs, JSON/JUnit,
local CLI, and CI checks for the kit itself.

Possible extensions: reusable configuration includes, watch mode, service
lifecycle hooks, parallel checks, report history, and framework-specific result
aggregation. These are future work; the shared command interface supports new
languages today without adding runtime adapters.
