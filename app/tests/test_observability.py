from __future__ import annotations

import json
import logging

import pytest

from didactic_cards.domain.interfaces import CompileResult
from didactic_cards.web.observability import (
    JsonLogFormatter,
    configure_json_logging,
    run_observed_pdf_compilation,
)


def test_json_formatter_emits_safe_structured_fields():
    record = logging.LogRecord(
        name='test', level=logging.INFO, pathname=__file__, lineno=1,
        msg='pdf_compilation', args=(), exc_info=None,
    )
    record.event = 'pdf_compilation'
    record.request_id = 'request-1'
    record.job_kind = 'calibration'
    record.profile_id = 'office-printer'
    record.deck_id = 'deck-1'
    record.duration_ms = 12.5
    record.private_path = '/private/path'

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload['message'] == 'pdf_compilation'
    assert payload['request_id'] == 'request-1'
    assert payload['job_kind'] == 'calibration'
    assert payload['profile_id'] == 'office-printer'
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


@pytest.mark.parametrize(
    ('result', 'expected_status', 'expected_error_kind'),
    [
        (CompileResult(True, b'%PDF', ''), 'success', None),
        (
            CompileResult(False, b'', '/private/compiler.log', 'timeout'),
            'failure',
            'timeout',
        ),
        (
            CompileResult(False, b'', '/private/compiler.log', 'compile-error'),
            'failure',
            'compile-error',
        ),
    ],
)
def test_observed_pdf_compilation_logs_one_safe_correlated_event(
    result, expected_status, expected_error_kind
):
    events = []
    logger = logging.Logger('pdf-job-test')
    logger.info = lambda message, *, extra: events.append((message, extra))

    returned = run_observed_pdf_compilation(
        lambda: result,
        logger=logger,
        request_id='request-42',
        job_kind='calibration',
        profile_id='office-printer',
        side='calibration',
    )

    assert returned is result
    assert len(events) == 1
    message, metric = events[0]
    assert message == 'pdf_compilation'
    assert metric == {
        'event': 'pdf_compilation',
        'request_id': 'request-42',
        'job_kind': 'calibration',
        'profile_id': 'office-printer',
        'deck_id': None,
        'side': 'calibration',
        'status': expected_status,
        'error_kind': expected_error_kind,
        'duration_ms': metric['duration_ms'],
    }
    assert metric['duration_ms'] >= 0
    assert '/private' not in repr(metric)


@pytest.mark.parametrize(
    ('error_type', 'expected_error_kind'),
    [(ValueError, 'validation'), (RuntimeError, 'internal')],
)
def test_observed_pdf_compilation_sanitises_raised_errors(
    error_type, expected_error_kind
):
    events = []
    logger = logging.Logger('pdf-job-exception-test')
    logger.info = lambda message, *, extra: events.append((message, extra))

    def fail():
        raise error_type('/private/secret.tex')

    with pytest.raises(error_type, match='/private/secret'):
        run_observed_pdf_compilation(
            fail,
            logger=logger,
            request_id='request-43',
            job_kind='calibration',
            profile_id='base',
            validation_errors=(ValueError,),
        )

    assert len(events) == 1
    assert events[0][1]['error_kind'] == expected_error_kind
    assert events[0][1]['status'] == 'failure'
    assert '/private' not in repr(events[0][1])
