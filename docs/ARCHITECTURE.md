# CitizenAssist AI — Architecture & Plan

Version 0.1 · 2026-10-31 · **Status: design only. No application code exists yet.**

Legend used throughout:

- **[VERIFIED]** — I checked it in this session; what I checked is stated.
- **[TESTED]** — I ran an experiment and saw the result.
- **[MEMORY]** — from my own knowledge, *not* checked this session. Must be confirmed before we depend on it.
- **[DECISION]** — a choice I am making, with the reason.
- **[SPIKE]** — an unknown that a short experiment must resolve before we commit.

---

## 1. Environment findings

| Item | Finding |
|---|---|
| Where I work | A Linux sandbox: 2 CPU cores, 7.8 GB RAM, Python 3.13, Node 22, git. **Not your Windows laptop.** [VERIFIED] |
| Your target | Windows, 16 GB RAM, no GPU assumed. |
| Project state | Folder was empty. No earlier work to preserve. |
| Installed here | pytest, pydantic, uvicorn, onnxruntime. FastAPI, Streamlit, FAISS, sentence-transformers are not installed, but PyPI works: I downloaded wheels for faiss-cpu, sentence-transformers, streamlit, fastapi, chromadb, rank-bm25 and argon2-cffi. [VERIFIED] |
| Blocked from the shell | huggingface.co (proxy returned 403) and government sites (pmkisan.gov.in did not connect). [VERIFIED] |
| Web reading | WebSearch works. WebFetch works only for URLs that came from search results or from you, and obeys robots.txt (it refused igod.gov.in for that reason). [VERIFIED] |
| Your computer | Not linked; the free plan has no computer control. I cannot see or run anything on your laptop. |

**Consequences**

1. **I cannot download embedding models here**, so I cannot measure real retrieval quality in this sandbox. Unit tests will use a clearly labelled deterministic fake embedder. Real-model checks are scripts *you* run on the laptop (`scripts/eval_retrieval.py`, `scripts/verify_windows.ps1`) and paste back. I will not claim a retrieval-quality result I have not seen.
2. **Live link checks run on your laptop**; tests use mocked HTTP.
3. I develop and test on Linux / Python 3.13. Windows behaviour is unproven until you run the smoke script. Code stays compatible with Python 3.11+.
4. I need a way to get the code to you (decision D1 in section 14).

---

## 2. Design principles (the trust contract)

1. **Facts come from the database, never from a model.** The LLM never supplies scheme facts, rules, deadlines or URLs.
2. **Eligibility is deterministic and three-valued** (PASS / FAIL / UNKNOWN). Unknown never counts as pass.
3. **Everything has provenance.** Every rule, document, step and link points to a source document, a locator inside it, and a date.
4. **Unverified data is visibly unverified.** Draft records may be displayed, but their eligibility result is forced to `REQUIRES_OFFICIAL_VERIFICATION`.
5. **Degrade, never fabricate.** No LLM → extractive answers. No embeddings → keyword search only. No network → cached data with its age shown.
6. **Guest-first privacy.** Core features work without an account. Sensitive attributes are optional and consent-gated. We never collect Aadhaar numbers, bank details or passwords.

---

## 3. Technology stack

