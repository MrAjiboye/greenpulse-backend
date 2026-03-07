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

Base.metadata.create_all(engine)

# Idempotently add any columns that may have been missing from earlier deployments
with engine.connect() as conn:
    # email_verified (added after initial schema)
    row = conn.execute(sa.text(
        \"SELECT column_name FROM information_schema.columns \"
        \"WHERE table_name='users' AND column_name='email_verified'\"
    )).fetchone()
    if not row:
        conn.execute(sa.text(
            'ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT FALSE'
        ))
        conn.commit()
        print('Added missing email_verified column.')
    else:
        print('email_verified column OK.')

print('Database tables created/verified.')
"

# Stamp alembic as head so it doesn't try to run the incremental migrations
# (which were written for SQLite and assume tables already exist)
alembic stamp head

echo "Starting uvicorn on port $PORT..."
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT
