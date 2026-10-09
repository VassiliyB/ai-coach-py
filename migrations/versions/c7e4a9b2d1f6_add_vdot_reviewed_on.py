"""add vdot_reviewed_on to athlete_profiles

Revision ID: c7e4a9b2d1f6
Revises: b5d3e8f1a2c4
Create Date: 2026-10-09 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e4a9b2d1f6'
down_revision: Union[str, Sequence[str], None] = 'b5d3e8f1a2c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('athlete_profiles', sa.Column('vdot_reviewed_on', sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('athlete_profiles', 'vdot_reviewed_on')
