"""Load the small, strict configuration shared by every supported language."""

from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "testing.toml"
PLACEHOLDER = "REPLACE_WITH_TEST_COMMAND"


class ConfigError(ValueError):
    """A configuration cannot be executed as written."""


@dataclass(frozen=True)
class Check:
    name: str
    command: tuple[str, ...]
    cwd: Path
    timeout: float
    tags: tuple[str, ...]
    env: dict[str, str]


@dataclass(frozen=True)
class Config:
    root: Path
    artifacts: Path
    env: dict[str, str]
    checks: tuple[Check, ...]


def text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ConfigError(f"{label} must be a nonempty string without null bytes")
    return value


def keys(table: object, allowed: set[str], label: str) -> None:
    if not isinstance(table, dict):
        raise ConfigError(f"{label} must be a table")
    unknown = set(table) - allowed
    if unknown:
        raise ConfigError(f"Unknown {label} field(s): {', '.join(sorted(unknown))}")


def timeout_seconds(value: object, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{label} must be a positive, finite number of seconds")
    return float(value)


def environment(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a table of string values")
    for key, item in value.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigError(f"Invalid environment variable name in {label}")
        if not isinstance(item, str) or "\0" in item:
            raise ConfigError(f"{label}.{key} must be a string without null bytes")
    return dict(value)


def project_path(root: Path, value: object, label: str) -> Path:
    relative = Path(text(value, label))
    if relative.is_absolute() or relative.drive:
        raise ConfigError(f"{label} must be relative to the project")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ConfigError(f"{label} must stay inside the project")
    return resolved


def load_config(project: Path) -> Config:
    root = project.resolve(strict=True)
    with (root / CONFIG_NAME).open("rb") as stream:
        document = tomllib.load(stream)
    keys(document, {"version", "timeout", "artifacts_dir", "env", "checks"}, "config")
    if type(document.get("version")) is not int or document["version"] != 1:
        raise ConfigError("testing.toml requires version = 1")
    timeout = timeout_seconds(document.get("timeout", 300), "timeout")
    artifacts = project_path(
        root, document.get("artifacts_dir", "artifacts/testkit"), "artifacts_dir"
    )
    if artifacts == root or (artifacts.exists() and not artifacts.is_dir()):
        raise ConfigError("artifacts_dir must name an output directory below the project")
    env = environment(document.get("env", {}), "env")
    entries = document.get("checks")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("Define at least one [[checks]] table")
    checks = []
    names = set()
    for entry in entries:
        keys(entry, {"name", "command", "cwd", "timeout", "tags", "env"}, "check")
        name = text(entry.get("name"), "check.name")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) or name in names:
            raise ConfigError("Check names must be unique identifiers using letters, digits, ._-")
        names.add(name)
        command = entry.get("command")
        if not isinstance(command, list) or not command:
            raise ConfigError(f"{name}.command must be a nonempty array of arguments")
        # Empty arguments are valid; an empty executable is not.
        if any(not isinstance(arg, str) or "\0" in arg for arg in command):
            raise ConfigError(f"{name}.command arguments must be strings without null bytes")
        text(command[0], f"{name}.command executable")
        if command[0] == PLACEHOLDER:
            raise ConfigError(f"Replace {PLACEHOLDER} with your actual test command")
        cwd = project_path(root, entry.get("cwd", "."), f"{name}.cwd")
        if not cwd.is_dir():
            raise ConfigError(f"{name}.cwd does not exist or is not a directory")
        tags = entry.get("tags", [])
        if not isinstance(tags, list):
            raise ConfigError(f"{name}.tags must be an array of strings")
        for tag in tags:
            text(tag, f"{name}.tags")
        checks.append(
            Check(
                name=name,
                command=tuple(command),
                cwd=cwd,
                timeout=timeout_seconds(entry.get("timeout", timeout), f"{name}.timeout"),
                tags=tuple(tags),
                env=environment(entry.get("env", {}), f"{name}.env"),
            )
        )
    return Config(root, artifacts, env, tuple(checks))


def select_checks(config: Config, names: list[str], tags: list[str]) -> tuple[Check, ...]:
    unknown = set(names) - {check.name for check in config.checks}
    if unknown:
        raise ConfigError(f"Unknown check(s): {', '.join(sorted(unknown))}")
    unknown_tags = set(tags) - {tag for check in config.checks for tag in check.tags}
    if unknown_tags:
        raise ConfigError(f"Unknown tag(s): {', '.join(sorted(unknown_tags))}")
    selected = tuple(
        check
        for check in config.checks
        if (not names or check.name in names) and (not tags or set(tags).intersection(check.tags))
    )
    if not selected:
        raise ConfigError("No checks match the selection")
    return selected
