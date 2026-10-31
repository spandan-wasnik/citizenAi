import copy
import json
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text

from app.database.connection import make_engine, session_scope
from app.services.scheme_loader import (
    SchemeFileError, SeedConflict, content_hash, load_registry, load_scheme_dir, load_scheme_file, seed_schemes,
)
from scripts.init_db import init_database
from tests.unit.test_scheme_schema import base

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_registry(ROOT / "data" / "sources" / "registry.yaml")


@pytest.fixture
def engine(tmp_path):
    url = f"sqlite:///{tmp_path / 'seed.db'}"
    init_database(url)
    eng = make_engine(url)
    yield eng
    eng.dispose()


def write(directory: Path, data: dict, name: str | None = None) -> Path:
    path = directory / f"{name or data['scheme_id']}.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def seed(engine, data_list, **kw):
    from app.domain.scheme_schema import SchemeFile
    schemes = [SchemeFile.model_validate(d) for d in data_list]
    with session_scope(engine) as s:
        return seed_schemes(s, schemes, REGISTRY, **kw)


def q(engine, sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).fetchall()


# ------------------------------------------------------------------ reading files
def test_registry_has_only_checked_facts():
    mys = REGISTRY["myscheme.gov.in"]
    assert mys["automated_access"] == "prohibited"
    assert REGISTRY["pmkisan.gov.in"]["reuse_policy"] == "permission_required"
    assert REGISTRY["pib.gov.in"]["reuse_policy"] == "free_with_attribution"
    unreviewed = REGISTRY["scholarships.gov.in"]
    assert unreviewed["reuse_policy"] == "unknown" and unreviewed["terms_url"] is None


def test_file_name_must_match_scheme_id(tmp_path):
    path = write(tmp_path, base(), name="something-else")
    with pytest.raises(SchemeFileError, match="must equal scheme_id"):
        load_scheme_file(path, REGISTRY)


def test_source_domain_must_be_in_registry(tmp_path):
    d = base()
    d["sources"][0]["url"] = "https://unlisted.gov.in/x"
    d["links"] = []
    d["steps"]["en"][0]["link"] = None
    with pytest.raises(SchemeFileError, match="not in the source registry"):
        load_scheme_file(write(tmp_path, d), REGISTRY)


def test_automated_fetch_from_a_prohibiting_site_is_rejected(tmp_path):
    d = base()
    d["sources"][0].update(url="https://www.myscheme.gov.in/schemes/x", retrieval_method="automated_fetch")
    d["links"] = []
    d["steps"]["en"][0]["link"] = None
    with pytest.raises(SchemeFileError, match="prohibited by its terms"):
        load_scheme_file(write(tmp_path, d), REGISTRY)
    d["sources"][0]["retrieval_method"] = "manual_curation"  # reading it in a browser is fine
    load_scheme_file(write(tmp_path, d), REGISTRY)


def test_bad_yaml_and_non_mapping_are_reported_not_crashed(tmp_path):
    (tmp_path / "broken.yaml").write_text("a: [unclosed", encoding="utf-8")
    (tmp_path / "list.yaml").write_text("- 1\n- 2\n", encoding="utf-8")
    schemes, errors = load_scheme_dir(tmp_path, REGISTRY)
    assert schemes == [] and len(errors) == 2


def test_directory_load_collects_all_errors_skips_templates_and_flags_duplicates(tmp_path):
    write(tmp_path, base())
    bad = base(); bad["scheme_id"] = "bad-one"; bad["exclusions"][0]["field"] = "typo"
    write(tmp_path, bad)
    write(tmp_path, base(), name="_template")  # skipped (and would mismatch its name anyway)
    dup = copy.deepcopy(base())
    write(tmp_path, dup, name="zzz-dup")
    schemes, errors = load_scheme_dir(tmp_path, REGISTRY)
    assert [s.scheme_id for s in schemes] == ["test-scholarship"]
    messages = " ".join(m for e in errors for m in e.messages)
    assert "unknown field 'typo'" in messages and "must equal scheme_id" in messages


