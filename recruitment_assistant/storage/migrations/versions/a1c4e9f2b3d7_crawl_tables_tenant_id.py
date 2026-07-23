"""crawl tables tenant_id (crawl_task, boss_candidate_record)

Revision ID: a1c4e9f2b3d7
Revises: 5f8fc672f4d6
Create Date: 2026-07-23 00:00:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c4e9f2b3d7'
down_revision: str | None = '5f8fc672f4d6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('crawl_task', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_crawl_task_tenant_id'), ['tenant_id'], unique=False)

    with op.batch_alter_table('boss_candidate_record', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_boss_candidate_record_tenant_id'), ['tenant_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('boss_candidate_record', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_boss_candidate_record_tenant_id'))
        batch_op.drop_column('tenant_id')

    with op.batch_alter_table('crawl_task', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_crawl_task_tenant_id'))
        batch_op.drop_column('tenant_id')
