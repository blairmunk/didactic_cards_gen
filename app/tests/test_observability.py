from __future__ import annotations

import json
import logging

from didactic_cards.web.observability import JsonLogFormatter, configure_json_logging


def test_json_formatter_emits_safe_structured_fields():
    record = logging.LogRecord(
        name='test', level=logging.INFO, pathname=__file__, lineno=1,
        msg='pdf_compilation', args=(), exc_info=None,
    )
    record.event = 'pdf_compilation'
    record.request_id = 'request-1'
    record.deck_id = 'deck-1'
    record.duration_ms = 12.5
    record.private_path = '/private/path'

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload['message'] == 'pdf_compilation'
    assert payload['request_id'] == 'request-1'
    assert payload['duration_ms'] == 12.5
    assert 'private_path' not in payload


def test_json_formatter_reports_exception_type_without_exception_message():
    try:
        raise RuntimeError('/private/path')
    except RuntimeError:
        import sys
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name='test', level=logging.ERROR, pathname=__file__, lineno=1,
        msg='failed', args=(), exc_info=exc_info,
    )
    payload = json.loads(JsonLogFormatter().format(record))
    assert payload['exception'] == 'RuntimeError'
    assert '/private/path' not in json.dumps(payload)


def test_configure_json_logging_updates_existing_handlers():
    logger = logging.Logger('test-json-logger')
    handler = logging.StreamHandler()
    logger.addHandler(handler)
    configure_json_logging(logger)
    assert isinstance(handler.formatter, JsonLogFormatter)


def test_configure_json_logging_adds_handler_when_missing():
    logger = logging.Logger('test-empty-logger')
    configure_json_logging(logger)
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0].formatter, JsonLogFormatter)
