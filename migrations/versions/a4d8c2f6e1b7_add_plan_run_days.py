"""add run_days to training_plans

Revision ID: a4d8c2f6e1b7
Revises: c3e7a1f5b9d2
Create Date: 2026-10-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4d8c2f6e1b7'
down_revision: Union[str, Sequence[str], None] = 'c3e7a1f5b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('training_plans', sa.Column('run_days', sa.SmallInteger(), nullable=True))
    op.create_check_constraint(op.f('ck_training_plans_run_days'), 'training_plans', 'run_days BETWEEN 3 AND 6')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_training_plans_run_days'), 'training_plans', type_='check')
    op.drop_column('training_plans', 'run_days')
