"""add email_verified to users

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-03-03 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'c2d3e4f5a6b7'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade():
    # Add email_verified with server_default='1' so ALL existing users stay verified
    op.add_column(
        'users',
        sa.Column('email_verified', sa.Boolean(), nullable=False, server_default='1'),
    )


def downgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('email_verified')
