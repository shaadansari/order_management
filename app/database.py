"""Database engine, session factory, and declarative base.

WHY this file exists separately from models: the engine + Base are infrastructure that
every model imports, so they must live in a leaf module with no business-logic deps.

Engine creation branches on the URL scheme: SQLite (dev/test) gets the thread + pragma
setup it needs; Postgres (production) gets a sized connection pool. The rest of the app
is engine-agnostic — switching DATABASE_URL is the only change required to move between
them (see migrations/ for schema setup).
"""
import logging

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import settings

logger = logging.getLogger(__name__)

_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    # SQLite connections are thread-bound by default; FastAPI serves requests across
    # threads, so cross-thread usage must be explicitly allowed for SQLite only.
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,  # detect dead connections before handing them out
        future=True,         # SQLAlchemy 2.0 style
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        """Enable WAL mode + foreign keys on every new SQLite connection.

        WHY WAL: allows concurrent readers while a writer holds the lock — better
        throughput for the order/payment flows. Writers still serialize, which is
        exactly what protects us from overselling (see order_service.pay_order).
        WHY FK ON: SQLite ignores foreign keys by default; turn them on so the
        constraints in the schema actually fire.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
        except Exception as e:  # pragma: no cover - WAL is always available on modern SQLite
            logger.warning("WAL mode unavailable: %s", e)
        try:
            cursor.execute("PRAGMA foreign_keys=ON;")
        except Exception as e:  # pragma: no cover
            logger.warning("Foreign keys pragma failed: %s", e)
        finally:
            cursor.close()
else:
    # WHY pool_size=5 + max_overflow=10: conservative for managed Postgres free tiers
    # (e.g. Render's free Postgres caps connections). 5 steady + 10 burst = 15 per worker
    # process; with N uvicorn/gunicorn workers that's N*15, comfortably under the cap.
    # WHY pool_pre_ping: a long-idle pooled connection can be killed by the server or a
    # restart; pre-ping discards dead ones instead of handing a broken conn to a request.
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        future=True,
    )

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