| Layer | Choice | Why / what I rejected |
|---|---|---|
| Language | Python 3.11 or 3.12 on the laptop; code compatible with 3.11+ | Newest Python often lacks wheels for ML libraries on Windows [MEMORY]. |
| API | FastAPI, Uvicorn, Pydantic v2, pydantic-settings | As specified. |
| DB | SQLite (WAL mode) via SQLAlchemy 2.0, Alembic migrations | PostgreSQL-ready; the only SQLite-specific piece (FTS5) sits behind an interface. |
| Auth | argon2-cffi + PyJWT; **guest mode is the default** | Argon2 wheel confirmed on PyPI. [VERIFIED] |
| Frontend | Streamlit calling the FastAPI backend over HTTP (httpx) | Every button hits a real endpoint. No fake state. |
| Vector search | `faiss-cpu`, exact `IndexFlatIP` on L2-normalised vectors, wrapped in an ID map | **Rejected ChromaDB:** heavy dependency tree, duplicates metadata we already keep in SQLite, and we need joint vector + keyword + SQL filtering anyway. At our scale (thousands of chunks) exact search takes milliseconds, so no approximate index is needed. |
| Keyword search | SQLite **FTS5** with a Devanagari-safe tokenizer | **[TESTED]** — see section 9.3. Replaces `rank_bm25`. |
| Embeddings | `sentence-transformers`, model name configurable, lazy-loaded | See section 10. |
| LLM layer | A thin in-house provider interface (about 100 lines) | **Rejected LangChain:** unnecessary abstraction for a pipeline this size. |
| Parsing | pypdf + pdfplumber (tables), python-docx, BeautifulSoup + lxml, pandas | **Not PyMuPDF by default:** it is AGPL-licensed [MEMORY]. Fine only if you accept AGPL terms. |
| NLP | Hand-built, tested rules and lexicons (en / hi / mr) | **Rejected spaCy:** to my knowledge it has no trained Marathi pipeline [MEMORY]. Rules are also deterministic and testable. |
| HTTP | httpx, with a small in-house backoff helper | Avoids another dependency. |
| Tests | pytest, httpx TestClient, respx (HTTP mocking), hypothesis (boundary tests for rules) | Dev-only dependencies. |
| Requirements split | `requirements.txt` (core, no torch) and `requirements-ml.txt` (torch, sentence-transformers, faiss) | The API, eligibility engine and keyword search keep working if the ML install fails, which is one of your reliability requirements. |

---

## 4. Folder structure

```
citizenassist-ai/
├── app/
│   ├── main.py                    # FastAPI app factory, router wiring
│   ├── config.py                  # pydantic-settings; reads .env
│   ├── api/                       # thin HTTP layer only
│   │   ├── auth.py  schemes.py  eligibility.py  assistant.py
│   │   ├── documents.py  admin.py  health.py
│   ├── core/
│   │   ├── security.py            # argon2, JWT
│   │   ├── exceptions.py          # structured error responses
│   │   ├── logging_config.py      # JSON logs with PII redaction
│   │   ├── rate_limit.py
│   │   └── dependencies.py
│   ├── domain/                    # PURE Python, no I/O: the trusted core
│   │   ├── profile.py             # profile + field registry + provenance
│   │   ├── rules.py               # rule-tree models and operators
│   │   ├── eligibility.py         # three-valued evaluator
│   │   ├── ranking.py             # recommendation scoring
│   │   └── checklist.py           # document checklist resolution
│   ├── database/
│   │   ├── connection.py  models.py  repositories.py
│   ├── nlp/                       # deterministic NLP, no models
│   │   ├── language_detect.py  normalize.py  entity_extractor.py
│   │   ├── intent.py  lexicons/{en,hi,mr}.yaml
│   ├── ai/                        # model-dependent components
│   │   ├── embeddings.py  vector_store.py  keyword_index.py
│   │   ├── retrieval.py  rag_pipeline.py  grounding.py
│   │   └── llm/{base,none,ollama,gemini,openai,hf}.py
│   ├── services/                  # orchestration
│   │   ├── scheme_service.py  eligibility_service.py
│   │   ├── conversation_manager.py   # slot-filling state machine
│   │   ├── checklist_service.py  application_guide.py
│   │   ├── source_verifier.py  document_service.py
│   ├── ingestion/
│   │   ├── loaders/{pdf,web,docx,tabular}.py   # web loader is robots-aware
│   │   ├── cleaner.py  chunker.py  injection_scan.py
│   │   ├── indexer.py  pipeline.py
│   ├── i18n/                      # en.json hi.json mr.json: UI text, rule-explanation
│   │                              #   templates, protected-terms glossary
│   ├── frontend/
│   │   ├── streamlit_app.py  api_client.py  pages/  components/
│   └── utils/
├── data/
│   ├── schemes/*.yaml             # one file per scheme: the knowledge base source of truth
│   ├── sources/registry.yaml      # source registry incl. reuse terms
│   ├── documents/                 # uploaded PDFs (gitignored)
│   ├── processed/                 # (gitignored)
│   └── vector_store/              # FAISS files (gitignored)
├── migrations/                    # Alembic
├── scripts/
│   ├── init_db.py  seed_database.py  validate_schemes.py
│   ├── ingest_documents.py  rebuild_index.py  verify_sources.py
│   ├── eval_retrieval.py          # runs on the laptop with the real model
│   └── verify_windows.ps1         # smoke test for your machine
├── eval/                          # gold sets: en/hi/mr queries, extraction cases, scenarios
├── tests/{unit,integration,e2e,fixtures}/
├── docs/{ARCHITECTURE.md, DATABASE.md}
├── CHECKLIST.md  README.md  .env.example  .gitignore
├── requirements.txt  requirements-ml.txt  pyproject.toml  run.py
```

