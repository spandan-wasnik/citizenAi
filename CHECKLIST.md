# CitizenAssist AI — Development Checklist & Roadmap

Last updated 2026-10-31. A box is ticked **only** when the work exists and its tests have passed. Effort is a rough guess in "sessions" (one focused 2–3 hour working block); with about four sessions a week the whole plan is roughly 8–12 weeks. Treat it as an estimate to be revised after Phase 1.

Legend: `[x]` done and tested · `[ ]` not started · `[~]` in progress

---

## Phase 0 — Design (this phase)

- [x] Inspect environment, tools and constraints
- [x] Check data-source terms (myScheme, pmkisan.gov.in, PIB, data.gov.in)
- [x] Test Devanagari keyword search in SQLite FTS5 and find the fix
- [x] Architecture (`docs/ARCHITECTURE.md`)
- [x] Database design (`docs/DATABASE.md`); schema sketch loaded in SQLite and 8 constraint rejections confirmed
- [x] Roadmap (this file)
- [ ] Your answers to decisions D1–D5 (`docs/ARCHITECTURE.md`, section 14)

---

## Phase 1 — Foundation  *(about 5–6 sessions)*

Goal: a working skeleton on solid ground, with the riskiest data-model decision (rules) validated *before* we invest in data entry.

- [x] **1.1 Scaffold:** repo layout, `pyproject.toml`, `requirements.txt` / `requirements-ml.txt`, `.env.example`, `.gitignore`, `config.py`
- [x] **1.2 Logging with PII redaction**, plus tests that sample PII never reaches logs
- [x] **1.3 `scripts/verify_windows.ps1` (passed on your Windows laptop 2026-10-31: Python 3.13.14, SQLite 3.50.4 FTS5 OK; only 0.7 GB RAM was free):** checks Python version, UTF-8 mode, SQLite FTS5 on Windows, required imports, free RAM. *You run it and paste the output.*
- [x] **1.4 Database (28 tables, FTS5 keyword index, append-only audit log, 20 tests incl. migration/model drift check):** SQLAlchemy models, Alembic initial migration, seeded `profile_fields` registry; tests for foreign keys and CHECK constraints
- [x] **1.5 Scheme data format (schema, source registry, validator, idempotent loader with human_reviewed protection; 73 tests; no real scheme data yet, that is 1.8):** Pydantic schema for `data/schemes/*.yaml`, `validate_schemes.py`, idempotent seed loader; tests rejecting unsourced rules, unknown fields, and bad enum values; UNKNOWN handled explicitly
- [x] **1.6 Link classifier + robots-aware link checker** (suspicious/look-alike detection, robots.txt per RFC 9309, throttle, backoff, redirect safety, registry policy; 78 tests, all with mocked HTTP)
- [~] **1.6b Live run on your laptop (robots.txt done 2026-10-31: 5 of 6 sites readable and root allowed; pmkisan.gov.in unreachable, reason still to be diagnosed; terms pages of scholarships/mahadbt/india.gov.in still to be read):** `python scripts/verify_sources.py --robots`, then read the terms of scholarships.gov.in, mahadbt.maharashtra.gov.in and india.gov.in and update `data/sources/registry.yaml`
- [ ] **1.7 Eligibility core (pulled forward from Phase 2):** rule tree, three-valued evaluator, outcome mapping, per-rule trace; hypothesis boundary tests
- [ ] **1.8 Seed 5 schemes**, chosen so the rule model is stressed: an income-threshold scholarship, an occupation-based farmer scheme, an age-based pension, a state-specific scheme, a gender-specific scheme. Each gets a verification state and source references.
- [ ] **1.9 Basic API:** `/health`, scheme list/search/detail, evaluate-eligibility endpoint; structured errors; tests (success, invalid input, missing record)
- [ ] **1.10 Basic Streamlit UI:** scheme browser and detail page showing sources, link verification badges and UNKNOWN fields; no placeholder buttons

**Exit criteria:** `pytest` green here; `verify_windows.ps1` passes on your laptop; 5 validated schemes browsable through the real API; every displayed link shows its true verification state.

---

## Phase 2 — Core intelligence  *(about 6–8 sessions)*

