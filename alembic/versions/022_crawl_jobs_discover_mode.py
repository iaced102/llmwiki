"""add discover mode fields to crawl_jobs

Revision ID: 022
Revises: 021
Create Date: 2026-05-11 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = '022'
down_revision: Union[str, None] = '021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # mode: "discover" = BFS collect links only, "crawl" = render PDF + ingest
    op.add_column(
        'crawl_jobs',
        sa.Column(
            'mode',
            sa.String(20),
            nullable=False,
            server_default='crawl',
            comment='discover | crawl',
        ),
    )
    # All URLs discovered during BFS (populated in discover mode)
    op.add_column(
        'crawl_jobs',
        sa.Column(
            'discovered_urls',
            JSONB,
            nullable=False,
            server_default='[]',
            comment='All URLs found during BFS discovery phase',
        ),
    )
    # URLs selected by user to actually crawl (populated when starting crawl)
    op.add_column(
        'crawl_jobs',
        sa.Column(
            'selected_urls',
            JSONB,
            nullable=False,
            server_default='[]',
            comment='URLs selected by user to crawl and ingest',
        ),
    )


def downgrade() -> None:
    op.drop_column('crawl_jobs', 'selected_urls')
    op.drop_column('crawl_jobs', 'discovered_urls')
    op.drop_column('crawl_jobs', 'mode')
