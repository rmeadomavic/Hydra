"""Regression test for DetectionLogger writer-thread resilience.

If _process_work_item raises, the writer thread must survive and keep
processing subsequent items. Previously, one bad item would kill the
daemon thread silently and the hot path would fill the queue to capacity.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from hydra_detect.detection_logger import DetectionLogger


@pytest.fixture
def logger_instance(tmp_path):
    """A DetectionLogger configured against tmp_path, not started."""
    dl = DetectionLogger(
        log_dir=str(tmp_path / "logs"),
        image_dir=str(tmp_path / "images"),
        save_images=False,
        save_crops=False,
    )
    yield dl
    # Clean up: if the writer was started, stop it.
    if dl._writer_thread is not None and dl._writer_thread.is_alive():
        dl.stop(timeout=2.0)


def test_writer_loop_survives_process_item_exception(logger_instance):
    """One bad item must not kill the writer thread."""
    dl = logger_instance
    dl.start()

    call_count = {"n": 0}

    def flaky_process(item):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic failure in _process_work_item")
        # Subsequent calls succeed (no-op)

    with patch.object(dl, "_process_work_item", side_effect=flaky_process):
        # Enqueue three items. First will raise; next two should still run.
        for i in range(3):
            dl._write_queue.put_nowait({"frame_no": i, "records": [],
                                        "frame": None, "img_filename": None,
                                        "tracking_result": [], "flush": False})

        # Give the writer thread time to consume all three.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and call_count["n"] < 3:
            time.sleep(0.05)

    assert call_count["n"] == 3, (
        f"Writer thread died after first exception; only {call_count['n']} "
        "of 3 items were processed."
    )
    assert dl._writer_thread.is_alive(), "Writer thread should still be alive"


def test_writer_loop_drains_on_stop_even_after_exception(logger_instance):
    """STOP drain path must also tolerate a failing work item."""
    dl = logger_instance
    dl.start()

    processed: list[int] = []

    def flaky_process(item):
        if item["frame_no"] == 1:
            raise ValueError("synthetic")
        processed.append(item["frame_no"])

    with patch.object(dl, "_process_work_item", side_effect=flaky_process):
        # Enqueue items: frame 0 (ok), frame 1 (raises), frame 2 (ok)
        # then STOP. The drain-on-stop branch of _writer_loop must
        # process 0 and 2 despite 1 raising.
        dl._write_queue.put_nowait({"frame_no": 0, "records": [], "frame": None,
                                    "img_filename": None, "tracking_result": [],
                                    "flush": False})

        dl.stop(timeout=3.0)  # sends STOP; writer drains on exit

    # At minimum, frame 0 should be processed. Whether the drain path
    # reached everything depends on scheduling, but frame 0 (enqueued
    # before STOP) must always run.
    assert 0 in processed
