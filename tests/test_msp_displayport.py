"""Tests for MSP v1 DisplayPort protocol (HDZero OSD driver).

Covers the pure frame-builders against known byte sequences.  We do NOT
exercise the serial thread — that requires real hardware and adds flakiness.
"""

from __future__ import annotations

from hydra_detect.msp_displayport import (
    DEFAULT_CANVAS_COLS,
    DEFAULT_CANVAS_ROWS,
    MspOsdData,
    _msp_frame,
    clear_frame,
    draw_frame,
    heartbeat_frame,
    write_string_frame,
)


# ---------------------------------------------------------------------------
# Low-level frame builder
# ---------------------------------------------------------------------------

class TestMspFrame:
    def test_frame_structure(self):
        """Frame = $M< + size + cmd + payload + xor_checksum."""
        frame = _msp_frame(182, b"\x01\x02\x03")
        assert frame[:3] == b"$M<"
        assert frame[3] == 3       # size
        assert frame[4] == 182     # command
        assert frame[5:8] == b"\x01\x02\x03"
        expected_xor = 3 ^ 182 ^ 1 ^ 2 ^ 3
        assert frame[8] == expected_xor

    def test_empty_payload(self):
        frame = _msp_frame(100, b"")
        assert len(frame) == 6  # 3 header + size + cmd + cksum
        # Checksum = 0 ^ 100 = 100
        assert frame[5] == 100

    def test_checksum_masks_to_byte(self):
        """Checksum is &= 0xFF — large XOR must fit in one byte."""
        frame = _msp_frame(255, bytes([0xFF] * 10))
        assert 0 <= frame[-1] <= 255


# ---------------------------------------------------------------------------
# High-level frame builders
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def test_heartbeat_default_canvas(self):
        f = heartbeat_frame()
        # Payload: [sub=0, rows, cols, 0, 0]
        assert f[5] == 0  # DP_SUB_HEARTBEAT
        assert f[6] == DEFAULT_CANVAS_ROWS
        assert f[7] == DEFAULT_CANVAS_COLS

    def test_heartbeat_custom_canvas(self):
        f = heartbeat_frame(rows=16, cols=30)
        assert f[6] == 16
        assert f[7] == 30


class TestClear:
    def test_clear_frame_is_single_sub_command(self):
        f = clear_frame()
        # Header(3) + size(1=1) + cmd(182) + sub(2) + cksum
        assert f[:3] == b"$M<"
        assert f[3] == 1
        assert f[4] == 182
        assert f[5] == 2  # DP_SUB_CLEAR


class TestWriteString:
    def test_write_ascii_text(self):
        f = write_string_frame(5, 10, "ABC")
        # payload = [sub=3, row=5, col=10, attr=0, 'A', 'B', 'C']
        assert f[5] == 3
        assert f[6] == 5
        assert f[7] == 10
        assert f[8] == 0  # attr
        assert f[9:12] == b"ABC"

    def test_write_non_ascii_replaced(self):
        f = write_string_frame(0, 0, "héllo")  # é will become '?'
        # Should not raise, and length is preserved
        size = f[3]
        # payload = [sub, row, col, attr] + "h?llo" = 4 + 5 = 9
        assert size == 9

    def test_attr_byte(self):
        f = write_string_frame(0, 0, "X", attr=7)
        assert f[8] == 7


class TestDraw:
    def test_draw_frame_structure(self):
        f = draw_frame()
        assert f[:3] == b"$M<"
        assert f[3] == 1  # size
        assert f[5] == 4  # DP_SUB_DRAW


# ---------------------------------------------------------------------------
# MspOsdData dataclass
# ---------------------------------------------------------------------------

class TestMspOsdData:
    def test_defaults(self):
        d = MspOsdData()
        assert d.fps == 0.0
        assert d.active_tracks == 0
        assert d.locked_track_id is None
        assert d.lock_mode is None

    def test_assignable(self):
        d = MspOsdData(
            fps=15.5, inference_ms=20.0, active_tracks=3,
            locked_track_id=42, lock_mode="strike",
            latest_det_label="person", latest_det_conf=0.87,
        )
        assert d.lock_mode == "strike"
        assert d.latest_det_conf == 0.87