def test_hindi_and_marathi_text_survives_the_round_trip(tmp_path):
    d = base()
    d["translations"]["mr"] = {"official_name": "चाचणी शिष्यवृत्ती", "origin": "curated"}
    scheme = load_scheme_file(write(tmp_path, d), REGISTRY)
    assert scheme.translations["mr"].official_name == "चाचणी शिष्यवृत्ती"


def test_repo_data_directory_currently_validates():
    schemes, errors = load_scheme_dir(ROOT / "data" / "schemes", REGISTRY)
    assert errors == []


# ------------------------------------------------------------------ seeding
def test_seed_creates_everything_with_correct_structure(engine):
    result = seed(engine, [base()])
    assert result.created == ["test-scholarship"]
    assert q(engine, "SELECT COUNT(*) FROM rules")[0][0] == 6
    groups = {r[0]: r[1] for r in q(engine, "SELECT purpose || ':' || logic, COUNT(*) FROM rule_groups GROUP BY 1")}
    assert groups == {"eligibility:AND": 1, "eligibility:OR": 1, "exclusion:NOT_ANY": 1, "conditional:AND": 1}
    nested = q(engine, "SELECT COUNT(*) FROM rule_groups WHERE parent_group_id IS NOT NULL")[0][0]
    assert nested == 1
    assert {r[0] for r in q(engine, "SELECT rule_status FROM rules")} == {"verified"}
    rows = q(engine, "SELECT requirement_type, condition_group_id IS NOT NULL FROM scheme_documents ORDER BY 1")
    assert rows == [("conditional", 1), ("mandatory", 0)]
    assert q(engine, "SELECT value FROM rules WHERE field_key='age_years'")[0][0] == "[18, 30]"
    link = q(engine, "SELECT domain, domain_class, verification_status FROM links")[0]
    assert tuple(link) == ("pib.gov.in", "gov_in", "unverified")
    assert q(engine, "SELECT link_id IS NOT NULL FROM application_steps")[0][0] == 1
    assert q(engine, "SELECT COUNT(*) FROM audit_log WHERE action='seed_created'")[0][0] == 1


def test_draft_scheme_rules_are_stored_as_draft(engine):
    d = base()
    d["verification_status"] = "draft"; d["rules_complete"] = False
    seed(engine, [d])
    assert {r[0] for r in q(engine, "SELECT rule_status FROM rules")} == {"draft"}


def test_boolean_rule_values_are_stored_as_json_null(engine):
    seed(engine, [base()])
    assert q(engine, "SELECT value FROM rules WHERE operator='is_true'")[0][0] == "null"
    assert json.loads(q(engine, "SELECT value FROM rules WHERE operator='is_true'")[0][0]) is None


def test_reseeding_unchanged_data_changes_nothing(engine):
    seed(engine, [base()])
    ids_before = q(engine, "SELECT rule_id FROM rules ORDER BY 1")
    result = seed(engine, [base()])
    assert result.unchanged == ["test-scholarship"] and not result.created and not result.updated
    assert q(engine, "SELECT rule_id FROM rules ORDER BY 1") == ids_before
    assert q(engine, "SELECT COUNT(*) FROM audit_log")[0][0] == 1


def test_changed_data_replaces_children_without_duplicates_and_is_audited(engine):
    seed(engine, [base()])
    changed = base()
    changed["eligibility"]["items"][1]["rule"]["value"] = 300000
    changed["documents"].pop()
    result = seed(engine, [changed])
    assert result.updated == ["test-scholarship"]
    assert q(engine, "SELECT COUNT(*) FROM rules")[0][0] == 6
    assert q(engine, "SELECT value FROM rules WHERE field_key='annual_family_income'")[0][0] == "300000"
    assert q(engine, "SELECT COUNT(*) FROM scheme_documents")[0][0] == 1
    assert q(engine, "SELECT COUNT(*) FROM audit_log WHERE action='seed_updated'")[0][0] == 1
    assert q(engine, "SELECT COUNT(*) FROM schemes")[0][0] == 1


