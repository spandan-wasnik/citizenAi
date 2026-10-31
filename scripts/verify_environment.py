"""Environment smoke test for CitizenAssist AI. Run from the project root:

    python scripts/verify_environment.py

Prints PASS / WARN / FAIL per check and exits non-zero if any check FAILs.
Output is ASCII-only so it renders in any Windows console.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.utils.devanagari import fts5_tokenizer_spec, normalize_text  # noqa: E402

CORE_PACKAGES = {  # import name -> pip name
    "fastapi": "fastapi", "uvicorn": "uvicorn", "pydantic": "pydantic",
    "pydantic_settings": "pydantic-settings", "sqlalchemy": "sqlalchemy",
    "alembic": "alembic", "httpx": "httpx", "yaml": "pyyaml",
}
ML_PACKAGES = {"sentence_transformers": "sentence-transformers", "faiss": "faiss-cpu", "torch": "torch"}


@dataclass
class Check:
    name: str
    status: str  # PASS | WARN | FAIL
    detail: str


def check_python(version: tuple[int, int, int] | None = None) -> Check:
    major, minor, micro = version or sys.version_info[:3]
    text = f"{major}.{minor}.{micro}"
    if (major, minor) < (3, 11):
        return Check("python version", "FAIL", f"{text}: need 3.11 or newer")
    if (major, minor) >= (3, 14):
        return Check("python version", "WARN", f"{text}: ML libraries may lack wheels for this "
                     "version (unverified). Prefer 3.13 or 3.11 for this project")
    return Check("python version", "PASS", text)


def check_utf8() -> Check:
    enc = (sys.stdout.encoding or "").lower()
    if sys.flags.utf8_mode or "utf-8" in enc or "utf8" in enc:
        return Check("utf-8 mode", "PASS", f"stdout encoding={enc or 'unknown'}, utf8_mode={sys.flags.utf8_mode}")
    return Check("utf-8 mode", "WARN", f"stdout encoding={enc or 'unknown'}; set PYTHONUTF8=1 for Hindi/Marathi text")


def check_sqlite_fts5() -> Check:
    try:
        con = sqlite3.connect(":memory:")
        con.execute(f"create virtual table t using fts5(body, tokenize=\"{fts5_tokenizer_spec()}\")")
        docs = ["शेतकरी कुटुंबांना ६००० रुपये", "किसान परिवारों को 6000 रुपये", "Farmer families 6000"]
        for d in docs:
            con.execute("insert into t values (?)", (normalize_text(d),))
        con.execute("create virtual table v using fts5vocab(t, 'row')")
        vocab = {r[0] for r in con.execute("select term from v")}
        hits = [r[0] for r in con.execute("select rowid from t where t match ? order by rowid",
                                          ('"%s"' % normalize_text("शेतकरी"),))]
        digits = con.execute("select count(*) from t where t match '\"6000\"'").fetchone()[0]
    except sqlite3.Error as exc:
        return Check("sqlite FTS5 + Devanagari", "FAIL", f"sqlite {sqlite3.sqlite_version}: {exc}")
    ok = "शेतकरी" in vocab and "तकर" not in vocab and hits == [1] and digits == 3
    detail = f"sqlite {sqlite3.sqlite_version}; whole-word tokens={'yes' if ok else 'NO'}"
    return Check("sqlite FTS5 + Devanagari", "PASS" if ok else "FAIL", detail)


def _installed(import_name: str, pip_name: str) -> str | None:
    if importlib.util.find_spec(import_name) is None:
        return None
    try:
        return importlib.metadata.version(pip_name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def check_core_packages() -> list[Check]:
    out = []
    for imp, pip in CORE_PACKAGES.items():
        ver = _installed(imp, pip)
        out.append(Check(f"package {pip}", "PASS" if ver else "FAIL",
                         ver or "missing: pip install -r requirements.txt"))
    return out


def check_ml_packages() -> list[Check]:
    out = []
    for imp, pip in ML_PACKAGES.items():
        ver = _installed(imp, pip)
        out.append(Check(f"optional {pip}", "PASS" if ver else "WARN",
                         ver or "not installed (needed from Phase 3: requirements-ml.txt)"))
    return out


def available_ram_gb() -> float | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            return stat.ullAvailPhys / 1024**3
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024**2
    except Exception:  # noqa: BLE001 - a diagnostic must never crash
        return None
    return None


def check_memory() -> Check:
    gb = available_ram_gb()
    if gb is None:
        return Check("available RAM", "WARN", "could not read")
    if gb < 4:
        return Check("available RAM", "WARN", f"{gb:.1f} GB free: close other apps before loading models")
    return Check("available RAM", "PASS", f"{gb:.1f} GB free")


def check_location() -> Check:
    if "onedrive" in str(ROOT).lower():
        return Check("project location", "WARN", "inside OneDrive: sync can corrupt SQLite/FAISS files; move it")
    return Check("project location", "PASS", "not inside OneDrive")


def run_checks() -> list[Check]:
    return [check_python(), check_utf8(), check_sqlite_fts5(), *check_core_packages(),
            *check_ml_packages(), check_memory(), check_location()]


def main() -> int:
    results = run_checks()
    width = max(len(c.name) for c in results)
    for c in results:
        print(f"[{c.status:4}] {c.name.ljust(width)}  {c.detail}")
    counts = {s: sum(c.status == s for c in results) for s in ("PASS", "WARN", "FAIL")}
    print(f"\n{counts['PASS']} passed, {counts['WARN']} warnings, {counts['FAIL']} failed")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
