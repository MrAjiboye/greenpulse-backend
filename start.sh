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
Base.metadata.create_all(engine)
print('Database tables created/verified.')
"

# Stamp alembic as head so it doesn't try to run the incremental migrations
# (which were written for SQLite and assume tables already exist)
alembic stamp head

echo "Starting uvicorn on port $PORT..."
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT
