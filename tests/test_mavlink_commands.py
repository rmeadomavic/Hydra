"""Tests for MAVLink command listener (lock/strike/unlock over SiK radio)."""

from __future__ import annotations

from unittest.mock import MagicMock

from hydra_detect.mavlink_io import MAVLinkIO


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mavlink_io() -> MAVLinkIO:
    """Build a MAVLinkIO with no real connection."""
    mav = MAVLinkIO(connection_string="/dev/null", baud=115200)
    mav._mav = MagicMock()
    mav._mav.mav = MagicMock()
    return mav


def _make_command_long_msg(command: int, param1: float = 0.0) -> MagicMock:
    """Simulate a COMMAND_LONG MAVLink message."""
    msg = MagicMock()
    msg.get_type.return_value = "COMMAND_LONG"
    msg.command = command
    msg.param1 = param1
    msg.param2 = 0.0
    msg.param3 = 0.0
    msg.param4 = 0.0
    msg.param5 = 0.0
    msg.param6 = 0.0
    msg.param7 = 0.0
    return msg


def _make_named_value_int_msg(name: str, value: int) -> MagicMock:
    """Simulate a NAMED_VALUE_INT MAVLink message."""
    msg = MagicMock()
    msg.get_type.return_value = "NAMED_VALUE_INT"
    msg.name = name
    msg.value = value
    return msg


# ---------------------------------------------------------------------------
# COMMAND_LONG tests
# ---------------------------------------------------------------------------

class TestCommandLong:
    def test_lock_command(self):
        mav = _make_mavlink_io()
        lock_cb = MagicMock(return_value=True)
        mav.set_command_callbacks(on_lock=lock_cb)

        msg = _make_command_long_msg(mav.CMD_LOCK, param1=5.0)
        import logging
        audit = logging.getLogger("hydra.audit")
        mav._handle_command_long(msg, audit)

        lock_cb.assert_called_once_with(5)

    def test_strike_command(self):
        mav = _make_mavlink_io()
        strike_cb = MagicMock(return_value=True)
        mav.set_command_callbacks(on_strike=strike_cb)

        msg = _make_command_long_msg(mav.CMD_STRIKE, param1=7.0)
        import logging
        mav._handle_command_long(msg, logging.getLogger("hydra.audit"))

        strike_cb.assert_called_once_with(7)

    def test_unlock_command(self):
        mav = _make_mavlink_io()
        unlock_cb = MagicMock()
        mav.set_command_callbacks(on_unlock=unlock_cb)

        msg = _make_command_long_msg(mav.CMD_UNLOCK)
        import logging
        mav._handle_command_long(msg, logging.getLogger("hydra.audit"))

        unlock_cb.assert_called_once()

    def test_ack_sent_on_success(self):
        mav = _make_mavlink_io()
        mav.set_command_callbacks(on_lock=MagicMock(return_value=True))

        msg = _make_command_long_msg(mav.CMD_LOCK, param1=1.0)
        import logging
        mav._handle_command_long(msg, logging.getLogger("hydra.audit"))

        mav._mav.mav.command_ack_send.assert_called_once_with(
            mav.CMD_LOCK, 0  # MAV_RESULT_ACCEPTED
        )

    def test_ack_sent_on_failure(self):
        mav = _make_mavlink_io()
        mav.set_command_callbacks(on_lock=MagicMock(return_value=False))

        msg = _make_command_long_msg(mav.CMD_LOCK, param1=999.0)
        import logging
        mav._handle_command_long(msg, logging.getLogger("hydra.audit"))

        mav._mav.mav.command_ack_send.assert_called_once_with(
            mav.CMD_LOCK, 4  # MAV_RESULT_FAILED
        )

    def test_ack_unsupported_when_no_callback(self):
        mav = _make_mavlink_io()
        # No callbacks registered

        msg = _make_command_long_msg(mav.CMD_LOCK, param1=1.0)
        import logging
        mav._handle_command_long(msg, logging.getLogger("hydra.audit"))

        mav._mav.mav.command_ack_send.assert_called_once_with(
            mav.CMD_LOCK, 3  # MAV_RESULT_UNSUPPORTED
        )

    def test_unknown_command_ignored(self):
        mav = _make_mavlink_io()
        mav.set_command_callbacks(on_lock=MagicMock())

        msg = _make_command_long_msg(99999)  # Unknown command
        import logging
        mav._handle_command_long(msg, logging.getLogger("hydra.audit"))

        mav._mav.mav.command_ack_send.assert_not_called()


