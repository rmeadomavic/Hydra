"""Regression tests for rtl_power_client subprocess cleanup.

CLAUDE.md mandates that external-process spawners (rtl_power, Kismet, etc.)
implement cleanup via try/finally so KeyboardInterrupt / SystemExit don't
orphan the subprocess. These tests verify `_scan_peak` calls `_kill_proc`
on every failure path, including BaseException subclasses that
`except Exception:` would miss.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from hydra_detect.rf import rtl_power_client
from hydra_detect.rf.rtl_power_client import RtlPowerClient


def _make_proc(lines: list[str] | None = None, raise_on_iter: BaseException | None = None):
    """Build a Popen-like mock.

    If raise_on_iter is set, stdout iteration raises that exception
    on the first __next__ call.
    """
    proc = MagicMock(spec=subprocess.Popen)
    proc.pid = 99999  # bogus PID; _kill_proc is mocked anyway

    if raise_on_iter is not None:
        def _raise(*_a, **_k):
            raise raise_on_iter
        stdout = MagicMock()
        stdout.__iter__ = _raise
    else:
        stdout = iter(lines or [])
    proc.stdout = stdout
    return proc


def test_scan_peak_kills_proc_on_keyboard_interrupt():
    client = RtlPowerClient()
    proc = _make_proc(raise_on_iter=KeyboardInterrupt())

    with patch.object(rtl_power_client, "_start_rtl_power", return_value=proc), \
            patch.object(rtl_power_client, "_kill_proc") as kill:
        with pytest.raises(KeyboardInterrupt):
            client._scan_peak(433.0)

    kill.assert_called_once_with(proc)


def test_scan_peak_kills_proc_on_system_exit():
    client = RtlPowerClient()
    proc = _make_proc(raise_on_iter=SystemExit(0))

    with patch.object(rtl_power_client, "_start_rtl_power", return_value=proc), \
            patch.object(rtl_power_client, "_kill_proc") as kill:
        with pytest.raises(SystemExit):
            client._scan_peak(433.0)

    kill.assert_called_once_with(proc)


def test_scan_peak_kills_proc_on_generic_exception():
    client = RtlPowerClient()
    proc = _make_proc(raise_on_iter=RuntimeError("synthetic"))

    with patch.object(rtl_power_client, "_start_rtl_power", return_value=proc), \
            patch.object(rtl_power_client, "_kill_proc") as kill:
        with pytest.raises(RuntimeError):
            client._scan_peak(433.0)

    kill.assert_called_once_with(proc)


def test_scan_peak_kills_proc_on_wait_timeout():
    """TimeoutExpired during proc.wait is the one caught case — finally still kills."""
    client = RtlPowerClient()
    proc = _make_proc(lines=[])
    proc.wait.side_effect = subprocess.TimeoutExpired(cmd="rtl_power", timeout=5)

    with patch.object(rtl_power_client, "_start_rtl_power", return_value=proc), \
            patch.object(rtl_power_client, "_kill_proc") as kill:
        result = client._scan_peak(433.0)

    assert result is None  # no lines → no peak
    kill.assert_called_once_with(proc)


def test_scan_peak_does_not_kill_proc_on_clean_exit():
    """If proc.wait() returns cleanly, _kill_proc should NOT fire."""
    client = RtlPowerClient()
    proc = _make_proc(lines=[
        # rtl_power CSV: date,time,start_hz,end_hz,step,sample_count,db1,db2,...
        "2026-04-20, 02:30:00, 430000000, 440000000, 100000, 1, -42.3, -55.1",
    ])

    with patch.object(rtl_power_client, "_start_rtl_power", return_value=proc), \
            patch.object(rtl_power_client, "_kill_proc") as kill:
        result = client._scan_peak(435.0)

    assert result == -42.3  # peak (higher dB value)
    kill.assert_not_called()
    proc.wait.assert_called_once_with(timeout=5)


def test_scan_peak_returns_none_when_rtl_power_missing():
    """FileNotFoundError from _start_rtl_power → graceful None return, no kill."""
    client = RtlPowerClient()

    def _raise_fnf(*_a, **_k):
        raise FileNotFoundError("rtl_power")

    with patch.object(rtl_power_client, "_start_rtl_power", side_effect=_raise_fnf), \
            patch.object(rtl_power_client, "_kill_proc") as kill:
        result = client._scan_peak(433.0)

    assert result is None
    kill.assert_not_called()
