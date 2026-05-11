"""add crawl_jobs table

Revision ID: 020
Revises: 019
Create Date: 2026-05-11 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = '020'
down_revision: Union[str, None] = '019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'crawl_jobs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('start_url', sa.Text(), nullable=False),
        sa.Column('domain', sa.String(500), nullable=False),
        sa.Column(
            'status', sa.String(20), nullable=False, server_default='pending',
            comment='pending | running | completed | failed | cancelled',
        ),
        sa.Column('knowledge_type_id', UUID(as_uuid=True), nullable=True),
        sa.Column('scope_type', sa.String(20), nullable=False, server_default='global'),
        sa.Column('scope_id', UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_id', UUID(as_uuid=True), nullable=True),
        # Progress counters
        sa.Column('pages_discovered', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('pages_crawled', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('pages_failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('pages_skipped', sa.Integer(), nullable=False, server_default='0'),
        # Visited URL dedup store (array of normalized URLs)
        sa.Column('visited_urls', JSONB, nullable=False, server_default='[]'),
        # Error message if status=failed
        sa.Column('error_message', sa.Text(), nullable=True),
        # arq job id
        sa.Column('job_id', sa.String(200), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True),
            server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False,
        ),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_crawl_jobs_status', 'crawl_jobs', ['status'])
    op.create_index('ix_crawl_jobs_created_by_id', 'crawl_jobs', ['created_by_id'])


def downgrade() -> None:
    op.drop_index('ix_crawl_jobs_created_by_id', 'crawl_jobs')
    op.drop_index('ix_crawl_jobs_status', 'crawl_jobs')
    op.drop_table('crawl_jobs')
