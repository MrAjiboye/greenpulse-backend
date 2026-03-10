#!/bin/sh
set -e

# Fix postgres:// → postgresql:// for SQLAlchemy + Alembic (Railway gives the old form)
if echo "$DATABASE_URL" | grep -q "^postgres://"; then
    export DATABASE_URL=$(echo "$DATABASE_URL" | sed 's|^postgres://|postgresql://|')
fi

echo "Starting GreenPulse backend..."

# Create all tables from current models (safe on existing DB — won't drop anything)
python -c "
from app.database import engine
from app.models import Base
import sqlalchemy as sa

try:
    Base.metadata.create_all(engine)
except Exception as e:
    print(f'Warning: create_all had a partial error (likely enum type conflict): {e}')
    print('Continuing — individual tables will be created below if missing.')

# Idempotently add any columns that may have been missing from earlier deployments
with engine.connect() as conn:
    # Idempotently add all columns that may be missing from earlier deployments.
    # ADD COLUMN IF NOT EXISTS is a no-op when the column already exists (PostgreSQL 9.6+).
    missing_col_stmts = [
        'ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE',
        'ALTER TABLE users ADD COLUMN IF NOT EXISTS job_title TEXT',
        'ALTER TABLE users ADD COLUMN IF NOT EXISTS department TEXT',
        'ALTER TABLE users ADD COLUMN IF NOT EXISTS company_name TEXT',
        'ALTER TABLE users ADD COLUMN IF NOT EXISTS oauth_provider TEXT',
        'ALTER TABLE users ADD COLUMN IF NOT EXISTS oauth_sub TEXT',
    ]
    for stmt in missing_col_stmts:
        conn.execute(sa.text(stmt))

    # Org billing columns
    org_col_stmts = [
        \"ALTER TABLE organizations ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free'\",
        'ALTER TABLE organizations ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT',
        'ALTER TABLE organizations ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ',
    ]
    for stmt in org_col_stmts:
        conn.execute(sa.text(stmt))

    conn.commit()
    print('User column migrations applied.')

    # goals table
    conn.execute(sa.text(\"\"\"
        CREATE TABLE IF NOT EXISTS goals (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER REFERENCES organizations(id),
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            target_value FLOAT NOT NULL,
            unit TEXT NOT NULL,
            period_start TIMESTAMPTZ NOT NULL,
            period_end TIMESTAMPTZ NOT NULL,
            created_by INTEGER REFERENCES users(id),
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    \"\"\"))

    # team_invites table
    conn.execute(sa.text(\"\"\"
        CREATE TABLE IF NOT EXISTS team_invites (
            id SERIAL PRIMARY KEY,
            email TEXT NOT NULL,
            organization_id INTEGER REFERENCES organizations(id),
            role TEXT NOT NULL DEFAULT 'VIEWER',
            token_hash TEXT NOT NULL,
            invited_by INTEGER REFERENCES users(id),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            accepted_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ NOT NULL
        )
    \"\"\"))
    conn.commit()

print('Database tables created/verified.')
"

# Stamp alembic as head so it doesn't try to run the incremental migrations
# (which were written for SQLite and assume tables already exist)
alembic stamp head

echo "Starting uvicorn on port $PORT..."
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT
