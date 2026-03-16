"""Tests for RF gradient ascent navigator."""

from __future__ import annotations

from hydra_detect.rf.navigator import GradientNavigator


class TestGradientNavigator:
    def test_initial_state(self):
        nav = GradientNavigator()
        assert nav.get_best_rssi() == -100.0
        assert nav.probe_count == 0
        assert nav.bearing == 0.0
        assert nav.get_sample_count() == 0

    def test_record_updates_best(self):
        nav = GradientNavigator()
        nav.record(-70.0, 34.05, -118.25, 15.0)
        assert nav.get_best_rssi() == -70.0
        assert nav.get_best_position() == (34.05, -118.25)
        assert nav.get_sample_count() == 1

    def test_record_keeps_best(self):
        nav = GradientNavigator()
        nav.record(-50.0, 34.05, -118.25, 15.0)
        nav.record(-60.0, 34.06, -118.26, 15.0)
        assert nav.get_best_rssi() == -50.0
        assert nav.get_best_position() == (34.05, -118.25)

    def test_converge_when_strong(self):
        nav = GradientNavigator(converge_dbm=-40.0)
        lat, lon, cont = nav.next_probe(34.05, -118.25, -35.0, -50.0)
        assert cont is False  # converged

    def test_continue_when_improving(self):
        nav = GradientNavigator(improve_threshold_dbm=2.0, converge_dbm=-30.0)
        lat, lon, cont = nav.next_probe(34.05, -118.25, -50.0, -55.0)
        assert cont is True
        assert nav.probe_count == 0  # reset on improvement

    def test_rotate_when_dropping(self):
        nav = GradientNavigator(
            improve_threshold_dbm=2.0, rotation_deg=45.0, converge_dbm=-30.0,
        )
        initial_bearing = nav.bearing
        # Signal dropped significantly
        nav.next_probe(34.05, -118.25, -60.0, -50.0)
        assert nav.bearing == initial_bearing + 45.0
        assert nav.probe_count == 1

    def test_exhaust_probes(self):
        nav = GradientNavigator(
            max_probes=4, rotation_deg=90.0,
            improve_threshold_dbm=2.0, converge_dbm=-30.0,
        )
        nav.record(-50.0, 34.05, -118.25, 15.0)  # set best position

        # Each call rotates but signal keeps dropping
        for i in range(3):
            _, _, cont = nav.next_probe(34.05, -118.25, -70.0, -50.0)
            assert cont is True

        # 4th probe — exhausted
        lat, lon, cont = nav.next_probe(34.05, -118.25, -70.0, -50.0)
        assert cont is False
        # Returns best position
        assert lat == 34.05
        assert lon == -118.25

    def test_reset(self):
        nav = GradientNavigator()
        nav.bearing = 90.0
        nav.probe_count = 5
        nav.reset()
        assert nav.bearing == 0.0
        assert nav.probe_count == 0

    def test_marginal_signal_continues(self):
        nav = GradientNavigator(improve_threshold_dbm=3.0, converge_dbm=-30.0)
        # Marginal: within ±threshold
        _, _, cont = nav.next_probe(34.05, -118.25, -50.0, -51.0)
        assert cont is True
        assert nav.probe_count == 0  # didn't rotate

    def test_next_probe_returns_offset_position(self):
        nav = GradientNavigator(step_m=10.0, converge_dbm=-30.0, improve_threshold_dbm=2.0)
        lat, lon, cont = nav.next_probe(34.05, -118.25, -60.0, -65.0)
        assert cont is True
        # Should have moved from the original position
        assert lat != 34.05 or lon != -118.25

    def test_samples_bounded(self):
        """Samples deque has a maxlen to prevent unbounded memory growth."""
        from hydra_detect.rf.navigator import _MAX_SAMPLES
        nav = GradientNavigator()
        assert nav.samples.maxlen == _MAX_SAMPLES

    def test_thread_safe_accessors(self):
        nav = GradientNavigator()
        nav.record(-55.0, 34.05, -118.25, 15.0)
        assert nav.get_best_rssi() == -55.0
        assert nav.get_best_position() == (34.05, -118.25)
        assert nav.get_sample_count() == 1
        samples = nav.get_samples_copy()
        assert len(samples) == 1
        assert samples[0].rssi_dbm == -55.0
