"""add organisations

Revision ID: b1c2d3e4f5a6
Revises: a3c1d2e4f5b6
Create Date: 2026-03-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import secrets

# revision identifiers
revision = 'b1c2d3e4f5a6'
down_revision = 'a3c1d2e4f5b6'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Create organizations table
    op.create_table(
        "organizations",
        sa.Column("id",          sa.Integer(),    primary_key=True),
        sa.Column("name",        sa.String(),     nullable=False),
        sa.Column("iot_api_key", sa.String(),     nullable=True,  unique=True),
        sa.Column("created_at",  sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 2. Seed a default demo org so existing data is not orphaned
    op.execute(
        f"INSERT INTO organizations (id, name, iot_api_key) VALUES (1, 'GreenPulse Demo', '{secrets.token_hex(32)}')"
    )

    # 3. Add organization_id column to each data table (nullable integer, no FK constraint
    #    because SQLite does not support adding FK constraints via ALTER TABLE)
    for table in ("users", "energy_readings", "waste_logs", "insights", "notifications"):
        op.add_column(
            table,
            sa.Column("organization_id", sa.Integer(), nullable=True),
        )

    # 4. Assign all existing rows to the demo org
    for table in ("users", "energy_readings", "waste_logs", "insights", "notifications"):
        op.execute(f"UPDATE {table} SET organization_id = 1 WHERE organization_id IS NULL")


def downgrade():
    for table in ("users", "energy_readings", "waste_logs", "insights", "notifications"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("organization_id")
    op.drop_table("organizations")
