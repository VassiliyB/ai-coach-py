"""add manual max hr and lthr to athlete_profiles

Revision ID: a8c2e5f9d1b3
Revises: f4b1d7a3c9e2
Create Date: 2026-10-09 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8c2e5f9d1b3'
down_revision: Union[str, Sequence[str], None] = 'f4b1d7a3c9e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('athlete_profiles', sa.Column('manual_max_hr', sa.Integer(), nullable=True))
    op.add_column('athlete_profiles', sa.Column('lthr', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('athlete_profiles', 'lthr')
    op.drop_column('athlete_profiles', 'manual_max_hr')
