import os
from functools import lru_cache

from celery.signals import worker_process_shutdown
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from studyhub.core.config import get_settings


@lru_cache
def resources(pid: int):
    """Created lazily inside each child; no live pool is inherited across a fork."""
    settings = get_settings()
    engine = create_engine(
        settings.sync_database_url,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=30000 -c idle_in_transaction_session_timeout=30000",
        },
    )
    control = Redis.from_url(
        settings.control_url.get_secret_value(), socket_timeout=3, socket_connect_timeout=2
    )
    cache = Redis.from_url(
        settings.cache_url.get_secret_value(), socket_timeout=3, socket_connect_timeout=2
    )
    return engine, sessionmaker(engine, expire_on_commit=False), control, cache


def get_resources():
    return resources(os.getpid())


@worker_process_shutdown.connect
def shutdown_resources(**kwargs):
    if resources.cache_info().currsize:
        engine, _, control, cache = get_resources()
        control.close()
        cache.close()
        engine.dispose()
        resources.cache_clear()
