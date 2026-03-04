"""add company_name to users

Revision ID: a3c1d2e4f5b6
Revises: f8e2c1a09b3d
Create Date: 2026-03-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'a3c1d2e4f5b6'
down_revision = '19da78f1e167'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('company_name', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'company_name')