def test_human_reviewed_scheme_is_not_silently_overwritten(engine):
    seed(engine, [base()])
    with engine.begin() as c:
        c.execute(text("UPDATE schemes SET verification_status='human_reviewed'"))
    changed = base()
    changed["eligibility"]["items"][1]["rule"]["value"] = 999999
    with pytest.raises(SeedConflict, match="human_reviewed"):
        seed(engine, [changed])
    assert q(engine, "SELECT value FROM rules WHERE field_key='annual_family_income'")[0][0] == "250000"
    seed(engine, [changed], force=True)
    assert q(engine, "SELECT value FROM rules WHERE field_key='annual_family_income'")[0][0] == "999999"


def test_reseed_is_blocked_when_assessments_reference_old_rules(engine):
    seed(engine, [base()])
    with engine.begin() as c:
        rule_id = c.execute(text("SELECT rule_id FROM rules LIMIT 1")).scalar()
        c.execute(text("INSERT INTO assessments(scheme_id,result,engine_version,rules_hash,created_at) "
                       "VALUES('test-scholarship','LIKELY_ELIGIBLE','1','h','t')"))
        c.execute(text("INSERT INTO assessment_rule_results VALUES(1,:r,'21','PASS','ok')"), {"r": rule_id})
    changed = base()
    changed["eligibility"]["items"][1]["rule"]["value"] = 1
    with pytest.raises(SeedConflict, match="admin edit flow"):
        seed(engine, [changed])
    assert q(engine, "SELECT value FROM rules WHERE field_key='annual_family_income'")[0][0] == "250000"  # rolled back


def test_shared_document_catalog_keeps_existing_names_and_warns(engine):
    seed(engine, [base()])
    other = base()
    other["scheme_id"] = "test-two"
    other["sources"][0]["url"] = "https://pib.gov.in/fixture-two"
    other["links"][0]["url"] = other["steps"]["en"][0]["link"] = "https://pib.gov.in/fixture-two"
    other["documents"][0]["names"] = {"en": "Income Proof"}
    result = seed(engine, [other])
    assert any("already named 'Income Certificate'" in w for w in result.warnings)
    assert q(engine, "SELECT COUNT(*) FROM document_catalog WHERE document_code='income_certificate'")[0][0] == 1
    assert q(engine, "SELECT name FROM document_translations WHERE document_code='income_certificate'")[0][0] == "Income Certificate"


def test_registry_rows_are_synced_with_reuse_terms(engine):
    seed(engine, [base()])
    row = q(engine, "SELECT reuse_policy, automated_access FROM sources WHERE domain='myscheme.gov.in'")[0]
    assert tuple(row) == ("unknown", "prohibited")


def test_source_documents_are_upserted_by_url(engine):
    seed(engine, [base()])
    changed = base()
    changed["sources"][0]["title"] = "Now titled"
    seed(engine, [changed])
    assert q(engine, "SELECT COUNT(*), MAX(title) FROM source_documents")[0] == (1, "Now titled")


def test_content_hash_is_stable_and_sensitive():
    from app.domain.scheme_schema import SchemeFile
    a, b = SchemeFile.model_validate(base()), SchemeFile.model_validate(base())
    assert content_hash(a) == content_hash(b)
    c = base(); c["status"] = "closed"
    assert content_hash(SchemeFile.model_validate(c)) != content_hash(a)


def test_unicode_names_are_stored_intact(engine):
    d = base()
    d["translations"]["hi"] = {"official_name": "परीक्षण छात्रवृत्ति", "origin": "curated"}
    d["documents"][0]["names"]["hi"] = "आय प्रमाण पत्र"
    seed(engine, [d])
    assert q(engine, "SELECT official_name FROM scheme_translations WHERE lang='hi'")[0][0] == "परीक्षण छात्रवृत्ति"
    assert q(engine, "SELECT name FROM document_translations WHERE lang='hi'")[0][0] == "आय प्रमाण पत्र"


def test_registry_dates_are_stored_as_iso_text(engine):
    seed(engine, [base()])
    value = q(engine, "SELECT robots_checked_at FROM sources WHERE domain='pib.gov.in'")[0][0]
    assert value == "2026-10-31" and isinstance(value, str)
    assert q(engine, "SELECT robots_checked_at FROM sources WHERE domain='pmkisan.gov.in'")[0][0] is None
