"""add user timezone

Revision ID: 9e2b7d4c1a6f
Revises: 7c41e2a9d5b3
Create Date: 2026-10-08 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e2b7d4c1a6f'
down_revision: Union[str, Sequence[str], None] = '7c41e2a9d5b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('app_users', sa.Column('timezone', sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('app_users', 'timezone')
