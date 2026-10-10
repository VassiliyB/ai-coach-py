"""add reminder_hour to app_users

Revision ID: c3e7a1f5b9d2
Revises: b9d4f2a6c8e1
Create Date: 2026-10-10 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3e7a1f5b9d2'
down_revision: Union[str, Sequence[str], None] = 'b9d4f2a6c8e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('app_users', sa.Column('reminder_hour', sa.SmallInteger(), nullable=True))
    op.create_check_constraint(op.f('ck_app_users_reminder_hour'), 'app_users', 'reminder_hour BETWEEN -1 AND 23')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_app_users_reminder_hour'), 'app_users', type_='check')
    op.drop_column('app_users', 'reminder_hour')
