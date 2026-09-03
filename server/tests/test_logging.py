"""pmchaser.logging.log_tool_call - the Phase 2 fix for finding #14 (no
logging at all in server code). Not wired in as a decorator on the MCP
tool adapters (see pmchaser/mcp/tools.py's and pmchaser/logging.py's
module docstrings for why) - tested directly here instead."""

from __future__ import annotations

import logging

import pytest

from pmchaser.logging import log_tool_call


def test_logs_ok_outcome_for_a_plain_successful_call(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.INFO, logger="pmchaser"):
        result = log_tool_call("fake_tool", lambda: {"value": 1})

    assert result == {"value": 1}
    assert len(caplog.records) == 1
    assert "tool=fake_tool" in caplog.records[0].message
    assert "outcome=ok" in caplog.records[0].message


def test_logs_tool_error_outcome_when_the_result_carries_an_error_key(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.INFO, logger="pmchaser"):
        result = log_tool_call("fake_tool", lambda: {"error": "no such thing"})

    assert result == {"error": "no such thing"}
    assert "outcome=tool_error" in caplog.records[0].message


def test_logs_exception_outcome_and_still_reraises(caplog: pytest.LogCaptureFixture):
    def _boom():
        raise ValueError("simulated crash")

    with caplog.at_level(logging.INFO, logger="pmchaser"):
        with pytest.raises(ValueError, match="simulated crash"):
            log_tool_call("fake_tool", _boom)

    assert "outcome=exception" in caplog.records[0].message
    assert caplog.records[0].levelname == "ERROR"


def test_passes_args_and_kwargs_through_to_the_wrapped_function():
    def _echo(a, b, c=None):
        return (a, b, c)

    result = log_tool_call("fake_tool", _echo, 1, 2, c=3)

    assert result == (1, 2, 3)


def test_a_non_dict_return_value_is_still_outcome_ok():
    result = log_tool_call("fake_tool", lambda: [1, 2, 3])
    assert result == [1, 2, 3]
