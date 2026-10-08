"""plan_details to jsonb

Revision ID: 7c41e2a9d5b3
Revises: 3a6c21c88a4f
Create Date: 2026-10-08 12:00:00.000000

Старые текстовые планы сохраняются как {"legacy_text": "..."}.
Downgrade возвращает текст из legacy_text, а структурный план превращает в строку JSON.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7c41e2a9d5b3'
down_revision: Union[str, Sequence[str], None] = '3a6c21c88a4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("training_plans", "weekly_plans")


def upgrade() -> None:
    """Upgrade schema."""
    for table in TABLES:
        op.alter_column(
            table, "plan_details",
            existing_type=sa.Text(),
            type_=postgresql.JSONB(),
            existing_nullable=False,
            postgresql_using="jsonb_build_object('legacy_text', plan_details)",
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in TABLES:
        op.alter_column(
            table, "plan_details",
            existing_type=postgresql.JSONB(),
            type_=sa.Text(),
            existing_nullable=False,
            postgresql_using="COALESCE(plan_details ->> 'legacy_text', plan_details::text)",
        )
