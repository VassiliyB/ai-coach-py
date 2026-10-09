"""add race result to athlete_profiles

Revision ID: f4b1d7a3c9e2
Revises: e3a9f6c2b8d4
Create Date: 2026-10-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b1d7a3c9e2'
down_revision: Union[str, Sequence[str], None] = 'e3a9f6c2b8d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('athlete_profiles', sa.Column('race_result_distance_m', sa.Float(), nullable=True))
    op.add_column('athlete_profiles', sa.Column('race_result_time_s', sa.Integer(), nullable=True))
    op.add_column('athlete_profiles', sa.Column('race_result_date', sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('athlete_profiles', 'race_result_date')
    op.drop_column('athlete_profiles', 'race_result_time_s')
    op.drop_column('athlete_profiles', 'race_result_distance_m')
