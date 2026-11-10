import logging
from uuid import UUID

from celery.signals import task_postrun, task_prerun

from studyhub.core.context import actor_id_context, request_id_context, school_id_context

logger = logging.getLogger(__name__)


@task_prerun.connect
def start_task(task_id=None, task=None, **kwargs):
    header = (getattr(task.request, "headers", None) or {}).get("request_id")
    try:
        request_id_context.set(str(UUID(header)) if header else None)
    except (ValueError, TypeError, AttributeError):
        request_id_context.set(None)
    logger.info("worker_task_started", extra={"task_id": task_id})


@task_postrun.connect
def end_task(task_id=None, state=None, **kwargs):
    logger.info("worker_task_finished", extra={"task_id": task_id, "outcome": state})
    for context in (request_id_context, actor_id_context, school_id_context):
        context.set(None)
