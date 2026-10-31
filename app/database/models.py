"""SQLAlchemy models. Mirrors docs/DATABASE.md; constraints live in the database itself.

JSON columns are TEXT guarded by json_valid() (SQLite). Moving to PostgreSQL means
switching those to JSONB, which is a deliberate later migration.
"""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint, Date, ForeignKey, ForeignKeyConstraint, Index, Integer,
    PrimaryKeyConstraint, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


LANGS = ("en", "hi", "mr")


# ---------------------------------------------------------------- identity & privacy
class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in("role", ("citizen", "editor", "admin")), name="ck_users_role"),
        CheckConstraint(_in("preferred_language", LANGS), name="ck_users_lang"),
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default="citizen")
    preferred_language: Mapped[str] = mapped_column(Text, server_default="en")
    is_active: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[str] = mapped_column(Text)
    deleted_at: Mapped[str | None] = mapped_column(Text)


class Consent(Base):
    __tablename__ = "consents"
    __table_args__ = (CheckConstraint(_in("purpose", (
        "store_profile", "store_sensitive_attributes", "store_history", "process_documents")),
        name="ck_consents_purpose"),)
    consent_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(Text)
    granted: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[str] = mapped_column(Text)
    granted_at: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[str | None] = mapped_column(Text)


class ProfileField(Base):
    """Registry of fields that eligibility rules may reference."""
    __tablename__ = "profile_fields"
    __table_args__ = (CheckConstraint(_in("value_type", (
        "int", "money_inr", "enum", "bool", "string", "date")), name="ck_profile_fields_type"),)
    field_key: Mapped[str] = mapped_column(Text, primary_key=True)
    value_type: Mapped[str] = mapped_column(Text)
    enum_values: Mapped[str | None] = mapped_column(Text)
    is_sensitive: Mapped[int] = mapped_column(Integer, server_default="0")
    description: Mapped[str] = mapped_column(Text)


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint("age_years BETWEEN 0 AND 120", name="ck_profile_age"),
        CheckConstraint("annual_family_income >= 0", name="ck_profile_family_income"),
        CheckConstraint("annual_individual_income >= 0", name="ck_profile_individual_income"),
        CheckConstraint("extra IS NULL OR json_valid(extra)", name="ck_profile_extra_json"),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    age_years: Mapped[int | None] = mapped_column(Integer)
    state_code: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    occupation: Mapped[str | None] = mapped_column(Text)
    education_level: Mapped[str | None] = mapped_column(Text)
    employment_status: Mapped[str | None] = mapped_column(Text)
    annual_family_income: Mapped[int | None] = mapped_column(Integer)
    annual_individual_income: Mapped[int | None] = mapped_column(Integer)
    family_size: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[str | None] = mapped_column(Text)
    social_category: Mapped[str | None] = mapped_column(Text)
    disability_status: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- sources
class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(_in("source_type", ("official_portal", "ministry_page", "gazette_pdf",
                                            "guidelines_pdf", "secondary_reference")), name="ck_sources_type"),
        CheckConstraint("authority_tier BETWEEN 1 AND 3", name="ck_sources_tier"),
        CheckConstraint(_in("reuse_policy", ("permission_required", "free_with_attribution",
                                             "automated_prohibited", "unknown")), name="ck_sources_reuse"),
        CheckConstraint(_in("automated_access", ("allowed", "written_permission_on_file",
                                                 "prohibited", "unknown")), name="ck_sources_automated"),
    )
    source_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(Text, unique=True)
    organization: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text)
    authority_tier: Mapped[int] = mapped_column(Integer)
    terms_url: Mapped[str | None] = mapped_column(Text)
    reuse_policy: Mapped[str] = mapped_column(Text, server_default="unknown")
    automated_access: Mapped[str] = mapped_column(Text, server_default="unknown")
    robots_checked_at: Mapped[str | None] = mapped_column(Text)


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        CheckConstraint(_in("doc_type", ("html", "pdf", "docx", "json", "csv", "curated_note")), name="ck_srcdoc_type"),
        CheckConstraint(_in("retrieval_method", ("manual_curation", "admin_upload", "automated_fetch")),
                        name="ck_srcdoc_method"),
        CheckConstraint(_in("extraction_status", ("ok", "partial", "failed", "pending")), name="ck_srcdoc_extraction"),
    )
    doc_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.source_id"))
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str | None] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[str] = mapped_column(Text)
    retrieval_method: Mapped[str] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    extraction_status: Mapped[str] = mapped_column(Text, server_default="pending")
    extraction_notes: Mapped[str | None] = mapped_column(Text)
    injection_flagged: Mapped[int] = mapped_column(Integer, server_default="0")


