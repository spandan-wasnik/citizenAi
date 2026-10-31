import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_environment.py"


def load():
    spec = importlib.util.spec_from_file_location("verify_environment", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses needs the module registered
    spec.loader.exec_module(mod)
    return mod


def test_checks_run_and_have_valid_statuses():
    checks = load().run_checks()
    assert checks
    assert {c.status for c in checks} <= {"PASS", "WARN", "FAIL"}


def test_core_environment_has_no_failures_here():
    failed = [c for c in load().run_checks() if c.status == "FAIL"]
    assert not failed, failed


def test_python_check_by_version():
    mod = load()
    assert mod.check_python((3, 10, 9)).status == "FAIL"
    assert mod.check_python((3, 11, 0)).status == "PASS"
    assert mod.check_python((3, 13, 1)).status == "PASS"
    assert mod.check_python((3, 14, 2)).status == "WARN"


def test_missing_core_package_is_a_failure(monkeypatch):
    mod = load()
    monkeypatch.setattr(mod, "_installed", lambda imp, pip: None)
    assert all(c.status == "FAIL" for c in mod.check_core_packages())
    assert all(c.status == "WARN" for c in mod.check_ml_packages())


def test_script_runs_as_a_program_and_prints_ascii():
    proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, cwd=ROOT, timeout=60)
    out = proc.stdout.decode("ascii")  # raises if any non-ASCII slipped into the report
    assert "passed" in out
    assert proc.returncode == 0
