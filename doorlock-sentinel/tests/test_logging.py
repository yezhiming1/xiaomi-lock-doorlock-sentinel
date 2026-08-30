from __future__ import annotations

import json
import logging

from doorlock_sentinel.logging_setup import JsonFormatter


def test_json_logs_do_not_serialize_exception_details():
    formatter = JsonFormatter()
    try:
        raise RuntimeError("must-not-leak")
    except RuntimeError:
        record = logging.LogRecord(
            name="doorlock.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="fixed error code=TEST_FAILED",
            args=(),
            exc_info=True,
        )

    payload = json.loads(formatter.format(record))
    assert payload["message"] == "fixed error code=TEST_FAILED"
    assert "exception" not in payload
    assert "must-not-leak" not in formatter.format(record)
