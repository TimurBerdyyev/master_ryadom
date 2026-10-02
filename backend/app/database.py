from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

# SQLite is only for quick local runs without Postgres: allow use from FastAPI's threadpool.
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_unicode_lower(dbapi_connection, _record):
        # SQLite's built-in lower() is ASCII-only, so ILIKE would be case-sensitive for Cyrillic.
        dbapi_connection.create_function("lower", 1, lambda v: v.lower() if isinstance(v, str) else v)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
