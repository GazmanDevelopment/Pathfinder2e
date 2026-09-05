import json
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

REFERENCE_LIBRARY_DATA_PATH = Path(__file__).resolve().parent / "data" / "reference_library.json"

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

    Phase 7 adds reference_id/reference_version to spells/equipment/features
    the same way — plain nullable columns, no uniqueness needed, so just the
    ADD COLUMN guard, repeated per table.
    """
    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(users)")}
        if "local_username" not in cols:
            conn.exec_driver_sql("ALTER TABLE users ADD COLUMN local_username VARCHAR")
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_local_username ON users(local_username)"
        )
        for table in ("spells", "equipment", "features"):
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if "reference_id" not in cols:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN reference_id INTEGER")
            if "reference_version" not in cols:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN reference_version VARCHAR")
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(spells)")}
        if "uses_current" not in cols:
            conn.exec_driver_sql("ALTER TABLE spells ADD COLUMN uses_current INTEGER")
        if "uses_max" not in cols:
            conn.exec_driver_sql("ALTER TABLE spells ADD COLUMN uses_max INTEGER")
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(characters)")}
        for coin in ("pp", "gp", "sp", "cp"):
            if coin not in cols:
                conn.exec_driver_sql(f"ALTER TABLE characters ADD COLUMN {coin} INTEGER")
        conn.commit()


def seed_reference_library(engine):
    """Loads the vendored Phase 7 ingest snapshot (see
    scripts/ingest_reference_library.py) into the reference_library table.

    Runs at every startup but is a cheap no-op unless the snapshot's
    source_version actually changed — no network or git access happens
    here, only reading a JSON file already baked into the image. Missing
    file means nothing has been ingested yet (a fresh install, or this
    fork/deployment simply hasn't run the ingestion script) — the feature
    stays purely additive, so this quietly does nothing rather than erroring.

    Upserts by (entry_type, foundry_id) rather than delete-and-reinsert,
    since Spell/Equipment/Feature.reference_id is a foreign key into this
    table — replacing rows wholesale would orphan every existing prefilled
    character row's reference_id and break the "differs from book" check.
    An entry that disappears from a later ingest is left in place rather
    than deleted, for the same reason.
    """
    if not REFERENCE_LIBRARY_DATA_PATH.exists():
        return

    from app.models import ReferenceLibrary  # deferred: models.py imports Base from this module

    with REFERENCE_LIBRARY_DATA_PATH.open("r", encoding="utf-8") as f:
        snapshot = json.load(f)
    source_version = snapshot["source_version"]

    with SessionLocal() as db:
        existing_version = db.query(ReferenceLibrary.source_version).first()
        if existing_version is not None and existing_version[0] == source_version:
            return

        existing_by_key = {
            (row.entry_type, row.foundry_id): row for row in db.query(ReferenceLibrary).all()
        }
        copy_fields = (
            "name",
            "source",
            "rank",
            "action_cost",
            "range",
            "uses",
            "effect",
            "damage_formula",
            "level_gained",
            "license",
            "publication_title",
        )
        for entry in snapshot["entries"]:
            key = (entry["entry_type"], entry["foundry_id"])
            row = existing_by_key.get(key)
            if row is None:
                row = ReferenceLibrary(entry_type=entry["entry_type"], foundry_id=entry["foundry_id"])
                db.add(row)
            for field in copy_fields:
                setattr(row, field, entry.get(field))
            row.agile = bool(entry.get("agile", False))
            row.source_version = source_version
        db.commit()


def reference_library_has_entries(engine) -> bool:
    """Whether anything has ever been ingested — gates the site-wide
    attribution notice and (implicitly, via empty search results) the
    reference-search UI on a fresh install with no data seeded yet."""
    with engine.connect() as conn:
        count = conn.exec_driver_sql("SELECT COUNT(*) FROM reference_library").scalar()
    return bool(count)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