- [ ] i18n catalog (en/hi/mr): rule-explanation templates, UI text, protected-terms glossary
- [ ] Normalisation: Devanagari digits, lakh/crore/thousand, Indian number formats, spelling variants
- [ ] Entity extractor (rules + trilingual lexicons) with provenance (explicit vs inferred)
- [ ] Language detection (script + marker words, romanised Hindi/Marathi lexicon); gold-set accuracy reported
- [ ] Intent classifier (rule-based first)
- [ ] Conversation state machine: slots, corrections, restart, language switch, next-question selection
- [ ] Ranking (gate by status, weighted score within group), explanations
- [ ] Gold sets in `eval/`: extraction cases (en/hi/mr/mixed/ambiguous), end-to-end scenarios 1–4
- [ ] Dataset track: grow to ~12 schemes

**Exit criteria:** all extraction and eligibility test cases from your spec pass; scenarios 1–3 produce correct, source-referenced output through the API.

---

## Phase 3 — RAG  *(about 5–7 sessions)*

- [ ] Loaders (PDF, HTML, DOCX, CSV/JSON), cleaner, injection scan, chunker
- [ ] Embedding wrapper (lazy load, disk cache, model recorded in `index_state`), deterministic fake embedder for tests only
- [ ] FAISS store + FTS5 index: incremental add, delete-by-source, rebuild, duplicate detection
- [ ] Hybrid retrieval with RRF and deterministic re-ranking
- [ ] LLM provider interface + extractive (no-LLM) mode; then Ollama / Gemini / OpenAI adapters
- [ ] Grounding checks (citations, numbers, URLs, scheme names); evidence gate; computed confidence
- [ ] **Eval set (60+ queries en/hi/mr)** and `scripts/eval_retrieval.py`; *you run it with the real model* and we pick the embedding model from the numbers (proposed target: recall@5 ≥ 0.85)
- [ ] Tests: relevant, irrelevant, no-evidence, conflicting sources, outdated source, cross-language, prompt injection
- [ ] Dataset track: reach 20+ schemes (statuses honestly labelled)

**Exit criteria:** every RAG test case from your spec passes; measured retrieval numbers on your machine are recorded in this file; confidence calibration table recorded.

---

## Phase 4 — Multilingual completion  *(about 3–5 sessions)*

- [ ] Hindi and Marathi end-to-end flows through assistant and checklist output
- [ ] Mixed-language and romanised inputs against the gold set
- [ ] Response in the user's language (templates for deterministic parts; LLM prose behind grounding checks)
- [ ] Low-confidence handling: ask instead of guessing
- [ ] Optional: IndicTrans2 / Bhashini adapters for free-text translation, labelled machine-translated

**Exit criteria:** scenario 4 passes in Marathi and Hindi; language-detection accuracy on the gold set meets the stated target.

---

## Phase 5 — Documents  *(about 4–6 sessions)*

- [ ] Checklist service: mandatory / conditional / optional, personalised, with reasons; saved progress
- [ ] PDF export. **Spike first:** Devanagari shaping in the chosen library vs printable HTML
- [ ] Optional, after the core works: upload + OCR + document-type classification, with explicit consent, local processing and deletion

**Exit criteria:** scenario 5 passes; exported PDFs verified visually for Hindi and Marathi text.

---

## Phase 6 — Administration  *(about 4–5 sessions)*

- [ ] Roles and admin-only endpoints; append-only audit log
- [ ] Scheme and rule editor with verification workflow (draft → source_verified → human_reviewed)
- [ ] PDF upload, ingestion trigger, index rebuild, extraction-error review, injection-flag review
- [ ] Source and link verification runs; outdated-record report
- [ ] Analytics without personal data

**Exit criteria:** an admin can add a new scheme and rules with no code changes, and the audit log shows who changed what.

---

## Phase 7 — Hardening  *(about 4–6 sessions)*

- [ ] Rate limiting, upload validation, secrets handling review, safe error messages
- [ ] Failure-mode tests: LLM down, site down, bad PDF, model fails to load, empty vector store, DB error, unsupported language
- [ ] Performance and memory measurement on your laptop
- [ ] All six demonstration scenarios run end to end
- [ ] README with exact PowerShell instructions, sample accounts, demo queries, known limitations, future roadmap

**Exit criteria:** every item in your "Definition of Done" is either demonstrably working or listed under known limitations.

---

## Explicitly deferred until the core is proven

Voice input/output, notifications, scheme comparison view, PostgreSQL switch, additional states or languages.

## Dataset track (runs across Phases 1–3)

| Milestone | Schemes | Status rule |
|---|---|---|
| End of Phase 1 | 5 | Each labelled draft / source_verified / human_reviewed |
| End of Phase 2 | about 12 | same |
| End of Phase 3 | 20+ | same |

A scheme counts as "verified" only if every rule, document, step and link has a source reference and a retrieval date. Unknown fields stay UNKNOWN.
