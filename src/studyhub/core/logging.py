import json
import logging
import re
from datetime import UTC, datetime


class RedactCredentials(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = re.sub(
            r"([?&](?:ticket|token|access_token|refresh_token|code|state)=)[^\s\"']+",
            r"\1[REDACTED]",
            record.getMessage(),
        )
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in (
            "request_id",
            "method",
            "path",
            "status",
            "duration_ms",
            "event_id",
            "error_type",
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
    logging.getLogger("uvicorn.error").addFilter(RedactCredentials())
