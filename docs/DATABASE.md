# CitizenAssist AI — Database Design

Version 0.1 · 2026-10-31 · Design only. Implemented with SQLAlchemy 2.0 models and Alembic migrations in Phase 1.

**Checked:** the SQL below (with `CREATE TABLE` added to the shorthand) loads cleanly in SQLite as 28 tables, and 8 constraint cases were confirmed rejected: state scheme without a state, central scheme with a state, rule without a source, rule with an unregistered field, rule with invalid JSON, conditional document without a condition group, `verified_manual` link with no verifier, and an invalid assessment result. Not yet covered: triggers, indexes and the FTS5 table (created in the real migration).

## Conventions

- SQLite now (WAL mode, `PRAGMA foreign_keys=ON`), PostgreSQL later. Types below are logical; JSON columns are `TEXT` with `CHECK (json_valid(col))` in SQLite and `JSONB` in PostgreSQL.
- Scheme IDs are readable slugs (`pm-kisan`), not integers, so data files and URLs stay stable.
- Enumerations are enforced with `CHECK` constraints. Profile field keys are enforced by a foreign key to a registry table.
- Timestamps are UTC ISO-8601. Dates that come from official sources are `DATE`.
- FTS5 is SQLite-specific and sits behind a `KeywordIndex` interface; PostgreSQL would use `tsvector` behind the same interface.

## 1. Identity and privacy

```sql
users(
  user_id        INTEGER PRIMARY KEY,
  email          TEXT NOT NULL UNIQUE,
  display_name   TEXT,
  password_hash  TEXT NOT NULL,                 -- Argon2
  role           TEXT NOT NULL DEFAULT 'citizen'
                 CHECK (role IN ('citizen','editor','admin')),
  preferred_language TEXT NOT NULL DEFAULT 'en'
                 CHECK (preferred_language IN ('en','hi','mr')),
  is_active      INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  deleted_at     TEXT
);

consents(
  consent_id   INTEGER PRIMARY KEY,
  user_id      INTEGER NOT NULL REFERENCES users ON DELETE CASCADE,
  purpose      TEXT NOT NULL CHECK (purpose IN
               ('store_profile','store_sensitive_attributes',
                'store_history','process_documents')),
  granted      INTEGER NOT NULL,
  policy_version TEXT NOT NULL,
  granted_at   TEXT, revoked_at TEXT
);

profile_fields(                                  -- canonical registry of fields rules may use
  field_key    TEXT PRIMARY KEY,                 -- e.g. annual_family_income, age_years, state_code
  value_type   TEXT NOT NULL CHECK (value_type IN
               ('int','money_inr','enum','bool','string','date')),
  enum_values  TEXT,                             -- JSON list when value_type='enum'
  is_sensitive INTEGER NOT NULL DEFAULT 0,
  description  TEXT NOT NULL
);

user_profiles(
  user_id            INTEGER PRIMARY KEY REFERENCES users ON DELETE CASCADE,
  age_years          INTEGER CHECK (age_years BETWEEN 0 AND 120),
  state_code         TEXT,                       -- ISO 3166-2:IN, e.g. 'MH'
  district           TEXT,
  occupation         TEXT,
  education_level    TEXT,
  employment_status  TEXT,
  annual_family_income     INTEGER CHECK (annual_family_income >= 0),   -- INR
  annual_individual_income INTEGER CHECK (annual_individual_income >= 0),
  family_size        INTEGER,
  gender             TEXT,                       -- sensitive: needs consent
  social_category    TEXT,                       -- sensitive: needs consent
  disability_status  TEXT,                       -- sensitive: needs consent
  extra              TEXT CHECK (json_valid(extra)),
  updated_at         TEXT NOT NULL
);
```

Guests have no row here: their profile lives in `conversations.slots` and expires.

## 2. Knowledge base

