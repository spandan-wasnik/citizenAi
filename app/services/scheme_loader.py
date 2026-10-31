"""Load, validate and seed scheme data files (data/schemes/*.yaml) into the database.

Safety properties (all tested):
  * a file whose name differs from its scheme_id, or that cites a domain missing from the
    source registry, is rejected;
  * seeding is idempotent: an unchanged file does nothing;
  * a scheme already marked human_reviewed in the database is never overwritten by a changed
    file unless force=True, so verified information is not silently replaced;
  * every create/update writes an audit_log row.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import models as m
from app.domain.domains import classify_domain, host_of
from app.domain.link_policy import assess_url
from app.domain.scheme_schema import GroupSpec, SchemeFile

REGISTRY_FIELDS = {"domain", "organization", "source_type", "authority_tier", "terms_url",
                   "reuse_policy", "automated_access", "robots_checked_at"}


class SchemeFileError(Exception):
    def __init__(self, path: Path | str, messages: list[str]):
        self.path, self.messages = str(path), messages
        super().__init__(f"{path}: " + " | ".join(messages))


class SeedConflict(Exception):
    pass


@dataclass
class SeedResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ reading files
def load_registry(path: Path) -> dict[str, dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, dict] = {}
    for entry in data.get("sources", []):
        unknown = set(entry) - REGISTRY_FIELDS
        if unknown or "domain" not in entry:
            raise SchemeFileError(path, [f"bad registry entry {entry.get('domain')!r}: {sorted(unknown)}"])
        out[entry["domain"].lower()] = entry
    return out


def _format_validation_error(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in e['loc']) or '(file)'}: {e['msg'].removeprefix('Value error, ')}"
            for e in exc.errors()]


def load_scheme_file(path: Path, registry: dict[str, dict]) -> SchemeFile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
        raise SchemeFileError(path, [f"cannot read file: {exc}"]) from exc
    if not isinstance(raw, dict):
        raise SchemeFileError(path, ["file must contain a YAML mapping"])
    try:
        scheme = SchemeFile.model_validate(raw)
    except ValidationError as exc:
        raise SchemeFileError(path, _format_validation_error(exc)) from exc

    problems = []
    if path.stem != scheme.scheme_id:
        problems.append(f"file name '{path.stem}' must equal scheme_id '{scheme.scheme_id}'")
    for src in scheme.sources:
        host = host_of(src.url)
        if host not in registry:
            problems.append(f"source '{src.key}': domain '{host}' is not in the source registry "
                            "(add it to data/sources/registry.yaml with its reuse terms first)")
        elif registry[host].get("automated_access") == "prohibited" and src.retrieval_method == "automated_fetch":
            problems.append(f"source '{src.key}': automated fetching from '{host}' is prohibited by its terms")
    for link in scheme.links:
        assessment = assess_url(link.url, tuple(registry))
        if assessment.suspicious:
            problems.append(f"link {link.url}: flagged suspicious ({', '.join(assessment.danger)})")
    if problems:
        raise SchemeFileError(path, problems)
    return scheme


def load_scheme_dir(directory: Path, registry: dict[str, dict]) -> tuple[list[SchemeFile], list[SchemeFileError]]:
    """Load every *.yaml except files starting with '_' (templates). Collects all errors."""
    schemes: list[SchemeFile] = []
    errors: list[SchemeFileError] = []
    seen: dict[str, Path] = {}
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        try:
            scheme = load_scheme_file(path, registry)
        except SchemeFileError as exc:
            errors.append(exc)
            continue
        if scheme.scheme_id in seen:
            errors.append(SchemeFileError(path, [f"duplicate scheme_id (also in {seen[scheme.scheme_id].name})"]))
            continue
        seen[scheme.scheme_id] = path
        schemes.append(scheme)
    return schemes, errors


def content_hash(scheme: SchemeFile) -> str:
    blob = json.dumps(scheme.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ writing to the database
def sync_registry(session: Session, registry: dict[str, dict]) -> dict[str, int]:
    """Upsert registry rows; returns domain -> source_id."""
    ids: dict[str, int] = {}
    for domain, entry in registry.items():
        row = session.scalar(select(m.Source).where(m.Source.domain == domain))
        values = {k: entry.get(k) for k in REGISTRY_FIELDS - {"domain"}}
        if row is None:
            row = m.Source(domain=domain, **values)
            session.add(row)
        else:
            for k, v in values.items():
                setattr(row, k, v)
        session.flush()
        ids[domain] = row.source_id
    return ids


def _clear_children(session: Session, scheme_id: str) -> None:
    sid = scheme_id
    session.execute(delete(m.ApplicationStep).where(m.ApplicationStep.scheme_id == sid))
    session.execute(delete(m.SchemeDocument).where(m.SchemeDocument.scheme_id == sid))
    session.execute(delete(m.RuleGroup).where(m.RuleGroup.scheme_id == sid))  # cascades to rules
    session.execute(delete(m.Link).where(m.Link.scheme_id == sid))
    session.execute(delete(m.SchemeTag).where(m.SchemeTag.scheme_id == sid))
    session.execute(delete(m.SchemeTranslation).where(m.SchemeTranslation.scheme_id == sid))


def _write_group(session: Session, scheme: SchemeFile, group: GroupSpec, purpose: str, parent: int | None,
                 doc_ids: dict[str, int], rule_status: str, logic: str | None = None) -> int:
    row = m.RuleGroup(scheme_id=scheme.scheme_id, parent_group_id=parent, logic=logic or group.logic,
                      purpose=purpose, description=group.description)
    session.add(row)
    session.flush()
    for item in group.items:
        if item.group:
            _write_group(session, scheme, item.group, purpose, row.group_id, doc_ids, rule_status)
        else:
            _write_rule(session, row.group_id, item.rule, doc_ids, rule_status)  # type: ignore[arg-type]
    return row.group_id


def _write_rule(session: Session, group_id: int, rule, doc_ids: dict[str, int], rule_status: str) -> None:
    session.add(m.Rule(
        group_id=group_id, field_key=rule.field, operator=rule.op, value=json.dumps(rule.value),
        as_of_date=rule.as_of, evaluability=rule.evaluability, source_doc_id=doc_ids[rule.source],
        source_locator=rule.locator, rule_status=rule_status))


def upsert_scheme(session: Session, scheme: SchemeFile, source_ids: dict[str, int], *,
                  force: bool = False, result: SeedResult | None = None) -> str:
    """Returns 'created', 'updated' or 'unchanged'."""
    result = result if result is not None else SeedResult()
    sid = scheme.scheme_id
    digest = content_hash(scheme)
    existing = session.get(m.Scheme, sid)

    if existing is not None and existing.source_hash == digest:
        result.unchanged.append(sid)
        return "unchanged"
    if existing is not None and existing.verification_status == "human_reviewed" and not force:
        raise SeedConflict(f"'{sid}' is human_reviewed in the database and its data file changed; "
                           "refusing to overwrite (re-run with force after reviewing the diff)")

    now = _now()
    columns = dict(
        level=scheme.level, state_code=scheme.state_code, ministry=scheme.ministry, department=scheme.department,
        status=scheme.status, volatility=scheme.volatility, verification_status=scheme.verification_status,
        rules_complete=int(scheme.rules_complete), last_verified=scheme.last_verified,
        next_review_due=scheme.next_review_due, application_open_date=scheme.application_open_date,
        application_deadline=scheme.application_deadline, source_hash=digest, updated_at=now)
    if existing is None:
        session.add(m.Scheme(scheme_id=sid, created_at=now, **columns))
        action = "created"
    else:
        try:
            _clear_children(session, sid)
        except IntegrityError as exc:
            raise SeedConflict(f"'{sid}': {exc.orig}. If assessments reference its old rules, "
                               "use the admin edit flow instead of re-seeding.") from exc
        for k, v in columns.items():
            setattr(existing, k, v)
        action = "updated"
    try:
        session.flush()
    except IntegrityError as exc:
        raise SeedConflict(f"'{sid}': {exc.orig}") from exc

    # source documents (upsert by url)
    doc_ids: dict[str, int] = {}
    for src in scheme.sources:
        doc = session.scalar(select(m.SourceDocument).where(m.SourceDocument.url == src.url))
        vals = dict(source_id=source_ids[host_of(src.url)], title=src.title, doc_type=src.doc_type,
                    language=src.language, retrieved_at=src.retrieved_at.isoformat(),
                    retrieval_method=src.retrieval_method)
        if doc is None:
            doc = m.SourceDocument(url=src.url, **vals)
            session.add(doc)
        else:
            for k, v in vals.items():
                setattr(doc, k, v)
        session.flush()
        doc_ids[src.key] = doc.doc_id

    for lang, tr in scheme.translations.items():
        session.add(m.SchemeTranslation(scheme_id=sid, lang=lang, **tr.model_dump()))
    for tag in sorted(set(scheme.tags)):
        session.add(m.SchemeTag(scheme_id=sid, tag=tag))

    link_ids: dict[str, int] = {}
    for link in scheme.links:
        host = host_of(link.url)
        domain_class = "official_other_verified" if (
            link.official_other_verified and classify_domain(host) == "unrecognized") else classify_domain(host)
        row = m.Link(scheme_id=sid, url=link.url, domain=host, link_type=link.link_type,
                     domain_class=domain_class, verification_status=link.verification_status,
                     verified_by=link.verified_by, verified_at=link.verified_at.isoformat() if link.verified_at else None,
                     notes=link.notes)
        session.add(row)
        session.flush()
        link_ids.setdefault(link.url, row.link_id)

    # Rules only count once the scheme itself is source-verified.
    rule_status = "verified" if scheme.verification_status != "draft" else "draft"
    if scheme.eligibility:
        _write_group(session, scheme, scheme.eligibility, "eligibility", None, doc_ids, rule_status)
    if scheme.exclusions:
        ex = m.RuleGroup(scheme_id=sid, logic="NOT_ANY", purpose="exclusion", description="Exclusion criteria")
        session.add(ex)
        session.flush()
        for rule in scheme.exclusions:
            _write_rule(session, ex.group_id, rule, doc_ids, rule_status)
    cond_ids = {name: _write_group(session, scheme, g, "conditional", None, doc_ids, rule_status)
                for name, g in scheme.condition_groups.items()}

    for doc in scheme.documents:
        if session.get(m.DocumentCatalog, doc.code) is None:
            session.add(m.DocumentCatalog(document_code=doc.code))
            session.flush()
        for lang, name in doc.names.items():
            tr = session.get(m.DocumentTranslation, (doc.code, lang))
            if tr is None:
                session.add(m.DocumentTranslation(document_code=doc.code, lang=lang, name=name,
                                                  explanation=doc.explanations.get(lang)))
            elif tr.name != name:
                result.warnings.append(f"{sid}: document '{doc.code}' [{lang}] already named '{tr.name}'; "
                                       f"kept it (file says '{name}')")
        session.add(m.SchemeDocument(
            scheme_id=sid, document_code=doc.code, requirement_type=doc.requirement_type,
            condition_group_id=cond_ids.get(doc.applies_if) if doc.applies_if else None,
            purpose_note=doc.purpose_note, source_doc_id=doc_ids[doc.source], source_locator=doc.locator))

    for lang, steps in scheme.steps.items():
        for number, step in enumerate(steps, 1):
            session.add(m.ApplicationStep(
                scheme_id=sid, lang=lang, step_number=number, instruction=step.instruction,
                link_id=link_ids.get(step.link) if step.link else None,
                source_doc_id=doc_ids[step.source], source_locator=step.locator))

    try:
        session.flush()
    except IntegrityError as exc:
        raise SeedConflict(f"'{sid}': {exc.orig}. If assessments reference its old rules, "
                           "use the admin edit flow instead of re-seeding.") from exc

    session.add(m.AuditLog(actor_user_id=None, action=f"seed_{action}", entity_type="scheme", entity_id=sid,
                           after_json=json.dumps({"source_hash": digest, "verification_status": scheme.verification_status}),
                           created_at=now))
    session.flush()
    getattr(result, action).append(sid)
    return action


def seed_schemes(session: Session, schemes: list[SchemeFile], registry: dict[str, dict], *,
                 force: bool = False) -> SeedResult:
    result = SeedResult()
    source_ids = sync_registry(session, registry)
    for scheme in schemes:
        upsert_scheme(session, scheme, source_ids, force=force, result=result)
    return result
