"""Tests for MAVLink alert class filter logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hydra_detect.mavlink_io import MAVLinkIO


def _make_mavlink(**kwargs) -> MAVLinkIO:
    """Create a MAVLinkIO instance without a real connection."""
    defaults = dict(connection_string="tcp:127.0.0.1:5760", baud=115200)
    defaults.update(kwargs)
    return MAVLinkIO(**defaults)


# ---------------------------------------------------------------------------
# alert_classes property
# ---------------------------------------------------------------------------

class TestAlertClassesProperty:
    def test_default_none_allows_all(self):
        m = _make_mavlink()
        assert m.alert_classes is None

    def test_init_with_classes(self):
        m = _make_mavlink(alert_classes={"person", "car"})
        assert m.alert_classes == {"person", "car"}

    def test_setter(self):
        m = _make_mavlink()
        m.alert_classes = {"truck", "bicycle"}
        assert m.alert_classes == {"truck", "bicycle"}

    def test_setter_none(self):
        m = _make_mavlink(alert_classes={"person"})
        m.alert_classes = None
        assert m.alert_classes is None


# ---------------------------------------------------------------------------
# alert_detection filtering behaviour
# ---------------------------------------------------------------------------

class TestAlertDetectionFilter:
    def test_alert_skipped_when_not_in_filter(self):
        """alert_detection skips labels absent from the allowlist."""
        m = _make_mavlink(alert_classes={"person"})
        m._mav = MagicMock()
        m.alert_detection("car")
        m._mav.mav.send.assert_not_called()

    def test_alert_sent_when_in_filter(self):
        """alert_detection sends STATUSTEXT for labels in the allowlist."""
        m = _make_mavlink(alert_classes={"person"})
        m._mav = MagicMock()
        m._gps = {"fix": 3, "lat": 340000000, "lon": -1180000000, "alt": 100000, "hdg": 9000}
        with patch("hydra_detect.mavlink_io.MAVLink_statustext_message", create=True):
            m.alert_detection("person")
        m._mav.mav.send.assert_called_once()

    def test_alert_sent_when_filter_is_none(self):
        """alert_detection sends STATUSTEXT for any label when filter is None."""
        m = _make_mavlink(alert_classes=None)
        m._mav = MagicMock()
        m._gps = {"fix": 3, "lat": 340000000, "lon": -1180000000, "alt": 100000, "hdg": 9000}
        with patch("hydra_detect.mavlink_io.MAVLink_statustext_message", create=True):
            m.alert_detection("toothbrush")
        m._mav.mav.send.assert_called_once()
