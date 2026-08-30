import sqlite3

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def run_startup_migrations(engine):
    """Ad-hoc, idempotent migrations for columns added after a table already
    existed on a live deployment. Base.metadata.create_all() only creates
    missing tables — it never alters an existing one — and this project has
    no migration framework (every prior phase's schema change shipped before
    real data existed, so "reset the dev DB" was enough; that stopped being
    true once the box got a real users/characters table).

    Phase 4c's User.local_username is the first column added since real data
    exists: SQLite has no ADD COLUMN IF NOT EXISTS, hence the PRAGMA guard,
    and no ADD COLUMN ... UNIQUE, hence the separate index (which itself
    supports IF NOT EXISTS and correctly allows unlimited NULLs while still
    enforcing uniqueness among the non-null values).
    """
    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(users)")}
        if "local_username" not in cols:
            conn.exec_driver_sql("ALTER TABLE users ADD COLUMN local_username VARCHAR")
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_local_username ON users(local_username)"
        )
        conn.commit()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
