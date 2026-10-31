"""keyword index (FTS5) and append-only audit log

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Frozen copy of the Devanagari combining marks (do not import app code into a migration).
_MARKS = "".join(chr(cp) for cp in (
    *range(0x0900, 0x0904), *range(0x093A, 0x093D), *range(0x093E, 0x0950),
    *range(0x0951, 0x0958), 0x0962, 0x0963))


def upgrade() -> None:
    # Standalone FTS5 table: rowid == chunks.chunk_id. The indexer inserts NORMALISED text
    # (Devanagari digits converted), so this cannot be an external-content table.
    op.execute(
        f"CREATE VIRTUAL TABLE chunks_fts USING fts5(text, "
        f"tokenize=\"unicode61 tokenchars '{_MARKS}'\")"
    )
    op.execute(
        "CREATE TRIGGER chunks_fts_cleanup AFTER DELETE ON chunks "
        "BEGIN DELETE FROM chunks_fts WHERE rowid = old.chunk_id; END"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    op.execute("DROP TRIGGER IF EXISTS chunks_fts_cleanup")
    op.execute("DROP TABLE IF EXISTS chunks_fts")
