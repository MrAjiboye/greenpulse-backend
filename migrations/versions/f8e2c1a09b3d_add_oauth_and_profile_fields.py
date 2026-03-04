"""add oauth and profile fields

Revision ID: f8e2c1a09b3d
Revises: 955fc3db6e02
Create Date: 2026-02-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8e2c1a09b3d'
down_revision: Union[str, None] = '955fc3db6e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('job_title', sa.String(), nullable=True))
    op.add_column('users', sa.Column('department', sa.String(), nullable=True))
    op.add_column('users', sa.Column('oauth_provider', sa.String(), nullable=True))
    op.add_column('users', sa.Column('oauth_sub', sa.String(), nullable=True))
    op.create_index(op.f('ix_users_oauth_sub'), 'users', ['oauth_sub'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_oauth_sub'), table_name='users')
    op.drop_column('users', 'oauth_sub')
    op.drop_column('users', 'oauth_provider')
    op.drop_column('users', 'department')
    op.drop_column('users', 'job_title')