# ---------------------------------------------------------------- knowledge base
class Scheme(Base):
    __tablename__ = "schemes"
    __table_args__ = (
        CheckConstraint(_in("level", ("central", "state", "ut", "local")), name="ck_schemes_level"),
        CheckConstraint(_in("status", ("active", "closed", "suspended", "unknown")), name="ck_schemes_status"),
        CheckConstraint(_in("volatility", ("stable", "volatile")), name="ck_schemes_volatility"),
        CheckConstraint(_in("verification_status", ("draft", "source_verified", "human_reviewed")),
                        name="ck_schemes_verification"),
        CheckConstraint("(level='central' AND state_code IS NULL) OR (level<>'central' AND state_code IS NOT NULL)",
                        name="ck_schemes_state_matches_level"),
        Index("ix_schemes_level_state_status", "level", "state_code", "status"),
    )
    scheme_id: Mapped[str] = mapped_column(Text, primary_key=True)
    level: Mapped[str] = mapped_column(Text)
    state_code: Mapped[str | None] = mapped_column(Text)
    ministry: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="unknown")
    volatility: Mapped[str] = mapped_column(Text, server_default="stable")
    verification_status: Mapped[str] = mapped_column(Text, server_default="draft")
    rules_complete: Mapped[int] = mapped_column(Integer, server_default="0")
    last_verified: Mapped[str | None] = mapped_column(Date)
    next_review_due: Mapped[str | None] = mapped_column(Date)
    application_open_date: Mapped[str | None] = mapped_column(Date)
    application_deadline: Mapped[str | None] = mapped_column(Date)
    source_hash: Mapped[str | None] = mapped_column(Text)  # hash of the data file last loaded
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class SchemeTranslation(Base):
    __tablename__ = "scheme_translations"
    __table_args__ = (
        PrimaryKeyConstraint("scheme_id", "lang"),
        CheckConstraint(_in("lang", LANGS), name="ck_scheme_tr_lang"),
        CheckConstraint(_in("origin", ("official", "curated", "machine")), name="ck_scheme_tr_origin"),
    )
    scheme_id: Mapped[str] = mapped_column(ForeignKey("schemes.scheme_id", ondelete="CASCADE"))
    lang: Mapped[str] = mapped_column(Text)
    official_name: Mapped[str] = mapped_column(Text)
    short_description: Mapped[str | None] = mapped_column(Text)
    benefits: Mapped[str | None] = mapped_column(Text)
    target_beneficiaries: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text)


class SchemeTag(Base):
    __tablename__ = "scheme_tags"
    __table_args__ = (PrimaryKeyConstraint("scheme_id", "tag"),)
    scheme_id: Mapped[str] = mapped_column(ForeignKey("schemes.scheme_id", ondelete="CASCADE"))
    tag: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- eligibility rules
class RuleGroup(Base):
    __tablename__ = "rule_groups"
    __table_args__ = (
        CheckConstraint(_in("logic", ("AND", "OR", "NOT_ANY")), name="ck_rule_groups_logic"),
        CheckConstraint(_in("purpose", ("eligibility", "exclusion", "conditional")), name="ck_rule_groups_purpose"),
        Index("ix_rule_groups_scheme", "scheme_id"),
    )
    group_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scheme_id: Mapped[str] = mapped_column(ForeignKey("schemes.scheme_id", ondelete="CASCADE"))
    parent_group_id: Mapped[int | None] = mapped_column(ForeignKey("rule_groups.group_id", ondelete="CASCADE"))
    logic: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    source_doc_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.doc_id"))
    source_locator: Mapped[str | None] = mapped_column(Text)


