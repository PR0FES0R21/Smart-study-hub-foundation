import json
import logging
import re
from datetime import UTC, datetime

from studyhub.core.context import actor_id_context, request_id_context, school_id_context


class RedactCredentials(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = re.sub(
            r"([?&](?:ticket|token|access_token|refresh_token|code|state)=)[^\s\"']+",
            r"\1[REDACTED]",
            record.getMessage(),
        )
        record.args = ()
        record.msg = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~-]+", r"\1[REDACTED]", record.msg)
        record.msg = re.sub(r"(://[^\s/:]+:)[^\s@]+@", r"\1[REDACTED]@", record.msg)
        record.msg = re.sub(
            r"""(?i)((?:password|client_secret|refresh_token|access_token)\s*[=:]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)""",
            r"\1[REDACTED]",
            record.msg,
        )
        if record.exc_info:
            record.error_type = record.exc_info[0].__name__
            record.msg = "exception_suppressed"  # SQL errors can contain query parameters/PII.
            record.exc_info = None
            record.exc_text = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name, context in (
            ("request_id", request_id_context),
            ("actor_id", actor_id_context),
            ("school_id", school_id_context),
        ):
            if context.get():
                data[name] = context.get()
        for field in (
            "request_id",
            "method",
            "path",
            "status",
            "duration_ms",
            "event_id",
            "error_type",
            "actor_id",
            "school_id",
            "action",
            "outcome",
            "task_id",
            "resource_type",
            "resource_id",
        ):
            if hasattr(record, field):
                data[field] = getattr(record, field)
        return json.dumps(data)


def configure_logging(**kwargs):
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactCredentials())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # Uvicorn logs WebSocket handshake URLs on its error logger, even with --no-access-log.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
