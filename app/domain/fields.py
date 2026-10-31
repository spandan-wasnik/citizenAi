"""Pure-Python registry of the profile fields eligibility rules may reference.

This is the single source of truth: the database registry table is seeded from it and the
scheme-file validator checks rules against it. No I/O or database imports here.
"""
from __future__ import annotations

# ISO 3166-2:IN subdivision codes: 28 states and 8 union territories (written from memory;
# a test pins the count, and the list should be re-checked against the ISO source).
STATE_CODES: frozenset[str] = frozenset({
    "AP", "AR", "AS", "BR", "CG", "GA", "GJ", "HR", "HP", "JH", "KA", "KL", "MP", "MH", "MN", "ML",
    "MZ", "NL", "OD", "PB", "RJ", "SK", "TN", "TS", "TR", "UP", "UK", "WB",        # states
    "AN", "CH", "DH", "DL", "JK", "LA", "LD", "PY",                                # union territories
})

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

_STATE_FIELDS = {"state_code", "domicile_state_code", "study_state_code"}
FIELD_TYPES: dict[str, str] = {key: vtype for key, vtype, *_ in PROFILE_FIELDS}


def allowed_values(field_key: str) -> frozenset[str] | None:
    """Permitted values for an enum field, or None if the field is not an enum."""
    if field_key in _STATE_FIELDS:
        return STATE_CODES
    for key, vtype, values, *_ in PROFILE_FIELDS:
        if key == field_key and vtype == "enum":
            return frozenset(values or [])
    return None
