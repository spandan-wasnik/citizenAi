"""Seeds the profile-field registry table from app.domain.fields (the single source of truth).

Adding a field there (and re-running the seed) is how the rule vocabulary grows; data
files that mention an unregistered field fail validation instead of silently never matching.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import ProfileField
from app.domain.fields import PROFILE_FIELDS

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
