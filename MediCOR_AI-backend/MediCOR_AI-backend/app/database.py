import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

logger = logging.getLogger("database")


# ============================================================
# SQL SERVER ONLY
# ============================================================

DATABASE_URL = settings.resolved_database_url


# ============================================================
# ENGINE CONFIGURATION
# ============================================================

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout_seconds,
    fast_executemany=True,
)


# ============================================================
# SESSION
# ============================================================

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# ============================================================
# BASE MODEL
# ============================================================

Base = declarative_base()


# ============================================================
# DATABASE DEPENDENCY
# ============================================================

def get_db():
    """
    Request-scoped SQL Server database session.

    Rolls back the current transaction if an exception occurs,
    then always closes the session.
    """

    db = SessionLocal()

    try:
        yield db

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# ============================================================
# SQL SERVER SCHEMA SYNC
# ============================================================

def sync_missing_columns(base=Base, bind=engine):
    """
    Add missing nullable columns to existing SQL Server tables.

    This is an additive schema synchronization helper.

    It:
      - does NOT delete tables
      - does NOT delete data
      - does NOT rename columns
      - does NOT modify existing columns
      - only adds missing columns

    SQL Server only.
    """

    inspector = inspect(bind)

    existing_tables = set(
        inspector.get_table_names()
    )

    preparer = bind.dialect.identifier_preparer

    with bind.begin() as conn:

        for table in base.metadata.sorted_tables:

            # Table does not exist yet.
            # create_all() handles brand-new tables.
            if table.name not in existing_tables:
                continue

            existing_columns = {
                column["name"]
                for column in inspector.get_columns(table.name)
            }

            for column in table.columns:

                if column.name in existing_columns:
                    continue

                column_type = column.type.compile(
                    dialect=bind.dialect
                )

                table_identifier = preparer.quote(
                    table.name
                )

                column_identifier = preparer.quote(
                    column.name
                )

                # SQL Server syntax:
                #
                # ALTER TABLE table
                # ADD column datatype
                ddl = (
                    f"ALTER TABLE "
                    f"{table_identifier} "
                    f"ADD "
                    f"{column_identifier} "
                    f"{column_type}"
                )

                conn.execute(text(ddl))

                logger.warning(
                    "schema sync: added missing column "
                    f"{table.name}.{column.name} "
                    f"({column_type})"
                )