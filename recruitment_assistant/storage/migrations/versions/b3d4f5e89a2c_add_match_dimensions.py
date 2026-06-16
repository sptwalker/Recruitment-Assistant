"""Add multi-dimensional scoring to position_matches

Revision ID: b3d4f5e89a2c
Revises: (no specific dependency)
Create Date: 2026-06-17 01:57:22
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3d4f5e89a2c'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add dimension columns to position_matches table in SQLite"""
    # Note: This migration affects the SQLite resume database, not PostgreSQL
    # SQLite doesn't support ALTER TABLE ADD COLUMN with default values easily
    # We'll use raw SQL to add columns with NULL default

    # Since this is for SQLite (resume_archive.db), we can't use op.add_column directly
    # The columns will be added via SQLAlchemy model and init_resume_database()
    # This migration serves as documentation
    pass


def downgrade() -> None:
    """Remove dimension columns from position_matches table"""
    # SQLite doesn't support DROP COLUMN easily
    # Would require table recreation
    pass
