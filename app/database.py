from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

_db_url = settings.DATABASE_URL
# Railway gives postgres:// — SQLAlchemy requires postgresql://
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)

IS_SQLITE = "sqlite" in _db_url

# Create database engine
engine = create_engine(
    _db_url,
    connect_args={"check_same_thread": False} if IS_SQLITE else {},
    pool_pre_ping=True,       # reconnect on stale connections (Neon closes idle conns)
    pool_recycle=300,         # recycle connections every 5 min
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()

# Dependency to get database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def naive_utc(dt: datetime) -> datetime:
    """
    SQLite stores datetimes without timezone — comparisons must use naive UTC.
    PostgreSQL stores timezone-aware datetimes — comparisons use aware UTC.
    Normalises a UTC datetime for whichever engine is active.
    """
    return dt.replace(tzinfo=None) if IS_SQLITE else dt