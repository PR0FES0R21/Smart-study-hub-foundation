from sqlalchemy import MetaData, event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

from studyhub.core.config import Settings


@event.listens_for(Session, "after_begin")
def apply_tenant_context(session, transaction, connection):
    # LOCAL scope resets on commit/rollback; reapply on every new transaction.
    connection.execute(
        text("SELECT set_config('app.school_id', :school, true)"),
        {"school": session.info.get("school_id", "")},
    )


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def create_database(settings: Settings):
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=5,
        pool_recycle=1800,
        connect_args={
            "timeout": 5,
            "command_timeout": 30,
            "server_settings": {
                "application_name": "studyhub-api",
                "statement_timeout": "30000",
                "idle_in_transaction_session_timeout": "30000",
            },
        },
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)