class Rule(Base):
    __tablename__ = "rules"
    __table_args__ = (
        CheckConstraint(_in("operator", ("eq", "neq", "in", "not_in", "gte", "lte", "gt", "lt",
                                         "between", "is_true", "is_false")), name="ck_rules_operator"),
        CheckConstraint("json_valid(value)", name="ck_rules_value_json"),
        CheckConstraint(_in("evaluability", ("auto", "official_only")), name="ck_rules_evaluability"),
        CheckConstraint(_in("rule_status", ("draft", "verified")), name="ck_rules_status"),
        Index("ix_rules_group", "group_id"),
    )
    rule_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("rule_groups.group_id", ondelete="CASCADE"))
    field_key: Mapped[str] = mapped_column(ForeignKey("profile_fields.field_key"))
    operator: Mapped[str] = mapped_column(Text)
    value: Mapped[str] = mapped_column(Text)
    as_of_date: Mapped[str | None] = mapped_column(Date)
    evaluability: Mapped[str] = mapped_column(Text, server_default="auto")
    source_doc_id: Mapped[int] = mapped_column(ForeignKey("source_documents.doc_id"))
    source_locator: Mapped[str] = mapped_column(Text)
    rule_status: Mapped[str] = mapped_column(Text, server_default="draft")


# ---------------------------------------------------------------- documents, steps, links
class DocumentCatalog(Base):
    __tablename__ = "document_catalog"
    document_code: Mapped[str] = mapped_column(Text, primary_key=True)


class DocumentTranslation(Base):
    __tablename__ = "document_translations"
    __table_args__ = (
        PrimaryKeyConstraint("document_code", "lang"),
        CheckConstraint(_in("lang", LANGS), name="ck_doc_tr_lang"),
    )
    document_code: Mapped[str] = mapped_column(ForeignKey("document_catalog.document_code"))
    lang: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)


class SchemeDocument(Base):
    __tablename__ = "scheme_documents"
    __table_args__ = (
        PrimaryKeyConstraint("scheme_id", "document_code"),
        CheckConstraint(_in("requirement_type", ("mandatory", "conditional", "optional")), name="ck_scheme_docs_type"),
        CheckConstraint("requirement_type <> 'conditional' OR condition_group_id IS NOT NULL",
                        name="ck_scheme_docs_conditional_needs_group"),
        Index("ix_scheme_documents_scheme", "scheme_id"),
    )
    scheme_id: Mapped[str] = mapped_column(ForeignKey("schemes.scheme_id", ondelete="CASCADE"))
    document_code: Mapped[str] = mapped_column(ForeignKey("document_catalog.document_code"))
    requirement_type: Mapped[str] = mapped_column(Text)
    condition_group_id: Mapped[int | None] = mapped_column(ForeignKey("rule_groups.group_id"))
    purpose_note: Mapped[str | None] = mapped_column(Text)
    source_doc_id: Mapped[int] = mapped_column(ForeignKey("source_documents.doc_id"))
    source_locator: Mapped[str] = mapped_column(Text)


class Link(Base):
    __tablename__ = "links"
    __table_args__ = (
        UniqueConstraint("scheme_id", "url", "link_type", name="uq_links_scheme_url_type"),
        CheckConstraint(_in("link_type", ("application", "information", "status_tracking",
                                          "helpline", "guidelines")), name="ck_links_type"),
        CheckConstraint(_in("domain_class", ("gov_in", "nic_in", "state_gov", "official_other_verified",
                                             "unrecognized", "suspicious")), name="ck_links_domain_class"),
        CheckConstraint(_in("verification_status", ("verified_manual", "auto_checked", "unverified", "broken",
                                                    "redirected_unverified", "suspicious")), name="ck_links_verification"),
        CheckConstraint("verification_status <> 'verified_manual' OR verified_by IS NOT NULL",
                        name="ck_links_manual_needs_verifier"),
        Index("ix_links_scheme", "scheme_id"),
    )
    link_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scheme_id: Mapped[str] = mapped_column(ForeignKey("schemes.scheme_id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text)
    link_type: Mapped[str] = mapped_column(Text)
    domain_class: Mapped[str] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(Text, server_default="unverified")
    http_status: Mapped[int | None] = mapped_column(Integer)
    final_url: Mapped[str | None] = mapped_column(Text)
    redirect_chain: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[str | None] = mapped_column(Text)
    verified_by: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class LinkCheck(Base):
    __tablename__ = "link_checks"
    check_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("links.link_id", ondelete="CASCADE"))
    checked_at: Mapped[str] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    final_url: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)