Differences from your master-prompt structure, and why:

- `domain/` holds the pure eligibility and ranking logic with no database, network or model imports, so it is fast to test and impossible for an AI component to contaminate.
- `nlp/` (rules) is separate from `ai/` (models), so extraction works even when no model loads.
- `i18n/` exists because deterministic outputs (rule explanations, checklist text) are produced from templates in three languages, not machine-translated.
- `eval/` and `migrations/` are new: evaluation sets for measuring quality, and Alembic for schema changes.

---

## 5. System architecture

```
┌──────────────────────── Streamlit UI (httpx client) ────────────────────────┐
│ Dashboard · Assistant · Discover · Eligibility · Checklist · Guide · Admin  │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ HTTP / JSON
┌───────────────────────────────────▼─────────────────────────────────────────┐
│ FastAPI  (auth · rate limit · validation · structured errors · /health)     │
└───────┬─────────────────────┬───────────────────────────┬───────────────────┘
        │                     │                           │
 ┌──────▼──────┐   ┌──────────▼───────────┐   ┌───────────▼────────────┐
 │ nlp/        │   │ services/            │   │ ai/ (optional layer)   │
 │ language,   │──▶│ conversation state   │──▶│ hybrid retrieval, RAG, │
 │ entities,   │   │ machine, orchestrate │   │ grounding checks, LLM  │
 │ intent      │   └──────────┬───────────┘   └───────────┬────────────┘
 └─────────────┘              │                           │
                    ┌─────────▼──────────┐      ┌─────────▼─────────┐
                    │ domain/ (pure)     │      │ FAISS + FTS5      │
                    │ eligibility,       │      │ (chunks, vectors) │
                    │ ranking, checklist │      └─────────┬─────────┘
                    └─────────┬──────────┘                │
                              └──────────┬────────────────┘
                                ┌────────▼─────────┐
                                │ SQLite (SQLAlchemy│  ◀── data/schemes/*.yaml
                                │ + Alembic)        │      (validated, seeded)
                                └──────────────────┘
```

**Request flow: "I am 21, B.Tech, Maharashtra, income 1.5 lakh. Which scholarships?"**

1. `nlp/` detects language, normalises numerals, extracts `age=21`, `education=undergraduate`, `state=MH`, `annual_family_income=150000`, intent = scholarship. Each value is tagged *explicit*.
2. The conversation manager merges these into the session slots.
3. Candidate schemes are selected by SQL filters (category, state, status) plus keyword/semantic relevance.
4. `domain/eligibility` evaluates every candidate against its rule tree. This is deterministic; no model is involved.
5. Ranking orders schemes within each status group; relevance is shown separately from eligibility.
6. The next question is chosen from the missing fields that would change the most candidates' status.
7. Results are rendered from templates in the user's language, with links read from the `links` table and a verification badge.
8. Follow-up questions ("what documents?", "how do I apply?") go to the checklist service or RAG, scoped to the scheme in focus.

---

## 6. Eligibility engine

**Rule model.** Each scheme has a tree of groups. A group has a logic (`AND`, `OR`, `NOT_ANY` for exclusions). A leaf is `(field, operator, value, as_of?, evaluability)`. Fields come from a **registry** (`profile_fields`), so a typo in a data file fails validation instead of silently never matching.

**Three-valued logic** (Kleene):

| Group | Result |
|---|---|
| AND | any FAIL → FAIL; else any UNKNOWN → UNKNOWN; else PASS |
| OR | any PASS → PASS; else any UNKNOWN → UNKNOWN; else FAIL |
| NOT_ANY (exclusion) | any PASS → FAIL (excluded); else any UNKNOWN → UNKNOWN; else PASS |

**Scheme-level outcome**

