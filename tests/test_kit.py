"""Exercise the public CLI against real commands in isolated target projects."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
CLI = KIT / "testkit.py"
sys.path.insert(0, str(KIT / "src"))

from testing_kit.config import ConfigError, load_config  # noqa: E402
from testing_kit.runner import python_for  # noqa: E402


class KitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="testing-kit-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.project = self.base / "project with spaces"
        self.project.mkdir()

    def cli(self, action, *arguments):
        return subprocess.run(
            [sys.executable, str(CLI), action, "--project", str(self.project), *arguments],
            cwd=self.base,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def config(self, checks, extra=""):
        lines = ["version = 1", "timeout = 10", extra]
        for name, command, options in checks:
            lines += [
                "[[checks]]",
                f"name = {json.dumps(name)}",
                f"command = {json.dumps(command)}",
            ]
            lines += [f"{key} = {json.dumps(value)}" for key, value in options.items()]
        (self.project / "testing.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def python_check(self, name, code, **options):
        return (name, ["{python}", "-c", code], options)

    def report(self):
        files = list((self.project / "artifacts/testkit").glob("*/summary.json"))
        self.assertEqual(len(files), 1)
        return json.loads(files[0].read_text(encoding="utf-8")), files[0].parent

    def test_init_runs_custom_command_and_preserves_existing_files(self):
        ignore = self.project / ".gitignore"
        ignore.write_text("existing-output/", encoding="utf-8")
        result = self.cli("init", "--command", "{python}", "-c", "print('real command')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ignore.read_text(), "existing-output/\n/artifacts/testkit/\n")
        original = (self.project / "testing.toml").read_bytes()
        repeat = self.cli("init", "--preset", "node")
        self.assertEqual(repeat.returncode, 2)
        self.assertEqual((self.project / "testing.toml").read_bytes(), original)
        self.assertEqual(self.cli("validate").returncode, 0)
        run = self.cli("run")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        report, directory = self.report()
        self.assertEqual(report["status"], "passed")
        self.assertIn("real command", (directory / report["checks"][0]["log"]).read_text())

    def test_generic_template_requires_a_real_command(self):
        self.assertEqual(self.cli("init").returncode, 0)
        result = self.cli("run")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Replace REPLACE_WITH_TEST_COMMAND", result.stderr)
        self.assertFalse((self.project / "artifacts").exists())

    def test_preset_is_only_configuration(self):
        result = self.cli("init", "--preset", "rust")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(load_config(self.project).checks[0].command, ("cargo", "test"))
        self.assertEqual({p.name for p in self.project.iterdir()}, {"testing.toml", ".gitignore"})

    def test_empty_command_is_rejected_before_writing(self):
        self.assertEqual(self.cli("init", "--command").returncode, 2)
        self.assertFalse((self.project / "testing.toml").exists())

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node/npm not installed")
    def test_node_preset_runs_javascript_without_external_packages(self):
        (self.project / "package.json").write_text(
            json.dumps({"private": True, "scripts": {"test": "node check.cjs"}}),
            encoding="utf-8",
        )
        script = self.project / "check.cjs"
        script.write_text(
            "require('node:assert/strict').equal(2 + 2, 4); console.log('JavaScript passed');\n",
            encoding="utf-8",
        )
        self.assertEqual(self.cli("init", "--preset", "node").returncode, 0)
        result = self.cli("run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report, directory = self.report()
        self.assertIn("JavaScript passed", (directory / report["checks"][0]["log"]).read_text())
        script.write_text("require('node:assert/strict').equal(2 + 2, 5);\n", encoding="utf-8")
        self.assertEqual(self.cli("run").returncode, 1)

    def test_failures_keep_the_exit_code_and_do_not_hide_later_checks(self):
        self.config(
            [
                self.python_check("first", "print('first output')"),
                self.python_check("broken", "import sys; print('failure detail'); sys.exit(7)"),
                self.python_check("last", "print('last output')"),
            ]
        )
        result = self.cli("run")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report, directory = self.report()
        self.assertEqual([r["status"] for r in report["checks"]], ["passed", "failed", "passed"])
        self.assertEqual(report["checks"][1]["exit_code"], 7)
        self.assertIn("failure detail", (directory / report["checks"][1]["log"]).read_text())
        suite = ET.parse(directory / "junit.xml").getroot()
        self.assertEqual(suite.attrib["tests"], "3")
        self.assertEqual(suite.attrib["failures"], "1")
        self.assertEqual(suite.attrib["errors"], "0")
        self.assertIsNotNone(suite.find("testcase[@name='broken']/failure"))

    def test_fail_fast_records_skipped_commands_without_executing_them(self):
        self.config(
            [
                self.python_check("broken", "raise SystemExit(3)"),
                self.python_check(
                    "later", "from pathlib import Path; Path('should-not-exist').touch()"
                ),
            ]
        )
        self.assertEqual(self.cli("run", "--fail-fast").returncode, 1)
        report, directory = self.report()
        self.assertEqual(report["checks"][1]["status"], "skipped")
        self.assertFalse((self.project / "should-not-exist").exists())
        self.assertEqual(ET.parse(directory / "junit.xml").getroot().attrib["skipped"], "1")

    def test_selection_and_dry_run_have_no_side_effects(self):
        self.config(
            [
                self.python_check("fast", "print('fast')", tags=["smoke", "unit"]),
                self.python_check("slow", "print('slow')", tags=["integration"]),
            ]
        )
        result = self.cli("run", "--tag", "smoke", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c["name"] for c in json.loads(result.stdout)], ["fast"])
        self.assertFalse((self.project / "artifacts").exists())
        for arguments in [
            ("--tag", "typo"),
            ("--check", "typo"),
            ("--tag", "smoke", "--check", "slow"),
            ("--tag", "smoke", "--tag", "typo"),
        ]:
            with self.subTest(arguments=arguments):
                self.assertEqual(self.cli("run", *arguments).returncode, 2)
        result = self.cli("run", "--tag", "smoke")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c["name"] for c in self.report()[0]["checks"]], ["fast"])

    def test_missing_executable_is_reported_as_an_error(self):
        self.config([("missing", ["testkit-this-executable-does-not-exist-82761"], {})])
        self.assertEqual(self.cli("run").returncode, 1)
        report, directory = self.report()
        self.assertEqual(report["checks"][0]["status"], "error")
        self.assertIsNone(report["checks"][0]["exit_code"])
        self.assertEqual(ET.parse(directory / "junit.xml").getroot().attrib["errors"], "1")

    def test_cwd_environment_and_literal_arguments(self):
        child = self.project / "component"
        child.mkdir()
        code = (
            "import json, os, sys; from pathlib import Path; "
            "print(json.dumps([Path.cwd().name, os.environ['KIT_TEST_VALUE'], sys.argv[1:]]))"
        )
        literal = 'space ; & | $() "quoted"'
        self.config(
            [
                ("arguments", ["{python}", "-c", code, literal, ""], {"cwd": "component"}),
            ],
            extra='[env]\nKIT_TEST_VALUE = "global"',
        )
        with (self.project / "testing.toml").open("a", encoding="utf-8") as stream:
            stream.write('[checks.env]\nKIT_TEST_VALUE = "per-check"\n')
        result = self.cli("run", "--python", sys.executable)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report, directory = self.report()
        output = json.loads((directory / report["checks"][0]["log"]).read_text())
        self.assertEqual(output, ["component", "per-check", [literal, ""]])

    def test_timeout_kills_the_command_and_continues(self):
        self.config(
            [
                self.python_check("hang", "import time; time.sleep(20)", timeout=0.2),
                self.python_check("next", "print('still runs')"),
            ]
        )
        started = time.monotonic()
        result = self.cli("run")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLess(time.monotonic() - started, 15)
        report, directory = self.report()
        self.assertEqual([c["status"] for c in report["checks"]], ["timeout", "passed"])
        self.assertEqual(ET.parse(directory / "junit.xml").getroot().attrib["errors"], "1")

    def test_timeout_stops_a_spawned_child(self):
        delayed = self.project / "child-finished"
        child_code = (
            f"import time; from pathlib import Path; time.sleep(3); Path({str(delayed)!r}).touch()"
        )
        parent_code = (
            "import subprocess, sys, time; from pathlib import Path; "
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
            "Path('child-started').touch(); time.sleep(30)"
        )
        self.config([self.python_check("tree", parent_code, timeout=1.5)])
        result = self.cli("run")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue((self.project / "child-started").exists())
        time.sleep(3)
        self.assertFalse(delayed.exists(), "Timed-out check left a child process running")

    def test_repeated_runs_keep_separate_reports(self):
        self.config([self.python_check("test", "print('ok')")])
        self.assertEqual(self.cli("run").returncode, 0)
        self.assertEqual(self.cli("run").returncode, 0)
        self.assertEqual(len(list((self.project / "artifacts/testkit").glob("*/summary.json"))), 2)

    def test_target_virtual_environment_is_preferred(self):
        venv = self.project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        venv.parent.mkdir(parents=True)
        venv.touch()
        self.assertEqual(python_for(self.project, None), str(venv))
        self.assertEqual(
            python_for(self.project, sys.executable), str(Path(sys.executable).resolve())
        )

    def test_invalid_configuration_is_rejected_before_execution(self):
        variants = [
            'version = 2\n[[checks]]\nname = "test"\ncommand = ["echo"]',
            "version = 1\nchecks = []",
            'version = 1\ntimeout = nan\n[[checks]]\nname = "test"\ncommand = ["echo"]',
            'version = 1\ntimeout = true\n[[checks]]\nname = "test"\ncommand = ["echo"]',
            'version = 1\n[[checks]]\nname = "test"\ncommand = "echo hello"',
            'version = 1\n[[checks]]\nname = "test"\ncommand = ["echo"]\ntimeot = 1',
            'version = 1\n[[checks]]\nname = "../escape"\ncommand = ["echo"]',
            'version = 1\n[[checks]]\nname = "test"\ncommand = ["echo"]\ncwd = ".."',
            'version = 1\nartifacts_dir = "../outside"\n[[checks]]\nname = "t"\ncommand = ["echo"]',
            'version = 1\nartifacts_dir = "."\n[[checks]]\nname = "t"\ncommand = ["echo"]',
            'version = 1\n[env]\nVALUE = 42\n[[checks]]\nname = "test"\ncommand = ["echo"]',
            'version = 1\n[[checks]]\nname = "t"\ncommand = ["echo"]\n'
            '[[checks]]\nname = "t"\ncommand = ["echo"]',
        ]
        for content in variants:
            with self.subTest(content=content):
                (self.project / "testing.toml").write_text(content, encoding="utf-8")
                with self.assertRaises(ConfigError):
                    load_config(self.project)
        self.assertFalse((self.project / "artifacts").exists())

    def test_missing_or_malformed_config_has_a_concise_error(self):
        self.assertEqual(self.cli("run").returncode, 2)
        (self.project / "testing.toml").write_text("version = [", encoding="utf-8")
        result = self.cli("validate")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
