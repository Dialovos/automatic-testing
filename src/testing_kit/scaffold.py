"""Adopt the shared runner by generating configuration, without copying code."""

from __future__ import annotations

import json
from pathlib import Path

from .config import CONFIG_NAME, PLACEHOLDER, ConfigError

PRESETS = {
    "python": ["{python}", "-m", "pytest"],
    "node": ["npm", "test"],
    "go": ["go", "test", "./..."],
    "rust": ["cargo", "test"],
    "dotnet": ["dotnet", "test"],
}


def initialize(project: Path, command: list[str] | None, preset: str | None) -> Path:
    root = project.resolve(strict=True)
    if not root.is_dir():
        raise ConfigError("--project must be an existing directory")
    target = root / CONFIG_NAME
    ignore = root / ".gitignore"
    if ignore.is_symlink():
        raise ConfigError("Refusing to modify a .gitignore symlink")
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    arguments = command or PRESETS.get(preset, [PLACEHOLDER])
    # JSON string escaping is also valid for these TOML basic strings.
    contents = (
        "# Test commands belong to this project; execution and reports live in the shared kit.\n"
        'version = 1\ntimeout = 300\nartifacts_dir = "artifacts/testkit"\n\n'
        '[env]\nCI = "true"\n\n'
        '[[checks]]\nname = "test"\n'
        f"command = {json.dumps(arguments, ensure_ascii=False)}\n"
        'tags = ["test"]\n'
    )
    try:
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(contents)
    except FileExistsError as error:
        raise ConfigError(f"{target} already exists; edit it to add more checks") from error
    if "/artifacts/testkit/" not in existing.splitlines():
        with ignore.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n" if existing and not existing.endswith("\n") else "")
            stream.write("/artifacts/testkit/\n")
    return target