| Status | When |
|---|---|
| LIKELY INELIGIBLE | eligibility tree FAIL, or an exclusion triggered |
| INSUFFICIENT INFORMATION | no FAIL, at least one UNKNOWN; missing fields are listed |
| REQUIRES OFFICIAL VERIFICATION | all answerable rules PASS but the scheme has `official_only` rules (cannot be self-assessed), or the record is draft / stale / not active, or `rules_complete = false` |
| LIKELY ELIGIBLE | all rules PASS, `rules_complete = true`, record verified and fresh, scheme active |

Every result carries a per-rule trace: rule, your value, official requirement, PASS/FAIL/UNKNOWN, a reason code, and the source locator. Explanations come from i18n templates in en/hi/mr. Every result includes the "preliminary, not government approval" notice.

**Semantic traps the data model must handle** (these are where eligibility engines go wrong):

- *Family* income and *individual* income are different fields; supplying one never answers a rule about the other.
- "Up to ₹2.5 lakh" and "below ₹2.5 lakh" differ (`lte` vs `lt`). Boundary values get explicit tests (hypothesis).
- Age and income are often measured *as of* a cutoff date or financial year, so rules carry `as_of`.
- State of residence, domicile, and state of study are separate fields.
- Exclusions ("not already receiving another scholarship") are usually UNKNOWN until asked, so they become questions rather than assumptions.

**LLMs may *draft* rules from a PDF into a review queue (Phase 6). Only rules a human has marked verified are ever evaluated.**

---

## 7. Conversation workflow

A state machine, not LLM memory:

`INTENT → COLLECTING → RESULTS → FOLLOW_UP`, with `CORRECTION`, `LANGUAGE_CHANGE` and `RESTART` reachable from any state.

- **Slots** hold `{value, provenance: explicit | corrected | inferred, source_message, confidence}`.
- Only *explicit* and *corrected* values feed eligibility. An *inferred* value triggers a confirmation question first.
- Next-question choice is deterministic: ask for the field whose answer would change the status of the most candidate schemes; break ties by asking non-sensitive fields before sensitive ones.
- Already-supplied slots are never re-asked. Users can correct a field ("no, income is 2 lakh"), switch language, restart, or reopen earlier recommendations.

---

## 8. Recommendation ranking

Eligibility is a **gate**, not a weighted term. Schemes are grouped by eligibility status first; scores only order schemes *within* a group, and "likely ineligible" schemes are shown collapsed with the reason.

Within a group, a configurable weighted score (weights in config, each component normalised to 0–1):

`score = w1·semantic_relevance + w2·profile_compatibility + w3·geographic_fit + w4·intent_match + w5·source_quality_and_freshness + w6·information_completeness`

Starting weights follow your master prompt and will be tuned against a labelled scenario set; the numbers are heuristics, not measurements. The UI labels the score "relevance" and states it is **not** a probability of receiving a benefit.

---

## 9. AI / RAG pipeline

### 9.1 Ingestion (Tier policy in section 12)
1. **Load** — admin-uploaded PDF/DOCX/CSV/JSON, curated YAML, or robots-aware web fetch where permitted.
2. **Parse** — text and tables; record extraction status (`ok / partial / failed`) so failures are visible, never silent.
3. **Clean** — strip markup and zero-width/control characters, Unicode NFC, normalise Devanagari digits, dedupe by content hash.
4. **Injection scan** — flag imperative or instruction-like text ("ignore previous instructions", hidden/invisible text) for admin review.
5. **Chunk** — section-aware, about 200–350 tokens. Each chunk is prefixed with `Scheme: <name> | Section: <section>` so eligibility text is never separated from its scheme. Table rows stay with their header. Metadata: scheme_id, section, state, language, source, last_verified.
6. **Embed** — configured model, lazy-loaded, batched, cached on disk by text hash.
7. **Index** — FAISS (vectors) and FTS5 (keywords) written together. Supports incremental add, delete-by-source, full rebuild, and duplicate detection. Only `source_verified` / `human_reviewed` documents are indexed for answering.

