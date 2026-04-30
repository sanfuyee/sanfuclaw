"""JSONFormatter — extra fields flow through, standard fields don't."""

from __future__ import annotations

import json
import logging

from sanfuclaw.core.logging import JSONFormatter


def _make_record(msg: str, **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="sanfuclaw.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def test_json_formatter_emits_minimal_record():
    payload = json.loads(JSONFormatter().format(_make_record("hello")))
    assert payload["msg"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "sanfuclaw.test"
    assert "ts" in payload
    # No standard junk leaked.
    assert "args" not in payload
    assert "msecs" not in payload


def test_json_formatter_passes_through_extras():
    record = _make_record(
        "tool call",
        session_id="abc12345",
        turn_id="xyz98765",
        tool="shell",
        round=2,
    )
    payload = json.loads(JSONFormatter().format(record))
    assert payload["session_id"] == "abc12345"
    assert payload["turn_id"] == "xyz98765"
    assert payload["tool"] == "shell"
    assert payload["round"] == 2


def test_json_formatter_handles_unserializable_extras():
    class Weird:
        def __repr__(self):
            return "<weird>"

    record = _make_record("x", thing=Weird())
    payload = json.loads(JSONFormatter().format(record))
    assert payload["thing"] == "<weird>"


def test_json_formatter_includes_exception():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys
        record = logging.LogRecord(
            name="x", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="failed", args=(), exc_info=sys.exc_info(),
        )
    payload = json.loads(JSONFormatter().format(record))
    assert "boom" in payload["exc"]
    assert "RuntimeError" in payload["exc"]
