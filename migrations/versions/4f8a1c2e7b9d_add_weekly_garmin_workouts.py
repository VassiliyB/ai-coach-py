"""add garmin_workouts to weekly_plans

Revision ID: 4f8a1c2e7b9d
Revises: 9e2b7d4c1a6f
Create Date: 2026-10-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '4f8a1c2e7b9d'
down_revision: Union[str, Sequence[str], None] = '9e2b7d4c1a6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'weekly_plans',
        sa.Column('garmin_workouts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('weekly_plans', 'garmin_workouts')