### 9.2 Retrieval and answering
1. Detect language; extract entities; build metadata filters (state ∈ {user state, all-India}, scheme active).
2. Dense top-k (FAISS) and keyword top-k (FTS5/BM25) in parallel.
3. Fuse with Reciprocal Rank Fusion, then light deterministic re-ranking (section matches intent, source authority, freshness). **No neural re-ranker by default**; the multilingual ones I know of are too heavy for this laptop [MEMORY].
4. **Evidence gate:** if the best fused score is below a calibrated threshold, or nothing is in scope, answer "I don't have enough verified information" and point to official portals *from the source registry* (Scenario 6). Thresholds are set on the eval set, not guessed.
5. Generate (or fall back to extractive mode). Retrieved text is wrapped as data and the model is told it is untrusted content, not instructions.
6. **Grounding checks** on the output before showing it:
   - every claim cites chunk IDs that exist;
   - every number, rupee amount, age and date in the answer appears in the cited chunks (after numeral normalisation);
   - any URL the model wrote is stripped; links are rendered only from the `links` table;
   - scheme names must exist in the catalogue.
   If a check fails, the response falls back to extractive mode with `requires_verification: true`.
7. **Confidence** is computed from retrieval and evidence quality: normalised top score, coverage of query entities in cited chunks, agreement across independent chunks, source authority tier, freshness decay, grounding result. It is never the LLM's self-report. The API returns the number plus a coarse label (high / medium / low), because a figure like 0.92 implies precision we will not have. Calibration against the eval set is a Phase 3 exit criterion.

Response shape follows your spec: `answer, sources[{title,url,section}], confidence, language, requires_verification`.

### 9.3 Keyword search for Devanagari — a problem found and fixed **[TESTED]**
SQLite's default FTS5 tokenizer splits Hindi and Marathi words at vowel signs (for example, `शेतकरी` became fragments such as `तकर`, `क`, `ट`), which would make keyword search unreliable and produce false matches across Hindi and Marathi. Adding the Devanagari combining marks (U+0900–0903, 093A–093C, 093E–094F, 0951–0957, 0962–0963) as `tokenchars` fixed it: whole words indexed, and Hindi/Marathi/English queries matched only the right documents (SQLite 3.53.1 here).

Remaining caveats:
- Devanagari digits (`६०००`) and ASCII digits (`6000`) are different tokens, so numerals are normalised at index time and query time.
- No stemming: Marathi `कुटुंबांना` will not match `कुटुंब` by keyword alone. Dense retrieval covers this; light suffix stripping is a later option.
- Windows' bundled SQLite must include FTS5. Python's official Windows builds should, but `verify_windows.ps1` will confirm it **[SPIKE]**.

---

## 10. Models

Budget estimate on a 16 GB Windows laptop **[MEMORY, to be measured]**: embedding model about 0.5 GB, FastAPI + Streamlit about 0.5 GB, SQLite/FAISS negligible, optional local LLM 2–3 GB. Only one model is loaded at a time, lazily.

| Role | Default | Alternatives | Notes |
|---|---|---|---|
| Embeddings | `intfloat/multilingual-e5-small` | `BAAI/bge-m3` (larger, about 568M params [MEMORY]); `paraphrase-multilingual-MiniLM-L12-v2` (short 128-token limit [MEMORY], trained for paraphrase rather than retrieval — not recommended) | Model card: MIT licence, lists Hindi and Marathi among 94 languages [VERIFIED]. About 118M params, 384 dimensions, 512 tokens, and the `query:` / `passage:` prefix convention are [MEMORY]; the card page I fetched did not state them, so Phase 3 verifies them. **Chosen by measurement:** an eval set of 60+ en/hi/mr queries; if recall@5 is below the target (proposed 0.85), we try bge-m3. |
| Reranker | none | optional later | Deterministic re-ranking first. |
| LLM | **No-LLM extractive mode is the baseline** | Ollama local small model, Gemini, OpenAI, HF Inference | Every provider sits behind one interface. I have not benchmarked small local models for Marathi; expect weak Marathi generation from small models. Which one to use is a bake-off on your machine **[SPIKE]**. Check each hosted provider's data-use terms before sending any user text. |
| Translation | **None in the critical path** | IndicTrans2 distilled 200M (optional, offline); Bhashini (optional, online) | IndicTrans2 card: about 0.3B params, MIT, needs `trust_remote_code`, the IndicTransToolkit package and accepting gated-access terms [VERIFIED]; the card page did not explicitly list Marathi, though IndicTrans2 targets the 22 scheduled languages [MEMORY]. `trust_remote_code` means running downloaded code, so we would pin an exact revision and review it. Bhashini: my search returned no authoritative page, so availability and terms are unverified. |
| Language ID | Script ratio + hi/mr marker-word lexicons | `lingua` restricted to en/hi/mr if our test set shows gaps | Hindi and Marathi share the Devanagari script, so script alone cannot separate them. Romanised text ("Mala scholarship pahije") needs a lexicon of common romanised words. Target ≥95% on a gold set; otherwise ask the user to confirm the language. **[SPIKE]** |

