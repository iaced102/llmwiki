"""add url_pattern and max_pages to crawl_jobs

Revision ID: 023
Revises: 022
Create Date: 2026-05-12 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '023'
down_revision: Union[str, None] = '022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'crawl_jobs',
        sa.Column(
            'url_pattern',
            sa.Text(),
            nullable=True,
            comment='Optional regex pattern — only URLs matching this pattern are crawled',
        ),
    )
    op.add_column(
        'crawl_jobs',
        sa.Column(
            'max_pages',
            sa.Integer(),
            nullable=True,
            comment='Maximum number of pages to discover/crawl (null = unlimited)',
        ),
    )


def downgrade() -> None:
    op.drop_column('crawl_jobs', 'max_pages')
    op.drop_column('crawl_jobs', 'url_pattern')
