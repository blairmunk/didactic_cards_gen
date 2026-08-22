from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


_EXTRA_FIELDS = (
    'event', 'request_id', 'method', 'path', 'status', 'duration_ms',
    'deck_id', 'side', 'error_kind',
)


class JsonLogFormatter(logging.Formatter):
    """One-line JSON logs with a deliberately small, non-sensitive schema."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': record.levelname,
            'message': record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload['exception'] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


def configure_json_logging(logger: logging.Logger) -> None:
    formatter = JsonLogFormatter()
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())
    for handler in logger.handlers:
        handler.setFormatter(formatter)
