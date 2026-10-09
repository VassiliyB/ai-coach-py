"""add book_chunks (фрагменты книг для /ask, полнотекстовый поиск)

Revision ID: 1b7e5c9d3a2f
Revises: d2f6b8c3e5a7
Create Date: 2026-10-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '1b7e5c9d3a2f'
down_revision: Union[str, Sequence[str], None] = 'd2f6b8c3e5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Копия models.book_chunk.SEARCH_VECTOR_SQL: миграция не должна зависеть от будущих правок модели
SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('russian'::regconfig, translate(coalesce(chapter, '') || ' ' || coalesce(section, ''),"
    " 'ёЁ', 'еЕ')), 'A') || "
    "setweight(to_tsvector('russian'::regconfig, translate(content, 'ёЁ', 'еЕ')), 'B')"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'book_chunks',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('book_key', sa.String(length=255), nullable=False),
        sa.Column('author', sa.String(length=255), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('chapter', sa.String(length=500), nullable=False),
        sa.Column('section', sa.String(length=500), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed(SEARCH_VECTOR_SQL, persisted=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_book_chunks')),
        sa.UniqueConstraint('book_key', 'position', name='uq_book_chunks_book_position'),
    )
    op.create_index('ix_book_chunks_search_vector', 'book_chunks', ['search_vector'], postgresql_using='gin')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_book_chunks_search_vector', table_name='book_chunks', postgresql_using='gin')
    op.drop_table('book_chunks')