```sql
schemes(
  scheme_id     TEXT PRIMARY KEY,                -- slug
  level         TEXT NOT NULL CHECK (level IN ('central','state','ut','local')),
  state_code    TEXT,                            -- NULL for central
  ministry      TEXT, department TEXT,
  status        TEXT NOT NULL DEFAULT 'unknown'
                CHECK (status IN ('active','closed','suspended','unknown')),
  volatility    TEXT NOT NULL DEFAULT 'stable' CHECK (volatility IN ('stable','volatile')),
  verification_status TEXT NOT NULL DEFAULT 'draft'
                CHECK (verification_status IN ('draft','source_verified','human_reviewed')),
  rules_complete INTEGER NOT NULL DEFAULT 0,     -- 1 only when all eligibility conditions are modelled
  last_verified DATE, next_review_due DATE,
  application_open_date DATE, application_deadline DATE,   -- NULL = not officially stated
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  CHECK ((level='central' AND state_code IS NULL) OR (level<>'central' AND state_code IS NOT NULL))
);

scheme_translations(
  scheme_id    TEXT NOT NULL REFERENCES schemes ON DELETE CASCADE,
  lang         TEXT NOT NULL CHECK (lang IN ('en','hi','mr')),
  official_name TEXT NOT NULL,
  short_description TEXT, benefits TEXT, target_beneficiaries TEXT,
  origin       TEXT NOT NULL CHECK (origin IN ('official','curated','machine')),
  PRIMARY KEY (scheme_id, lang)
);

scheme_tags(scheme_id TEXT REFERENCES schemes ON DELETE CASCADE, tag TEXT,
            PRIMARY KEY (scheme_id, tag));     -- scholarship, agriculture, pension, ...
```

Any field that is not officially known is stored as `NULL` and rendered as UNKNOWN in the UI, never guessed.

## 3. Eligibility rules (tree)

```sql
rule_groups(
  group_id        INTEGER PRIMARY KEY,
  scheme_id       TEXT NOT NULL REFERENCES schemes ON DELETE CASCADE,
  parent_group_id INTEGER REFERENCES rule_groups ON DELETE CASCADE,
  logic           TEXT NOT NULL CHECK (logic IN ('AND','OR','NOT_ANY')),
  purpose         TEXT NOT NULL CHECK (purpose IN ('eligibility','exclusion','conditional')),
  description     TEXT,
  source_doc_id   INTEGER REFERENCES source_documents,
  source_locator  TEXT                            -- e.g. 'para 4.1', 'page 3'
);

rules(
  rule_id        INTEGER PRIMARY KEY,
  group_id       INTEGER NOT NULL REFERENCES rule_groups ON DELETE CASCADE,
  field_key      TEXT NOT NULL REFERENCES profile_fields,
  operator       TEXT NOT NULL CHECK (operator IN
                 ('eq','neq','in','not_in','gte','lte','gt','lt','between','is_true','is_false')),
  value          TEXT NOT NULL CHECK (json_valid(value)),
  as_of_date     DATE,
  evaluability   TEXT NOT NULL DEFAULT 'auto' CHECK (evaluability IN ('auto','official_only')),
  source_doc_id  INTEGER NOT NULL REFERENCES source_documents,   -- mandatory: no unsourced rule
  source_locator TEXT NOT NULL,
  rule_status    TEXT NOT NULL DEFAULT 'draft' CHECK (rule_status IN ('draft','verified'))
);
```

The evaluator reads only `verified` rules. `source_doc_id` is `NOT NULL`, so a rule without a source cannot be stored.

## 4. Documents, steps, links

```sql
document_catalog(document_code TEXT PRIMARY KEY);              -- income_certificate, ...
document_translations(document_code TEXT REFERENCES document_catalog,
  lang TEXT, name TEXT NOT NULL, explanation TEXT, PRIMARY KEY (document_code, lang));

scheme_documents(
  scheme_id TEXT REFERENCES schemes ON DELETE CASCADE,
  document_code TEXT REFERENCES document_catalog,
  requirement_type TEXT NOT NULL CHECK (requirement_type IN ('mandatory','conditional','optional')),
  condition_group_id INTEGER REFERENCES rule_groups,            -- who it applies to
  purpose_note TEXT,
  source_doc_id INTEGER NOT NULL REFERENCES source_documents, source_locator TEXT NOT NULL,
  PRIMARY KEY (scheme_id, document_code),
  CHECK (requirement_type <> 'conditional' OR condition_group_id IS NOT NULL)
);

application_steps(
  step_id INTEGER PRIMARY KEY,
  scheme_id TEXT NOT NULL REFERENCES schemes ON DELETE CASCADE,
  lang TEXT NOT NULL, step_number INTEGER NOT NULL, instruction TEXT NOT NULL,
  link_id INTEGER REFERENCES links,
  source_doc_id INTEGER NOT NULL REFERENCES source_documents, source_locator TEXT NOT NULL,
  UNIQUE (scheme_id, lang, step_number)
);

links(
  link_id INTEGER PRIMARY KEY,
  scheme_id TEXT NOT NULL REFERENCES schemes ON DELETE CASCADE,
  url TEXT NOT NULL, domain TEXT NOT NULL,
  link_type TEXT NOT NULL CHECK (link_type IN
            ('application','information','status_tracking','helpline','guidelines')),
  domain_class TEXT NOT NULL CHECK (domain_class IN
            ('gov_in','nic_in','state_gov','official_other_verified','unrecognized','suspicious')),
  verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK (verification_status IN
            ('verified_manual','auto_checked','unverified','broken','redirected_unverified','suspicious')),
  http_status INTEGER, final_url TEXT, redirect_chain TEXT,
  last_checked_at TEXT, verified_by TEXT, verified_at TEXT, notes TEXT,
  UNIQUE (scheme_id, url, link_type),
  CHECK (verification_status <> 'verified_manual' OR verified_by IS NOT NULL)
);
link_checks(check_id INTEGER PRIMARY KEY, link_id INTEGER REFERENCES links ON DELETE CASCADE,
  checked_at TEXT NOT NULL, http_status INTEGER, final_url TEXT, outcome TEXT NOT NULL);
```

