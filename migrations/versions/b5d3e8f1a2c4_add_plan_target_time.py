"""add target_time_s to training_plans

Revision ID: b5d3e8f1a2c4
Revises: 4f8a1c2e7b9d
Create Date: 2026-10-09 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5d3e8f1a2c4'
down_revision: Union[str, Sequence[str], None] = '4f8a1c2e7b9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('training_plans', sa.Column('target_time_s', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('training_plans', 'target_time_s')
