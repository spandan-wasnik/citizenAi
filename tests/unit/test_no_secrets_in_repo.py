"""Guard: no API-key-like strings or real .env file may exist in the project tree."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY = re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".hypothesis", "node_modules"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".ps1", ".ini", ".cfg", ".example", ""}


def project_files():
    for p in ROOT.rglob("*"):
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS) and p.suffix in TEXT_SUFFIXES:
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
