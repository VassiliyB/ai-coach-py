"""add user access (доступ к боту по одобрению админа)

Revision ID: e3a9f6c2b8d4
Revises: 1b7e5c9d3a2f
Create Date: 2026-10-09 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3a9f6c2b8d4'
down_revision: Union[str, Sequence[str], None] = '1b7e5c9d3a2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Кто уже пользуется ботом, сохраняет доступ; новые пользователи ждут одобрения
    op.add_column(
        'app_users',
        sa.Column('access', sa.String(length=16), server_default='approved', nullable=False),
    )
    op.alter_column('app_users', 'access', server_default='pending')
    op.create_check_constraint(
        op.f('ck_app_users_access'), 'app_users', "access IN ('pending', 'approved', 'blocked')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_app_users_access'), 'app_users', type_='check')
    op.drop_column('app_users', 'access')