---

## 11. Multilingual strategy

- Knowledge is stored in English (from official English sources) plus **curated** Hindi/Marathi names, short descriptions, document names and step text where an official translation exists. Anything machine-translated is labelled as such.
- Query path: detect language → extract entities with trilingual lexicons (no translation) → embed the query directly, relying on the multilingual embedding model for cross-language retrieval → answer in the user's language.
- Deterministic content (eligibility explanations, checklist text, UI) comes from en/hi/mr templates, so it is exact and testable.
- A protected-terms glossary keeps scheme names, "Aadhaar", IDs and URLs out of any translation step.
- If language or translation confidence is low, ask the user instead of silently changing meaning.
- Mixed text (Hinglish, Marathi-English) is handled by the lexicon extractor and covered by gold test cases.

---

## 12. Data sources and ingestion policy

What I actually checked this session:

| Source | Finding |
|---|---|
| **myScheme** (myscheme.gov.in) | Terms of use prohibit bots, scrapers and other automatic copying of its pages without written authorization from myScheme. They mention no API. Content carries an accuracy disclaimer and tells users to confirm with the relevant ministry. [VERIFIED: terms page read] |
| **pmkisan.gov.in** | Copyright policy: free reproduction, but permission must first be obtained by email; material must be reproduced faithfully, not in a misleading context, with prominent credit; third-party material excluded. Page dated 17/02/2026. [VERIFIED] |
| **PIB** (pib.gov.in) | Copyright policy: free reproduction without prior approval, third-party material excluded, same faithfulness / no-distortion / credit conditions. [VERIFIED] |
| **igod.gov.in website policies** | My fetch was refused because the site's robots rules disallow it; terms unread. [VERIFIED] |
| **data.gov.in (OGD)** | Per a third-party client's README: an API key is required and the platform has no search API for resources. Licence terms not confirmed. Likely useful for statistics, not scheme rules. [VERIFIED only via that README] |
| **Third-party aggregators** (scholarship blogs and similar) | They dominated my searches for NSP, MahaDBT and Ladki Bahin. They are not authoritative; we use them at most as leads to find official pages, never as the source of a fact. [VERIFIED: observed in results] |
| **Not yet read:** scholarships.gov.in, mahadbt.maharashtra.gov.in, india.gov.in, UMANG, maharashtra.gov.in | You supplied these. Their terms and robots.txt are a Phase 1 task. |
| **Vikaspedia** | Appeared in results with multilingual scheme guides. Ownership and reuse terms unverified; treated as secondary until checked. |

**Policy [DECISION]**

- **Tier A — admin upload.** A person downloads an official PDF or gazette in a browser and uploads it. No automated-access question arises.
- **Tier B — curated YAML.** Facts read from official pages are recorded in our own words, each field with source URL, locator, and retrieval date. No bulk copying of site text.
- **Tier C — automated fetch.** Only for sources whose registry entry says automated access is allowed (or written permission is on file); robots.txt honoured, rate-limited, identifiable User-Agent.
- **Prohibited:** crawling myScheme. If you want it as a source, the proper route is to ask them for written authorization; I can draft that email. Until then it is a place to read and link to, not a feed.

Each registry entry stores its reuse terms, a robots check date, and a permission status, so this is enforced in code rather than remembered.

**Dataset reality.** Verifying a scheme means reading two to four official pages, and some official sites block automated readers. My research is a *draft* until a record is marked `human_reviewed`. Verification states: `draft → source_verified` (I read the official source on a recorded date) `→ human_reviewed` (you confirmed it). Draft and stale records can be shown with a banner but never produce LIKELY ELIGIBLE.

**Volatility.** Some schemes change often. News headlines in my Ladki Bahin search mention e-KYC deadlines and ineligibility declarations; I did not verify the official position. So records carry `volatility` and `next_review_due`, and stale volatile records are downgraded to REQUIRES OFFICIAL VERIFICATION automatically.

