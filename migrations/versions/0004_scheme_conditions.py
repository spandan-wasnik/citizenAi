"""scheme_conditions: official conditions that cannot be checked from a profile

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheme_conditions",
        sa.Column("condition_id", sa.Integer(), primary_key=True),
        sa.Column("scheme_id", sa.Text(), sa.ForeignKey("schemes.scheme_id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("texts", sa.Text(), nullable=False),
        sa.Column("source_doc_id", sa.Integer(), sa.ForeignKey("source_documents.doc_id"), nullable=False),
        sa.Column("source_locator", sa.Text(), nullable=False),
        sa.CheckConstraint("kind IN ('exclusion', 'requirement')", name="ck_scheme_conditions_kind"),
        sa.CheckConstraint("json_valid(texts)", name="ck_scheme_conditions_texts"),
    )
    op.create_index("ix_scheme_conditions_scheme", "scheme_conditions", ["scheme_id"])


def downgrade() -> None:
    op.drop_index("ix_scheme_conditions_scheme", table_name="scheme_conditions")
    op.drop_table("scheme_conditions")
