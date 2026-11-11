from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging

from studyhub.core.config import get_settings
from studyhub.core.logging import configure_logging
from studyhub.worker import observability  # noqa: F401

setup_logging.connect(configure_logging)

settings = get_settings()
celery_app = Celery(
    "studyhub",
    broker=settings.broker_url.get_secret_value(),
    include=["studyhub.worker.tasks", "studyhub.worker.exams"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_default_queue=f"{settings.namespace}:tasks",
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": 300,
        "socket_connect_timeout": 3,
        "socket_timeout": 3,
    },
    task_soft_time_limit=120,
    task_time_limit=150,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "expire-exam-attempts": {"task": "studyhub.expire_exam_attempts", "schedule": 30.0},
        "cleanup-operations": {
            "task": "studyhub.cleanup_operations",
            "schedule": crontab(hour=3, minute=15),
        },
        "dispatch-outbox": {"task": "studyhub.dispatch_outbox", "schedule": 2.0},
        "reconcile-pending-jobs": {"task": "studyhub.reconcile_jobs", "schedule": 60.0},
    },
)
