"""add review to weekly_plans

Revision ID: d2f6b8c3e5a7
Revises: c7e4a9b2d1f6
Create Date: 2026-10-10 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd2f6b8c3e5a7'
down_revision: Union[str, Sequence[str], None] = 'c7e4a9b2d1f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'weekly_plans',
        sa.Column('review', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('weekly_plans', 'review')
