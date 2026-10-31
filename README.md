# CitizenAssist AI

Multilingual (English / Hindi / Marathi) public service navigator for Indian citizens: scheme discovery, deterministic eligibility checks, document checklists, verified official links.

**Status: Phase 1 in progress.** See `CHECKLIST.md` for exactly what is done and tested, `docs/ARCHITECTURE.md` for the design, and `docs/DATABASE.md` for the schema.

## Setup on Windows (PowerShell)

> These commands have **not yet been run on Windows** (the code is developed on Linux). If any step fails, paste the error and it becomes a fix.

Use Python **3.13** (or 3.11). Python 3.14 is not recommended yet: ML libraries may not have wheels for it (unverified). Keep the project folder **outside OneDrive**.

```powershell
git clone https://github.com/spandan-wasnik/citizenAi.git
cd citizenAi

py -3.13 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt

Copy-Item .env.example .env        # then edit .env; never commit it

.\scripts\verify_windows.ps1       # environment smoke test
python -m pytest -q                # unit tests
```

## Secrets

API keys go only in `.env` (git-ignored). `.env.example` must keep secret values blank; a test fails if a key-like string appears anywhere in the project.

## LLM modes

`LLM_PROVIDER=none` (default) runs without any LLM. `openai` and `ollama` are selected in `.env`; if the chosen provider is incomplete the app falls back to `none` and reports why.

## Database

```powershell
python scripts/init_db.py          # creates/upgrades data/citizenassist.db and seeds the field registry
```

## Scheme data files

Schemes live in `data/schemes/<scheme_id>.yaml`; copy `data/schemes/_template.yaml` (all values are placeholders)
and fill in only what an official page states. Sources must come from a domain listed in `data/sources/registry.yaml`.

```powershell
python scripts/validate_schemes.py   # check every file, writes nothing
python scripts/seed_database.py      # validate, then load into the database (idempotent)
```

A scheme marked `human_reviewed` in the database is never overwritten by a changed file unless you pass `--force`.