A successful HTTP response alone never sets `verified_manual`; only a named person does. Application links shown to users come exclusively from this table.

## 5. Sources and RAG storage

```sql
sources(                                          -- the registry: one row per site/organisation
  source_id INTEGER PRIMARY KEY,
  domain TEXT NOT NULL UNIQUE, organization TEXT,
  source_type TEXT NOT NULL CHECK (source_type IN
    ('official_portal','ministry_page','gazette_pdf','guidelines_pdf','secondary_reference')),
  authority_tier INTEGER NOT NULL CHECK (authority_tier BETWEEN 1 AND 3),
  terms_url TEXT,
  reuse_policy TEXT NOT NULL DEFAULT 'unknown' CHECK (reuse_policy IN
    ('permission_required','free_with_attribution','automated_prohibited','unknown')),
  automated_access TEXT NOT NULL DEFAULT 'unknown' CHECK (automated_access IN
    ('allowed','written_permission_on_file','prohibited','unknown')),
  robots_checked_at TEXT
);

source_documents(
  doc_id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES sources,
  url TEXT NOT NULL UNIQUE, title TEXT,
  doc_type TEXT NOT NULL CHECK (doc_type IN ('html','pdf','docx','json','csv','curated_note')),
  language TEXT, content_hash TEXT, local_path TEXT,
  retrieved_at TEXT NOT NULL,
  retrieval_method TEXT NOT NULL CHECK (retrieval_method IN
    ('manual_curation','admin_upload','automated_fetch')),
  http_status INTEGER,
  extraction_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (extraction_status IN ('ok','partial','failed','pending')),
  extraction_notes TEXT,
  injection_flagged INTEGER NOT NULL DEFAULT 0
);

chunks(
  chunk_id INTEGER PRIMARY KEY,                   -- also the FAISS vector id
  doc_id INTEGER NOT NULL REFERENCES source_documents ON DELETE CASCADE,
  scheme_id TEXT REFERENCES schemes ON DELETE SET NULL,
  section TEXT, language TEXT, text TEXT NOT NULL, text_hash TEXT NOT NULL,
  token_count INTEGER, embedding_model TEXT, indexed_at TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  UNIQUE (doc_id, text_hash)
);
-- FTS5 virtual table over chunks.text:
--   tokenize = "unicode61 tokenchars '<Devanagari combining marks>'"
-- Digits are normalised before indexing and querying.

index_state(index_name TEXT PRIMARY KEY, embedding_model TEXT NOT NULL, dim INTEGER NOT NULL,
            built_at TEXT, chunk_count INTEGER, checksum TEXT);
ingestion_jobs(job_id INTEGER PRIMARY KEY, doc_id INTEGER REFERENCES source_documents,
  status TEXT NOT NULL, started_at TEXT, finished_at TEXT, error_summary TEXT, stats TEXT);
```

`index_state` records which embedding model built the index; if the configured model changes, the app refuses to mix vectors and asks for a rebuild.

## 6. Conversations, assessments, progress

