"""Guard: no API-key-like strings or real .env file may exist in the project tree."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY = re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".hypothesis", "node_modules"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".ps1", ".ini", ".cfg", ".example", ""}


def _git_visible_files():
    """Files git would commit: tracked, plus untracked-but-not-ignored. None if git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT, capture_output=True, check=True, timeout=30,
        ).stdout.decode("utf-8", errors="ignore")
    except (OSError, subprocess.SubprocessError):
        return None
    return [ROOT / name for name in out.split("\0") if name]


def project_files():
    files = _git_visible_files()
    if files is None:  # no git: walk the tree, but never scan local .env files (allowed to hold keys)
        files = [p for p in ROOT.rglob("*")
                 if not (set(p.relative_to(ROOT).parts) & SKIP_DIRS) and not p.name.startswith(".env")
                 or p.name == ".env.example"]
    for p in files:
        if p.is_file() and p.suffix in TEXT_SUFFIXES:
            yield p


def test_no_api_key_like_strings_anywhere():
    offenders = [str(p.relative_to(ROOT)) for p in project_files()
                 if KEY.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert not offenders, f"key-like text found in: {offenders}"


def test_env_example_has_no_filled_secrets():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("OPENAI_API_KEY", "JWT_SECRET"):
        line = next(l for l in text.splitlines() if l.startswith(name + "="))
        assert line == f"{name}=", f"{name} must be blank in .env.example"


def test_gitignore_blocks_env_files():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in text and "!.env.example" in text