**Candidate domains to verify in Phase 1** (from memory, **not verified; the system will not accept a link as official until a verification record exists**): pmjay.gov.in, eshram.gov.in, ncs.gov.in, pmvishwakarma.gov.in, jansuraksha.gov.in, nsap.nic.in, pmaymis.gov.in, mudra.org.in, standupmitra.in. The schemes in your master prompt are candidates, not confirmed-active schemes.

---

## 13. Security and privacy

- **Data minimisation:** guest sessions are held in a server-side session with expiry; accounts are optional. Sensitive fields (gender, social category, disability) are collected only when a rule needs them and only with consent.
- **Never collected:** Aadhaar or other ID numbers, bank details, passwords beyond the account hash.
- **Logging:** JSON logs with a redaction filter (phone, email, 12-digit ID patterns, profile values). Tests assert that sample PII never appears in logs.
- **LLM calls:** only the minimum text, redacted first. The rule-based extractor means most requests need no LLM at all.
- **Auth:** Argon2 hashing, short-lived JWTs, role-based access (`citizen`, `editor`, `admin`); scheme edits are admin-only and every change is written to an append-only audit log.
- **Uploads (Phase 5):** type, size and signature validation; processed in memory or a secure temp file; deleted after use; explicit consent before processing; OCR stays local; we state that recognising a document type does not prove validity.
- **API hardening:** input validation, rate limiting, parameterised queries, safe error messages.
- **Prompt injection:** section 9 (scan at ingestion, data/instruction separation, no tool access for the LLM, output validation).
- **Legal:** the app is guidance, not legal advice or an application agent. India's data-protection law applies to personal data we handle; we design for consent and minimisation, but I am not a lawyer and this needs a proper review before any public launch.

---

## 14. Risks, and open decisions

### Risks

| # | Risk | Mitigation |
|---|---|---|
| 1 | Can't download models or reach government sites from my sandbox | Test doubles in unit tests; real-model and live-link checks as scripts you run; I report only what I have seen |
| 2 | Scheme data goes stale or is volatile | `volatility`, `next_review_due`, last_verified shown to users, automatic downgrade to REQUIRES OFFICIAL VERIFICATION |
| 3 | myScheme can't be scraped; dataset growth is bounded by human effort | Curation workflow, Tier A uploads, honest draft/verified labels; start with 5 schemes, grow to 20+ across phases |
| 4 | Wrong eligibility from subtle semantics (family vs individual income, cutoffs, boundaries) | Field registry, `as_of`, boundary property tests, per-rule source locators |
| 5 | Hindi vs Marathi vs romanised input mis-detected | Lexicon approach, gold set, ask-to-confirm on low confidence |
| 6 | Weak Marathi from small local LLMs | Templates for all critical text; LLM only for prose, behind grounding checks; extractive fallback |
| 7 | Retrieval quality unknown until measured | Eval set with recall@5 / MRR; model swap if below target |
| 8 | Windows install friction (torch, Tesseract) and path/encoding issues | Split requirements, `verify_windows.ps1`, UTF-8 mode, keep the repo outside OneDrive |
| 9 | PDF export of Devanagari (conjunct shaping) is a known pain point in some PDF libraries [MEMORY] | Phase 5 spike: shaping-capable library vs printable HTML "Save as PDF" |
| 10 | OCR for regional-language documents may be inaccurate and install-heavy | Treated as optional; deferred until the core works |
| 11 | Hosted LLM free tiers may have data-use terms that conflict with citizen privacy | Redaction, local-first default, read terms before enabling |
| 12 | Scope: 13 features and 20+ schemes | Phase gates; voice and notifications deferred; nothing marked done without passing tests |

### Decisions I need from you

- **D1 — How should code reach your laptop?** Default: a private GitHub repo that I push to and you `git clone` (you create an empty repo and tell me its name). Alternative: I send a zip per milestone.
- **D2 — Python version on the laptop.** Default: 3.12 (3.11 also fine). Please confirm what you have installed.
- **D3 — LLM.** Default: build extractive (no-LLM) mode first and add a provider after retrieval is measured. Do you have a Gemini or OpenAI key you are happy to use, or do you want a local Ollama model?
- **D4 — Guest-first access.** Default: yes, core features work without login; accounts add saved profiles and checklists.
- **D5 — myScheme.** Optional: I draft an authorization request email to them. Otherwise it stays a read-and-link-only source.