```sql
conversations(
  conversation_id TEXT PRIMARY KEY,               -- random UUID
  user_id INTEGER REFERENCES users ON DELETE CASCADE,   -- NULL for guests
  language TEXT NOT NULL, state TEXT NOT NULL,
  slots TEXT NOT NULL CHECK (json_valid(slots)),  -- {field:{value,provenance,confidence}}
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL
);

messages(message_id INTEGER PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user','assistant')),
  text_redacted TEXT NOT NULL, created_at TEXT NOT NULL);   -- stored only with store_history consent

assessments(
  assessment_id INTEGER PRIMARY KEY,
  conversation_id TEXT REFERENCES conversations ON DELETE CASCADE,
  user_id INTEGER REFERENCES users ON DELETE CASCADE,
  scheme_id TEXT NOT NULL REFERENCES schemes,
  result TEXT NOT NULL CHECK (result IN ('LIKELY_ELIGIBLE','LIKELY_INELIGIBLE',
         'INSUFFICIENT_INFORMATION','REQUIRES_OFFICIAL_VERIFICATION')),
  missing_fields TEXT, engine_version TEXT NOT NULL, rules_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
assessment_rule_results(
  assessment_id INTEGER REFERENCES assessments ON DELETE CASCADE,
  rule_id INTEGER REFERENCES rules,
  user_value TEXT,                                -- only the value used; see privacy note
  result TEXT NOT NULL CHECK (result IN ('PASS','FAIL','UNKNOWN','OFFICIAL_ONLY')),
  reason_code TEXT NOT NULL,
  PRIMARY KEY (assessment_id, rule_id)
);

saved_schemes(user_id INTEGER REFERENCES users ON DELETE CASCADE,
  scheme_id TEXT REFERENCES schemes, saved_at TEXT NOT NULL, PRIMARY KEY (user_id, scheme_id));
checklist_progress(user_id INTEGER REFERENCES users ON DELETE CASCADE,
  scheme_id TEXT, document_code TEXT, is_completed INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, scheme_id, document_code),
  FOREIGN KEY (scheme_id, document_code) REFERENCES scheme_documents);
```

`rules_hash` plus `engine_version` make an old assessment reproducible: you can tell exactly which rules produced it.

## 7. Audit and analytics

```sql
audit_log(                                        -- must be append-only: UPDATE/DELETE-blocking triggers
                                                  -- are NOT in this sketch; they are added in the Phase 1/6 migration and tested
  audit_id INTEGER PRIMARY KEY, actor_user_id INTEGER, action TEXT NOT NULL,
  entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
  before_json TEXT, after_json TEXT, created_at TEXT NOT NULL);

search_events(event_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
  language TEXT, intent TEXT, category TEXT, result_count INTEGER);
-- No query text, no user id, no profile values: safe for admin analytics.
```

## 8. Indexes

`schemes(level, state_code, status)`, `rules(group_id)`, `rule_groups(scheme_id)`, `scheme_documents(scheme_id)`, `application_steps(scheme_id, lang)`, `links(scheme_id)`, `chunks(scheme_id, is_active)`, `chunks(doc_id)`, `conversations(expires_at)`, `assessments(user_id, created_at)`, `audit_log(entity_type, entity_id)`.

## 9. Retention and deletion

- Guest conversations expire (`expires_at`, default 24 hours) and a cleanup job deletes them.
- Message text is stored only with `store_history` consent and only after redaction.
- Deleting a user cascades to profile, consents, conversations, assessments, saved schemes and checklist progress.
- Assessments store only the profile values that rules actually used.
- Uploaded documents are not stored by default (Phase 5); any retained file has an owner and a delete endpoint.

## 10. Changes from the master-prompt schema, and why

| Master prompt | Here | Reason |
|---|---|---|
| Flat `ELIGIBILITY_RULES` with `condition_group` | `rule_groups` tree + `rules` | Real schemes need nested AND/OR and exclusions |
| Free-text `field` | `profile_fields` registry with FK | Typos fail loudly instead of silently never matching |
| `DOCUMENT_REQUIREMENTS` with names per scheme | `document_catalog` + `scheme_documents` + translations | One definition per document, three languages, cross-scheme checklists |
| Single `SOURCES` table | `sources` (registry with reuse terms) + `source_documents` + `links` + `link_checks` | Separates who we may read, what we read, and what we link; keeps verification history |
| `application_url` on `schemes` | `links` table only | URL type, domain class and verification state per link |
| Scheme name/description columns | `scheme_translations` | en/hi/mr with origin label (official / curated / machine) |
| — | `rules_complete`, `volatility`, `verification_status`, `rule_status` | Honest handling of partial and stale data |
| — | `consents`, `audit_log`, `search_events` | Privacy, admin accountability, PII-free analytics |
