import json
import sqlite3

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic import command
from alembic.config import Config
from sqlalchemy import exc, inspect, text

from app.database.connection import make_engine, session_scope
from app.database.models import Base
from app.database.profile_fields import PROFILE_FIELDS, seed_profile_fields
from app.utils.devanagari import normalize_text
from scripts.init_db import ROOT, init_database


@pytest.fixture
def engine(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    init_database(url)
    eng = make_engine(url)
    yield eng
    eng.dispose()


def run(engine, sql, **params):
    with engine.begin() as conn:
        return conn.execute(text(sql), params)


def rejected(engine, sql, **params):
    with pytest.raises(exc.IntegrityError):
        run(engine, sql, **params)


@pytest.fixture
def base_rows(engine):
    """A source, a document, a field, a central scheme and one rule group."""
    run(engine, "INSERT INTO sources(domain,source_type,authority_tier) VALUES('example.gov.in','official_portal',1)")
    run(engine, "INSERT INTO source_documents(source_id,url,doc_type,retrieved_at,retrieval_method) "
                "VALUES(1,'https://example.gov.in/a','html','2026-10-31','manual_curation')")
    run(engine, "INSERT INTO schemes(scheme_id,level,created_at,updated_at) VALUES('s1','central','t','t')")
    run(engine, "INSERT INTO rule_groups(scheme_id,logic,purpose) VALUES('s1','AND','eligibility')")
    return engine


# ---------------------------------------------------------------- migrations
def test_migrates_to_head_with_all_tables(engine):
    names = set(inspect(engine).get_table_names())
    assert len(set(Base.metadata.tables) & names) == 29
    assert "chunks_fts" in names
    assert "alembic_version" in names


def test_migrations_match_models_no_drift(engine):
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={
            "compare_type": False,
            "include_object": lambda obj, name, type_, reflected, compare_to:
                not (type_ == "table" and name.startswith("chunks_fts")),
        })
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == [], diff


