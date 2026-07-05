"""Database engine, session factory, and declarative base.

WHY this file exists separately from models: the engine + Base are infrastructure that
every model imports, so they must live in a leaf module with no business-logic deps.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import settings

# SQLite connections are thread-bound by default; FastAPI serves requests across
# threads, so cross-thread usage must be explicitly allowed for SQLite only.
_is_sqlite = settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,   # detect dead connections before handing them out
    future=True,          # SQLAlchemy 2.0 style
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        """Enable WAL mode + foreign keys on every new SQLite connection.

        WHY WAL: allows concurrent readers while a writer holds the lock — better
        throughput for the order/payment flows. Writers still serialize, which is
        exactly what protects us from overselling (see order_service.create_order).
        WHY FK ON: SQLite ignores foreign keys by default; turn them on so the
        constraints in the schema actually fire.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,       # don't issue implicit SELECTs before queries — fewer surprises
    autocommit=False,
    future=True,
    expire_on_commit=False,  # keep objects usable after commit (background tasks read them)
)

Base = declarative_base()


def get_db() -> Session:
    """FastAPI dependency: yield a fresh DB session per request and always close it.

    WHY a generator: guarantees the session is closed even if the route raises, so we
    never leak connections under error paths.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
