"""schemes.source_hash: detect unchanged data files on re-seed

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Native ALTER TABLE ADD COLUMN: no table rebuild, so existing rows are untouched.
    op.add_column("schemes", sa.Column("source_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("schemes", "source_hash")
