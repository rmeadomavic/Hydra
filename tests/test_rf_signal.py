"""Tests for RF signal filtering."""

from __future__ import annotations

from hydra_detect.rf.signal import RSSIFilter


class TestRSSIFilter:
    def test_empty_average(self):
        f = RSSIFilter(window_size=5)
        assert f.average == -100.0

    def test_single_sample(self):
        f = RSSIFilter(window_size=5)
        result = f.add(-60.0)
        assert result == -60.0
        assert f.average == -60.0

    def test_smoothing(self):
        f = RSSIFilter(window_size=3)
        f.add(-60.0)
        f.add(-63.0)
        f.add(-57.0)
        assert abs(f.average - (-60.0)) < 0.01

    def test_window_rolls(self):
        f = RSSIFilter(window_size=3)
        f.add(-80.0)
        f.add(-80.0)
        f.add(-80.0)
        assert f.average == -80.0
        # Push out old values
        f.add(-40.0)
        f.add(-40.0)
        f.add(-40.0)
        assert f.average == -40.0

    def test_trend_positive(self):
        f = RSSIFilter(window_size=6)
        for v in [-80, -75, -70, -65, -60, -55]:
            f.add(v)
        assert f.trend > 0

    def test_trend_negative(self):
        f = RSSIFilter(window_size=6)
        for v in [-55, -60, -65, -70, -75, -80]:
            f.add(v)
        assert f.trend < 0

    def test_trend_flat(self):
        f = RSSIFilter(window_size=4)
        for _ in range(4):
            f.add(-60.0)
        assert abs(f.trend) < 0.01

    def test_trend_insufficient_data(self):
        f = RSSIFilter(window_size=10)
        f.add(-60.0)
        assert f.trend == 0.0

    def test_reset(self):
        f = RSSIFilter(window_size=5)
        f.add(-50.0)
        f.add(-50.0)
        f.reset()
        assert f.average == -100.0
        assert len(f._window) == 0
