"""Run commands with bounded execution and retain one set of artifacts per run."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from .config import Check, Config


def python_for(root: Path, override: str | None) -> str:
    if override:
        candidate = Path(override)
        if candidate.is_file() or "/" in override or "\\" in override:
            return str(candidate.resolve())
        return override
    venv = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(venv) if venv.is_file() else sys.executable


def command_for(check: Check, interpreter: str, env: dict[str, str]) -> list[str]:
    command = [interpreter if arg == "{python}" else arg for arg in check.command]
    executable = Path(command[0])
    if not executable.is_absolute() and ("/" in command[0] or "\\" in command[0]):
        command[0] = str((check.cwd / executable).resolve())
    else:
        # Resolving npm -> npm.cmd is necessary on Windows. No shell wrapper is added.
        command[0] = shutil.which(command[0], path=env.get("PATH")) or command[0]
    return command


def stop_process(process: subprocess.Popen, job=None) -> None:
    """Stop this check's process tree on timeout or interruption."""
    if job is not None:
        job.close()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def execute(check: Check, config: Config, interpreter: str, log: Path) -> dict:
    env = os.environ.copy()
    env.update(config.env)
    env.update(check.env)
    command = command_for(check, interpreter, env)
    started = time.monotonic()
    result = {
        "name": check.name,
        "command": command,
        "cwd": str(check.cwd),
        "tags": list(check.tags),
        "status": "error",
        "exit_code": None,
        "duration_seconds": 0.0,
        "log": log.name,
        "message": "",
    }
    job = None
    process = None
    startup_error = log.with_suffix(".startup-error.txt")
    with log.open("wb") as output:
        try:
            if os.name == "nt":
                from .windows_job import WindowsJob

                job = WindowsJob()
                worker = Path(__file__).with_name("windows_worker.py")
                process = subprocess.Popen(
                    [sys.executable, str(worker), str(startup_error), *command],
                    cwd=check.cwd,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                )
                # The worker cannot spawn children until it belongs to this job.
                job.assign(process.pid)
                process.stdin.write(b"G")
                process.stdin.close()
            else:
                process = subprocess.Popen(
                    command,
                    cwd=check.cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    start_new_session=True,
                )
        except OSError as error:
            if process is not None:
                process.kill()
                process.wait(timeout=10)
                if process.stdin:
                    process.stdin.close()
            if job is not None:
                job.close()
            result["message"] = f"Could not start command: {error}"
            output.write(result["message"].encode("utf-8", errors="replace"))
        else:
            try:
                result["exit_code"] = process.wait(timeout=check.timeout)
                result["status"] = "passed" if result["exit_code"] == 0 else "failed"
                if result["status"] == "failed":
                    result["message"] = f"Command exited with code {result['exit_code']}"
                if startup_error.exists():
                    result.update(
                        status="error",
                        exit_code=None,
                        message="Could not start command: "
                        + startup_error.read_text(encoding="utf-8"),
                    )
                    output.write(result["message"].encode("utf-8", errors="replace"))
            except subprocess.TimeoutExpired:
                stop_process(process, job)
                result.update(status="timeout", message=f"Exceeded {check.timeout:g} seconds")
            except KeyboardInterrupt:
                stop_process(process, job)
                result.update(status="interrupted", message="Interrupted by user")
            finally:
                if job is not None:
                    job.close()
    result["duration_seconds"] = round(time.monotonic() - started, 6)
    return result


def xml_text(value: str) -> str:
    # OS errors and tool arguments can contain characters XML 1.0 cannot represent.
    return re.sub("[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]", "?", value)


def write_reports(report: dict, directory: Path) -> None:
    (directory / "summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    checks = report["checks"]
    suite = ET.Element(
        "testsuite",
        {
            "name": xml_text(Path(report["project"]).name),
            "tests": str(len(checks)),
            "failures": str(sum(check["status"] == "failed" for check in checks)),
            "errors": str(
                sum(check["status"] in {"error", "timeout", "interrupted"} for check in checks)
            ),
            "skipped": str(sum(check["status"] == "skipped" for check in checks)),
            "time": str(report["duration_seconds"]),
            "timestamp": report["started_at"],
        },
    )
    for check in checks:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "name": check["name"],
                "classname": suite.attrib["name"],
                "time": str(check["duration_seconds"]),
            },
        )
        if check["status"] != "passed":
            kind = {"failed": "failure", "skipped": "skipped"}.get(check["status"], "error")
            ET.SubElement(
                case,
                kind,
                {
                    "type": check["status"],
                    "message": xml_text(check["message"]),
                },
            )
        if check["log"]:
            ET.SubElement(case, "system-out").text = f"Command output: {check['log']}"
    ET.indent(suite)
    ET.ElementTree(suite).write(directory / "junit.xml", encoding="utf-8", xml_declaration=True)


def run_checks(
    config: Config,
    checks: tuple[Check, ...],
    interpreter: str,
    fail_fast: bool = False,
) -> tuple[dict, Path]:
    started_at = datetime.now(UTC)
    started = time.monotonic()
    directory = config.artifacts / (started_at.strftime("%Y%m%dT%H%M%S%fZ-") + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    results = []
    stopped = False
    interrupted = False
    for index, check in enumerate(checks, start=1):
        if stopped:
            results.append(
                {
                    "name": check.name,
                    "command": list(check.command),
                    "cwd": str(check.cwd),
                    "tags": list(check.tags),
                    "status": "skipped",
                    "exit_code": None,
                    "duration_seconds": 0.0,
                    "log": None,
                    "message": "Earlier check interrupted"
                    if interrupted
                    else "Stopped by --fail-fast",
                }
            )
            continue
        print(
            f"[{index}/{len(checks)}] Running {check.name} (timeout {check.timeout:g}s)", flush=True
        )
        result = execute(check, config, interpreter, directory / f"{index:03d}-{check.name}.log")
        results.append(result)
        print(f"  {result['status'].upper()} ({result['duration_seconds']:.2f}s)", flush=True)
        if result["status"] != "passed":
            print(f"  {result['message']}; log: {directory / result['log']}", flush=True)
        interrupted = result["status"] == "interrupted"
        stopped = interrupted or (fail_fast and result["status"] != "passed")
    exit_code = 130 if interrupted else int(any(item["status"] != "passed" for item in results))
    report = {
        "schema_version": 1,
        "project": str(config.root),
        "started_at": started_at.isoformat(),
        "duration_seconds": round(time.monotonic() - started, 6),
        "status": "passed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "checks": results,
    }
    write_reports(report, directory)
    return report, directory
