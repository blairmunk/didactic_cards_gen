from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable

from ..domain.interfaces import CompileResult


_EXTRA_FIELDS = (
    'event', 'request_id', 'method', 'path', 'status', 'duration_ms',
    'job_kind', 'profile_id', 'deck_id', 'side', 'error_kind',
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


def run_observed_pdf_compilation(
    action: Callable[[], CompileResult],
    *,
    logger: logging.Logger,
    request_id: str,
    job_kind: str,
    profile_id: str,
    deck_id: str | None = None,
    side: str | None = None,
    validation_errors: tuple[type[Exception], ...] = (),
) -> CompileResult:
    """Run one PDF job and emit exactly one deliberately sanitised metric."""
    started = time.perf_counter()
    try:
        result = action()
    except Exception as error:
        _log_pdf_compilation(
            logger,
            request_id=request_id,
            job_kind=job_kind,
            profile_id=profile_id,
            deck_id=deck_id,
            side=side,
            status='failure',
            error_kind=(
                'validation'
                if isinstance(error, validation_errors)
                else 'internal'
            ),
            started=started,
        )
        raise

    _log_pdf_compilation(
        logger,
        request_id=request_id,
        job_kind=job_kind,
        profile_id=profile_id,
        deck_id=deck_id,
        side=side,
        status='success' if result.success else 'failure',
        error_kind=result.error_kind,
        started=started,
    )
    return result


def _log_pdf_compilation(
    logger: logging.Logger,
    *,
    request_id: str,
    job_kind: str,
    profile_id: str,
    deck_id: str | None,
    side: str | None,
    status: str,
    error_kind: str | None,
    started: float,
) -> None:
    logger.info(
        'pdf_compilation',
        extra={
            'event': 'pdf_compilation',
            'request_id': request_id,
            'job_kind': job_kind,
            'profile_id': profile_id,
            'deck_id': deck_id,
            'side': side,
            'status': status,
            'error_kind': error_kind,
            'duration_ms': round((time.perf_counter() - started) * 1000, 3),
        },
    )