def test_downgrade_then_upgrade_round_trip(tmp_path):
    url = f"sqlite:///{tmp_path / 'rt.db'}"
    init_database(url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.downgrade(cfg, "base")
    eng = make_engine(url)
    assert set(inspect(eng).get_table_names()) <= {"alembic_version"}
    eng.dispose()
    init_database(url)
    eng = make_engine(url)
    assert "schemes" in inspect(eng).get_table_names()
    eng.dispose()


def test_init_database_is_idempotent(tmp_path):
    url = f"sqlite:///{tmp_path / 'twice.db'}"
    init_database(url)
    init_database(url)
    eng = make_engine(url)
    assert run(eng, "SELECT COUNT(*) FROM profile_fields").scalar() == len(PROFILE_FIELDS)
    eng.dispose()


# ---------------------------------------------------------------- profile field registry
def test_registry_seeded_and_sensitive_fields_flagged(engine):
    rows = {r[0]: r for r in run(engine, "SELECT field_key,is_sensitive,enum_values,value_type FROM profile_fields")}
    assert len(rows) == len(PROFILE_FIELDS)
    for key in ("gender", "social_category", "disability_status", "marital_status"):
        assert rows[key][1] == 1
    assert rows["annual_family_income"][1] == 0
    assert "annual_individual_income" in rows  # family vs individual income are separate fields
    assert json.loads(rows["residence_area"][2]) == ["rural", "urban"]


def test_seed_updates_changed_rows_only(engine):
    with session_scope(engine) as s:
        assert seed_profile_fields(s) == 0
    run(engine, "UPDATE profile_fields SET description='old' WHERE field_key='age_years'")
    with session_scope(engine) as s:
        assert seed_profile_fields(s) == 1


# ---------------------------------------------------------------- constraints
def test_foreign_keys_are_enforced(engine):
    rejected(engine, "INSERT INTO rule_groups(scheme_id,logic,purpose) VALUES('missing','AND','eligibility')")


def test_scheme_state_must_match_level(engine):
    rejected(engine, "INSERT INTO schemes(scheme_id,level,created_at,updated_at) VALUES('a','state','t','t')")
    rejected(engine, "INSERT INTO schemes(scheme_id,level,state_code,created_at,updated_at) VALUES('b','central','MH','t','t')")
    run(engine, "INSERT INTO schemes(scheme_id,level,state_code,created_at,updated_at) VALUES('c','state','MH','t','t')")


def test_rule_needs_source_registered_field_and_valid_json(base_rows):
    ok = ("INSERT INTO rules(group_id,field_key,operator,value,source_doc_id,source_locator) "
          "VALUES(1,:f,:op,:v,:d,'para 1')")
    rejected(base_rows, ok, f="annual_family_income", op="lte", v="250000", d=None)       # no source
    rejected(base_rows, ok, f="incm", op="lte", v="1", d=1)                                # unregistered field
    rejected(base_rows, ok, f="annual_family_income", op="lte", v="{bad", d=1)             # invalid JSON
    rejected(base_rows, ok, f="annual_family_income", op="roughly", v="1", d=1)            # unknown operator
    run(base_rows, ok, f="annual_family_income", op="lte", v="250000", d=1)                # valid control


def test_conditional_document_needs_condition_group(base_rows):
    run(base_rows, "INSERT INTO document_catalog VALUES('income_certificate')")
    sql = ("INSERT INTO scheme_documents(scheme_id,document_code,requirement_type,condition_group_id,"
           "source_doc_id,source_locator) VALUES('s1','income_certificate',:t,:g,1,'p2')")
    rejected(base_rows, sql, t="conditional", g=None)
    run(base_rows, sql, t="conditional", g=1)


def test_manual_link_verification_needs_a_named_verifier(base_rows):
    sql = ("INSERT INTO links(scheme_id,url,domain,link_type,domain_class,verification_status,verified_by) "
           "VALUES('s1',:u,'example.gov.in','application','gov_in','verified_manual',:v)")
    rejected(base_rows, sql, u="https://example.gov.in/1", v=None)
    run(base_rows, sql, u="https://example.gov.in/1", v="spandan")


def test_assessment_result_must_be_a_known_status(base_rows):
    sql = ("INSERT INTO assessments(scheme_id,result,engine_version,rules_hash,created_at) "
           "VALUES('s1',:r,'1','h','t')")
    rejected(base_rows, sql, r="MAYBE")
    run(base_rows, sql, r="LIKELY_ELIGIBLE")


def test_profile_value_ranges(engine):
    run(engine, "INSERT INTO users(email,password_hash,created_at) VALUES('a@b.in','x','t')")
    rejected(engine, "INSERT INTO user_profiles(user_id,age_years,updated_at) VALUES(1,200,'t')")
    rejected(engine, "INSERT INTO user_profiles(user_id,annual_family_income,updated_at) VALUES(1,-5,'t')")
    run(engine, "INSERT INTO user_profiles(user_id,age_years,annual_family_income,updated_at) VALUES(1,21,150000,'t')")


def test_deleting_a_user_removes_their_personal_data(engine, base_rows):
    run(engine, "INSERT INTO users(email,password_hash,created_at) VALUES('a@b.in','x','t')")
    run(engine, "INSERT INTO user_profiles(user_id,age_years,updated_at) VALUES(1,21,'t')")
    run(engine, "INSERT INTO consents(user_id,purpose,granted,policy_version) VALUES(1,'store_profile',1,'v1')")
    run(engine, "INSERT INTO saved_schemes VALUES(1,'s1','t')")
    run(engine, "INSERT INTO conversations VALUES('c1',1,'en','COLLECTING','{}','t','t','t')")
    run(engine, "INSERT INTO messages(conversation_id,role,text_redacted,created_at) VALUES('c1','user','hi','t')")
    run(engine, "INSERT INTO assessments(user_id,scheme_id,result,engine_version,rules_hash,created_at) "
                "VALUES(1,'s1','LIKELY_ELIGIBLE','1','h','t')")
    run(engine, "DELETE FROM users WHERE user_id=1")
    for table in ("user_profiles", "consents", "saved_schemes", "conversations", "messages", "assessments"):
        assert run(engine, f"SELECT COUNT(*) FROM {table}").scalar() == 0, table


def test_conversation_slots_must_be_json(engine):
    rejected(engine, "INSERT INTO conversations VALUES('c2',NULL,'en','S','{oops','t','t','t')")
    run(engine, "INSERT INTO conversations VALUES('c2',NULL,'en','S','{}','t','t','t')")  # guest: user_id NULL


# ---------------------------------------------------------------- audit log
def test_audit_log_is_append_only(engine):
    run(engine, "INSERT INTO audit_log(action,entity_type,entity_id,created_at) VALUES('create','scheme','s1','t')")
    with pytest.raises(exc.DatabaseError, match="append-only"):
        run(engine, "UPDATE audit_log SET action='tampered'")
    with pytest.raises(exc.DatabaseError, match="append-only"):
        run(engine, "DELETE FROM audit_log")
    assert run(engine, "SELECT action FROM audit_log").scalar() == "create"


# ---------------------------------------------------------------- keyword index
def add_chunk(engine, text_value, h):
    cid = run(engine, "INSERT INTO chunks(doc_id,scheme_id,text,text_hash) VALUES(1,'s1',:t,:h)",
              t=text_value, h=h).lastrowid
    run(engine, "INSERT INTO chunks_fts(rowid,text) VALUES(:i,:t)", i=cid, t=normalize_text(text_value))
    return cid


def search(engine, q):
    return [r[0] for r in run(engine, "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH :q ORDER BY rowid",
                              q='"%s"' % normalize_text(q))]


def test_keyword_index_handles_hindi_marathi_english_and_digits(base_rows):
    a = add_chunk(base_rows, "शेतकरी कुटुंबांना वार्षिक ६००० रुपये मिळतात", "h1")
    b = add_chunk(base_rows, "किसान परिवारों को सालाना 6000 रुपये मिलते हैं", "h2")
    c = add_chunk(base_rows, "Farmer families receive Rs 6000 per year", "h3")
    assert search(base_rows, "शेतकरी") == [a]
    assert search(base_rows, "किसान") == [b]
    assert search(base_rows, "6000") == [a, b, c]
    assert search(base_rows, "६०००") == [a, b, c]


def test_deleting_a_chunk_removes_it_from_the_keyword_index(base_rows):
    a = add_chunk(base_rows, "Farmer families receive Rs 6000 per year", "h1")
    assert search(base_rows, "Farmer") == [a]
    run(base_rows, "DELETE FROM chunks WHERE chunk_id=:i", i=a)
    assert search(base_rows, "Farmer") == []


def test_deleting_a_source_document_cascades_to_chunks_and_index(base_rows):
    add_chunk(base_rows, "Farmer families receive Rs 6000 per year", "h1")
    run(base_rows, "DELETE FROM source_documents WHERE doc_id=1")
    assert run(base_rows, "SELECT COUNT(*) FROM chunks").scalar() == 0
    assert search(base_rows, "Farmer") == []


def test_duplicate_chunk_text_in_same_document_is_rejected(base_rows):
    run(base_rows, "INSERT INTO chunks(doc_id,text,text_hash) VALUES(1,'x','same')")
    rejected(base_rows, "INSERT INTO chunks(doc_id,text,text_hash) VALUES(1,'x','same')")


# ---------------------------------------------------------------- migrating a populated database
def test_upgrading_a_populated_database_keeps_child_rows(tmp_path):
    url = f"sqlite:///{tmp_path / 'populated.db'}"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0002")
    eng = make_engine(url)
    run(eng, "INSERT INTO schemes(scheme_id,level,created_at,updated_at) VALUES('s1','central','t','t')")
    run(eng, "INSERT INTO scheme_tags VALUES('s1','scholarship')")
    run(eng, "INSERT INTO rule_groups(scheme_id,logic,purpose) VALUES('s1','AND','eligibility')")
    eng.dispose()
    command.upgrade(cfg, "head")
    eng = make_engine(url)
    assert run(eng, "SELECT COUNT(*) FROM schemes").scalar() == 1
    assert run(eng, "SELECT COUNT(*) FROM scheme_tags").scalar() == 1
    assert run(eng, "SELECT COUNT(*) FROM rule_groups").scalar() == 1
    assert "source_hash" in {c["name"] for c in inspect(eng).get_columns("schemes")}
    eng.dispose()
