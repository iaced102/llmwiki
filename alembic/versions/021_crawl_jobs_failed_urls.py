"""add failed_urls to crawl_jobs

Revision ID: 021
Revises: 020
Create Date: 2026-05-11 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = '021'
down_revision: Union[str, None] = '020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'crawl_jobs',
        sa.Column(
            'failed_urls',
            JSONB,
            nullable=False,
            server_default='[]',
            comment='List of URLs that failed to crawl (for retry support)',
        ),
    )


def downgrade() -> None:
    op.drop_column('crawl_jobs', 'failed_urls')
