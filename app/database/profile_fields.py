"""Canonical registry of profile fields that eligibility rules may reference.

Adding a field here (and re-running the seed) is how the rule vocabulary grows; data
files that mention an unregistered field fail validation instead of silently never matching.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import ProfileField

# (field_key, value_type, enum_values, is_sensitive, description)
PROFILE_FIELDS: list[tuple[str, str, list[str] | None, int, str]] = [
    ("age_years", "int", None, 0, "Age in completed years (relative to the rule's as_of date when given)"),
    ("state_code", "enum", None, 0, "State or UT of residence, ISO 3166-2:IN code such as MH"),
    ("domicile_state_code", "enum", None, 0, "State of domicile (can differ from current residence)"),
    ("study_state_code", "enum", None, 0, "State where the person studies"),
    ("district", "string", None, 0, "District of residence"),
    ("residence_area", "enum", ["rural", "urban"], 0, "Rural or urban residence"),
    ("annual_family_income", "money_inr", None, 0, "Total annual family income in INR"),
    ("annual_individual_income", "money_inr", None, 0, "Annual income of the applicant alone in INR"),
    ("family_size", "int", None, 0, "Number of family members"),
    ("occupation", "enum", ["student", "farmer", "self_employed", "salaried", "unemployed",
                            "homemaker", "retired", "artisan", "laborer", "other"], 0, "Primary occupation"),
    ("employment_status", "enum", ["employed", "unemployed", "self_employed", "student", "retired"], 0,
     "Current employment status"),
    ("education_level", "enum", ["none", "primary", "secondary", "higher_secondary", "diploma",
                                 "undergraduate", "postgraduate", "doctorate"], 0, "Highest or current education level"),
    ("is_enrolled_in_education", "bool", None, 0, "Currently enrolled in an education institution"),
    ("owns_agricultural_land", "bool", None, 0, "Owns cultivable agricultural land"),
    ("has_bank_account", "bool", None, 0, "Has a bank account (existence only; no account details are collected)"),
    ("receives_other_scholarship", "bool", None, 0, "Currently receives another scholarship (exclusion checks)"),
    ("gender", "enum", ["female", "male", "other"], 1, "Gender; collected only when a scheme rule needs it"),
    ("social_category", "enum", ["general", "sc", "st", "obc", "ews", "other"], 1,
     "Social category; collected only when a scheme rule needs it"),
    ("disability_status", "bool", None, 1, "Has a disability; collected only when a scheme rule needs it"),
    ("marital_status", "enum", ["unmarried", "married", "widowed", "divorced", "separated"], 1,
     "Marital status; collected only when a scheme rule needs it"),
]


def seed_profile_fields(session: Session) -> int:
    """Insert or update the registry. Idempotent. Returns the number of rows changed."""
    existing = {f.field_key: f for f in session.scalars(select(ProfileField))}
    changed = 0
    for key, vtype, enum_values, sensitive, desc in PROFILE_FIELDS:
        enum_json = json.dumps(enum_values) if enum_values else None
        row = existing.get(key)
        if row is None:
            session.add(ProfileField(field_key=key, value_type=vtype, enum_values=enum_json,
                                     is_sensitive=sensitive, description=desc))
            changed += 1
        elif (row.value_type, row.enum_values, row.is_sensitive, row.description) != (vtype, enum_json, sensitive, desc):
            row.value_type, row.enum_values, row.is_sensitive, row.description = vtype, enum_json, sensitive, desc
            changed += 1
    session.flush()
    return changed