# ---------------------------------------------------------------------------
# NAMED_VALUE_INT tests
# ---------------------------------------------------------------------------

class TestNamedValueInt:
    def test_lock_named_value(self):
        mav = _make_mavlink_io()
        lock_cb = MagicMock(return_value=True)
        mav.set_command_callbacks(on_lock=lock_cb)

        msg = _make_named_value_int_msg(mav.NV_LOCK, 3)
        import logging
        mav._handle_named_value_int(msg, logging.getLogger("hydra.audit"))

        lock_cb.assert_called_once_with(3)

    def test_strike_named_value(self):
        mav = _make_mavlink_io()
        strike_cb = MagicMock(return_value=True)
        mav.set_command_callbacks(on_strike=strike_cb)

        msg = _make_named_value_int_msg(mav.NV_STRIKE, 8)
        import logging
        mav._handle_named_value_int(msg, logging.getLogger("hydra.audit"))

        strike_cb.assert_called_once_with(8)

    def test_unlock_named_value(self):
        mav = _make_mavlink_io()
        unlock_cb = MagicMock()
        mav.set_command_callbacks(on_unlock=unlock_cb)

        msg = _make_named_value_int_msg(mav.NV_UNLOCK, 0)
        import logging
        mav._handle_named_value_int(msg, logging.getLogger("hydra.audit"))

        unlock_cb.assert_called_once()

    def test_null_padded_name(self):
        """NAMED_VALUE_INT names may be null-padded to 10 chars."""
        mav = _make_mavlink_io()
        lock_cb = MagicMock(return_value=True)
        mav.set_command_callbacks(on_lock=lock_cb)

        msg = _make_named_value_int_msg(mav.NV_LOCK + "\x00", 5)
        import logging
        mav._handle_named_value_int(msg, logging.getLogger("hydra.audit"))

        lock_cb.assert_called_once_with(5)

    def test_unknown_name_ignored(self):
        mav = _make_mavlink_io()
        lock_cb = MagicMock()
        mav.set_command_callbacks(on_lock=lock_cb)

        msg = _make_named_value_int_msg("UNKNOWN", 1)
        import logging
        mav._handle_named_value_int(msg, logging.getLogger("hydra.audit"))

        lock_cb.assert_not_called()


# ---------------------------------------------------------------------------
# Callback registration
# ---------------------------------------------------------------------------

class TestCallbackRegistration:
    def test_set_partial_callbacks(self):
        mav = _make_mavlink_io()
        lock_cb = MagicMock(return_value=True)
        mav.set_command_callbacks(on_lock=lock_cb)
        # Only lock registered — strike/unlock should not be present
        assert "lock" in mav._cmd_callbacks
        assert "strike" not in mav._cmd_callbacks
        assert "unlock" not in mav._cmd_callbacks

    def test_set_all_callbacks(self):
        mav = _make_mavlink_io()
        mav.set_command_callbacks(
            on_lock=MagicMock(),
            on_strike=MagicMock(),
            on_unlock=MagicMock(),
        )
        assert "lock" in mav._cmd_callbacks
        assert "strike" in mav._cmd_callbacks
        assert "unlock" in mav._cmd_callbacks


# ---------------------------------------------------------------------------
# Vehicle mode from HEARTBEAT
# ---------------------------------------------------------------------------

class TestVehicleMode:
    def test_initial_mode_is_none(self):
        mav = _make_mavlink_io()
        assert mav.get_vehicle_mode() is None

    def test_mode_updated_from_heartbeat(self):
        mav = _make_mavlink_io()
        mav._mav.mode_mapping.return_value = {"AUTO": 10, "GUIDED": 15, "MANUAL": 0}

        heartbeat = MagicMock()
        heartbeat.custom_mode = 10
        heartbeat.type = 1  # Not GCS

        mav._update_vehicle_mode(heartbeat)
        assert mav.get_vehicle_mode() == "AUTO"

    def test_unknown_mode_number(self):
        mav = _make_mavlink_io()
        mav._mav.mode_mapping.return_value = {"AUTO": 10}

        heartbeat = MagicMock()
        heartbeat.custom_mode = 999

        mav._update_vehicle_mode(heartbeat)
        assert mav.get_vehicle_mode() is None
