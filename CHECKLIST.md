# CitizenAssist AI — Development Checklist & Roadmap

Last updated 2026-11-01. A box is ticked **only** when the work exists and its tests have passed. Effort is a rough guess in "sessions" (one focused 2–3 hour working block); with about four sessions a week the whole plan is roughly 8–12 weeks. Treat it as an estimate to be revised after Phase 1.

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
- [~] **1.6b Live checks on your laptop:** robots.txt done (5 of 6 readable). pmkisan.gov.in fails TLS verification (incomplete certificate chain, automated checks skipped on purpose). india.gov.in terms recorded. Still open: terms of scholarships.gov.in and mahadbt.maharashtra.gov.in (only a privacy policy and an unlabelled security policy were supplied), and the terms URL for india.gov.in
- [x] **1.7 Eligibility core (pure three-valued engine, 4 statuses, birthday-aware age as_of, decisive missing fields, English explanations with Indian ₹ formatting, file and DB loaders with identical rules hash; 109 new tests incl. a hypothesis soundness test; hi/mr explanations arrive in Phase 2):** rule tree, evaluator, outcome mapping, per-rule trace
- [x] **1.8 Seed 5 schemes (pm-kisan, atal-pension-yojana, post-matric-scholarship-sc, mukhyamantri-majhi-ladki-bahin, rajarshi-shahu-maharaj-ebc-fee-concession; each from official text the owner supplied, every rule with a source and locator; all deliberately rules_complete=false and status unknown, so none gives a firm verdict yet; 'other conditions' list added, 29th table; 365 tests):** chosen so the rule model is stressed: an income-threshold scholarship, an occupation-based farmer scheme, an age-based pension, a state-specific scheme, a gender-specific scheme. Each gets a verification state and source references.
- [x] **1.9 Basic API (28 tests; also smoke-tested against a live uvicorn server):** `/health`, `/api/v1/fields`, scheme list/search/detail, evaluate one scheme and evaluate all; one structured error shape that never echoes submitted values; 503 when the database is not ready; hi/mr explanations refused honestly until Phase 2; run with `uvicorn app.main:create_app --factory`
- [x] **1.10 Basic Streamlit UI:** scheme browser and detail page showing sources, link verification badges and UNKNOWN fields; no placeholder buttons. Eligibility form built from `/api/v1/fields` (blank = not provided, sensitive questions optional), grouped results, honest English-only notice. 18 tests (helpers, API client, headless AppTest of the real script); also smoke-tested in a real browser; confirmed on Windows: 418 passed, UI viewed by the owner.
  - UI rule from india.gov.in's hyperlink policy: official links open in a new browser tab; never embed their pages in frames/iframes

**Exit criteria:** `pytest` green here; `verify_windows.ps1` passes on your laptop; 5 validated schemes browsable through the real API; every displayed link shows its true verification state.

---

## Phase 2 — Core intelligence  *(about 6–8 sessions)*

- [x] **2.6 i18n catalogs + action renderer** (`app/i18n/`; one JSON per language so a native speaker can review): chat messages, per-field questions, labels, enum value names, all 36 state/UT names in en/hi/mr; `render_turn(actions, lang)`. Tests: identical keys and placeholders in all three languages, no English inside hi/mr turns, every prompt we print (yes/no, skip word, state names) is understood when typed back, reply language is detected back as the language written. State names containing 'and' no longer split clauses; a gender answer of 'other' is understood. **Owner action: review `app/i18n/data/hi.json` and `mr.json`** (some hi/mr sentences have the bot refer to itself with masculine verb forms).
- [x] **2.6b Explanations in Hindi and Marathi** (`app/domain/explain.py` now reads templates from the catalogs): per-rule sentences, status/reason texts, preliminary notice, requirement and exclusion phrasing; API `lang=hi|mr` works on evaluate and on scheme detail (rule tree included). State names and option names are shown instead of codes. Official 'other conditions' exist only in English: they are shown in labelled English (`language_used`), never dropped or machine-translated. Guard tests: no English inside hi/mr explanations, every real scheme's rules read in hi/mr, unknown languages (e.g. fr) still refused. **Owner action: review the `explain` section of `hi.json` / `mr.json`.** Still English: scheme descriptions/documents/steps without a hi/mr translation (flagged by `language_used`), and the Streamlit pages (2.7).
- [x] **2.1 Normalisation** (`app/nlp/normalize.py`): Devanagari digits, lakh/crore/thousand, Indian grouping, nukta/chandrabindu variants, hi/mr number words and डेढ़/ढाई/सवा idioms; ranges and ambiguous input yield nothing. 45 tests. *Word lists need a native-speaker review.*
- [x] **2.2 Entity extractor** (`app/nlp/extract.py`, gold set `eval/extraction_gold.json`, 67 cases): age, gender, state/domicile, family vs individual income, land, bank account, income-tax payer, social category, occupation, enrolment, rural/urban in en/hi/mr. Every value has provenance (explicit/inferred) and evidence; negation, third-person, past tense, ranges, conflicts handled; unclear income becomes a clarification; bare answers accepted only for the question just asked. Regex blow-up found by testing and fixed; input capped at 2000 chars. *Known gaps: romanised Hindi/Marathi ("main 30 saal ki hoon") extracts nothing; pending language detection (2.3).*
- [x] **2.3 Language detection** (`app/nlp/langdetect.py`, `eval/language_gold.json` 54 cases, `python -m scripts.eval_language`): script + marker words, romanised Hindi/Marathi, returns unknown (not a guess) on no evidence or a tie, `strong` flag for switching language. Dev-written gold set: 100% (the lists were extended after the first run showed 96.3%, so this number is optimistic). **Owner action: add 20+ real messages to the gold file and re-run.** Romanised input is detected but not yet *extracted* (needs a transliteration step, 2.3b).
- [x] **2.4 Intent classifier** (`app/nlp/intent.py`, `eval/intent_gold.json` 71 cases): eligibility, discover, documents, how_to_apply, official_link, scheme_info, restart, language_switch (with target), correction, help, thanks, greeting, other; multi-intent reporting; scheme-mention aliases verified against the real scheme files. Dev-written gold set (optimistic). Whether a short message is an *answer* is decided by the conversation layer (2.5), not here.
- [x] **2.5 Conversation engine** (`app/conversation/`, `app/nlp/answers.py`): pure function `handle_message(state, text, evaluator) -> (new_state, actions)`. Explicit/confirmed/inferred answers (inferred never used for eligibility until confirmed), corrections, in-message conflicts, income-whose clarification, skip, restart, language follow/switch, focus on a named scheme, info intents routed without losing the pending question, next question = most schemes waiting (non-sensitive first; sensitive always skippable). State holds values only, never the person's words. 3 full scripted conversations (en/hi/mr) finish without repeating a question; property test over random conversations. Actions are structured, *not yet words*: the renderer comes in 2.6; chat endpoint + UI page in 2.7.
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