class ApplicationStep(Base):
    __tablename__ = "application_steps"
    __table_args__ = (
        UniqueConstraint("scheme_id", "lang", "step_number", name="uq_steps_scheme_lang_number"),
        CheckConstraint(_in("lang", LANGS), name="ck_steps_lang"),
        Index("ix_steps_scheme_lang", "scheme_id", "lang"),
    )
    step_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scheme_id: Mapped[str] = mapped_column(ForeignKey("schemes.scheme_id", ondelete="CASCADE"))
    lang: Mapped[str] = mapped_column(Text)
    step_number: Mapped[int] = mapped_column(Integer)
    instruction: Mapped[str] = mapped_column(Text)
    link_id: Mapped[int | None] = mapped_column(ForeignKey("links.link_id"))
    source_doc_id: Mapped[int] = mapped_column(ForeignKey("source_documents.doc_id"))
    source_locator: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- RAG storage
class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("doc_id", "text_hash", name="uq_chunks_doc_hash"),
        Index("ix_chunks_scheme_active", "scheme_id", "is_active"),
        Index("ix_chunks_doc", "doc_id"),
    )
    chunk_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("source_documents.doc_id", ondelete="CASCADE"))
    scheme_id: Mapped[str | None] = mapped_column(ForeignKey("schemes.scheme_id", ondelete="SET NULL"))
    section: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    indexed_at: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[int] = mapped_column(Integer, server_default="1")


class IndexState(Base):
    __tablename__ = "index_state"
    index_name: Mapped[str] = mapped_column(Text, primary_key=True)
    embedding_model: Mapped[str] = mapped_column(Text)
    dim: Mapped[int] = mapped_column(Integer)
    built_at: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(Text)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    job_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.doc_id"))
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- conversations & assessments
class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("json_valid(slots)", name="ck_conversations_slots_json"),
        Index("ix_conversations_expires", "expires_at"),
    )
    conversation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"))
    language: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    slots: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[str] = mapped_column(Text)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint(_in("role", ("user", "assistant")), name="ck_messages_role"),)
    message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.conversation_id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(Text)
    text_redacted: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        CheckConstraint(_in("result", ("LIKELY_ELIGIBLE", "LIKELY_INELIGIBLE", "INSUFFICIENT_INFORMATION",
                                       "REQUIRES_OFFICIAL_VERIFICATION")), name="ck_assessments_result"),
        Index("ix_assessments_user_created", "user_id", "created_at"),
    )
    assessment_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.conversation_id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"))
    scheme_id: Mapped[str] = mapped_column(ForeignKey("schemes.scheme_id"))
    result: Mapped[str] = mapped_column(Text)
    missing_fields: Mapped[str | None] = mapped_column(Text)
    engine_version: Mapped[str] = mapped_column(Text)
    rules_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class AssessmentRuleResult(Base):
    __tablename__ = "assessment_rule_results"
    __table_args__ = (
        PrimaryKeyConstraint("assessment_id", "rule_id"),
        CheckConstraint(_in("result", ("PASS", "FAIL", "UNKNOWN", "OFFICIAL_ONLY")), name="ck_arr_result"),
    )
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.assessment_id", ondelete="CASCADE"))
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.rule_id"))
    user_value: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
    reason_code: Mapped[str] = mapped_column(Text)


class SavedScheme(Base):
    __tablename__ = "saved_schemes"
    __table_args__ = (PrimaryKeyConstraint("user_id", "scheme_id"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"))
    scheme_id: Mapped[str] = mapped_column(ForeignKey("schemes.scheme_id"))
    saved_at: Mapped[str] = mapped_column(Text)


class ChecklistProgress(Base):
    __tablename__ = "checklist_progress"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "scheme_id", "document_code"),
        ForeignKeyConstraint(["scheme_id", "document_code"],
                             ["scheme_documents.scheme_id", "scheme_documents.document_code"]),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"))
    scheme_id: Mapped[str] = mapped_column(Text)
    document_code: Mapped[str] = mapped_column(Text)
    is_completed: Mapped[int] = mapped_column(Integer, server_default="0")
    updated_at: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- audit & analytics
class AuditLog(Base):
    """Append-only; UPDATE and DELETE are blocked by triggers (see the migration)."""
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)
    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str] = mapped_column(Text)
    before_json: Mapped[str | None] = mapped_column(Text)
    after_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class SearchEvent(Base):
    """Analytics without personal data: no query text, no user id."""
    __tablename__ = "search_events"
    event_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    result_count: Mapped[int | None] = mapped_column(Integer)
