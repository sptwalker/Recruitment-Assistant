"""Unify job_position: rename fields, add responsibilities and job_requirements

Revision ID: a3f2c8e71b04
Revises: 7d8149b30d99
Create Date: 2026-06-12 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "a3f2c8e71b04"
down_revision = "7d8149b30d99"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Rename columns to match UI expectations ---
    op.alter_column("job_position", "job_name", new_column_name="title")
    op.alter_column("job_position", "city", new_column_name="work_city")
    op.alter_column("job_position", "degree_requirement", new_column_name="min_education")

    # --- Replace salary_min / salary_max → salary_range (String) ---
    op.add_column("job_position", sa.Column("salary_range", sa.String(100)))
    op.execute(
        """
        UPDATE job_position
        SET salary_range = CASE
            WHEN salary_min IS NOT NULL AND salary_max IS NOT NULL
                THEN salary_min::text || '-' || salary_max::text
            WHEN salary_min IS NOT NULL THEN '>=' || salary_min::text
            WHEN salary_max IS NOT NULL THEN '<=' || salary_max::text
            ELSE NULL
        END
        """
    )
    op.drop_column("job_position", "salary_min")
    op.drop_column("job_position", "salary_max")

    # --- Replace experience_min_years / experience_max_years → min_experience (String) ---
    op.add_column("job_position", sa.Column("min_experience", sa.String(64)))
    op.execute(
        """
        UPDATE job_position
        SET min_experience = CASE
            WHEN experience_min_years IS NOT NULL AND experience_max_years IS NOT NULL
                THEN experience_min_years::text || '-' || experience_max_years::text || '年'
            WHEN experience_min_years IS NOT NULL
                THEN experience_min_years::text || '年以上'
            WHEN experience_max_years IS NOT NULL
                THEN experience_max_years::text || '年以内'
            ELSE NULL
        END
        """
    )
    op.drop_column("job_position", "experience_min_years")
    op.drop_column("job_position", "experience_max_years")

    # --- Add new text fields ---
    op.add_column("job_position", sa.Column("responsibilities", sa.Text))
    op.add_column("job_position", sa.Column("job_requirements", sa.Text))


def downgrade() -> None:
    # --- Remove new fields ---
    op.drop_column("job_position", "job_requirements")
    op.drop_column("job_position", "responsibilities")

    # --- Restore experience_min_years / experience_max_years ---
    op.add_column("job_position", sa.Column("experience_min_years", sa.Numeric(4, 1)))
    op.add_column("job_position", sa.Column("experience_max_years", sa.Numeric(4, 1)))
    op.drop_column("job_position", "min_experience")

    # --- Restore salary_min / salary_max ---
    op.add_column("job_position", sa.Column("salary_min", sa.Integer))
    op.add_column("job_position", sa.Column("salary_max", sa.Integer))
    op.drop_column("job_position", "salary_range")

    # --- Rename columns back ---
    op.alter_column("job_position", "min_education", new_column_name="degree_requirement")
    op.alter_column("job_position", "work_city", new_column_name="city")
    op.alter_column("job_position", "title", new_column_name="job_name")
