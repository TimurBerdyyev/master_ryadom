"""Apply Alembic migrations at startup.

New migration after changing models:
    cd backend && alembic revision --autogenerate -m "what changed"
then review the generated file in alembic/versions/ — autogenerate can't detect everything (e.g. renames).
"""
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

from app.database import Base, engine as app_engine

logger = logging.getLogger("master_ryadom")

BACKEND_DIR = Path(__file__).resolve().parent.parent
# Databases created before migrations existed (via create_all) are brought to this revision.
BASELINE_REVISION = "0001"
# Tables created by revision 0001. A pre-migration database may lack some of them
# (e.g. it was created before paid plans existed), so they are filled in before stamping.
BASELINE_TABLES = {
    "categories", "users", "addresses", "complaints", "masters", "notifications", "master_subscriptions",
    "orders", "services", "subscription_payments", "working_hours", "messages", "order_offers", "payments",
    "photos", "reviews",
}


def _config(engine: Engine) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["configure_logger"] = False
    cfg.attributes["database_url"] = engine.url.render_as_string(hide_password=False)
    return cfg


def upgrade_database(engine: Engine = app_engine) -> None:
    cfg = _config(engine)
    tables = set(inspect(engine).get_table_names())
    if tables and "alembic_version" not in tables:
        missing = BASELINE_TABLES - tables
        logger.info(
            "База без истории миграций: досоздаю таблицы %s и помечаю как ревизию %s",
            sorted(missing) or "—", BASELINE_REVISION,
        )
        if missing:
            # These tables haven't changed since 0001, so the current models describe them exactly.
            Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name in missing])
        command.stamp(cfg, BASELINE_REVISION)
    command.upgrade(cfg, "head")
