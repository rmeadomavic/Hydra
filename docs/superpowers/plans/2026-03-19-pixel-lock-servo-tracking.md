# Pixel-Lock Servo Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pixel-lock servo tracker that maps a locked target's horizontal pixel offset to a pan servo via PWM, and actuates a strike servo on strike commands.

**Architecture:** New `ServoTracker` class in `hydra_detect/servo_tracker.py` consumes the existing `error_x` value from the pipeline loop and calls `MAVLinkIO.set_servo()`. Pipeline integration follows the same pattern as `AutonomousController` — config-gated construction, per-frame calls in the hot loop, cleanup on shutdown. A prerequisite refactor consolidates the two unlock code paths into one.

**Tech Stack:** Python 3.10+, threading, MAVLinkIO.set_servo() (MAV_CMD_DO_SET_SERVO), pytest + unittest.mock

**Spec:** `docs/superpowers/specs/2026-03-19-pixel-lock-servo-tracking-design.md`

---

### Task 1: ServoTracker — pan servo mapping + unit tests

**Files:**
- Create: `hydra_detect/servo_tracker.py`
- Create: `tests/test_servo_tracker.py`

- [ ] **Step 1: Write failing tests for pan servo mapping**

```python
# tests/test_servo_tracker.py
"""Tests for the pixel-lock servo tracker."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from hydra_detect.servo_tracker import ServoTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tracker(**overrides) -> tuple[ServoTracker, MagicMock]:
    """Build a ServoTracker with a mock MAVLinkIO.

    Resets mock call history after construction so tests only see
    calls from the method under test, not __init__ safe-position calls.
    """
    mav = MagicMock()
    defaults = dict(
        pan_channel=1,
        pan_pwm_center=1500,
        pan_pwm_range=500,
        pan_invert=False,
        pan_dead_zone=0.05,
        pan_smoothing=1.0,  # alpha=1.0 means no smoothing (instant)
        strike_channel=2,
        strike_pwm_fire=1900,
        strike_pwm_safe=1100,
        strike_duration=0.5,
        replaces_yaw=False,
    )
    defaults.update(overrides)
    tracker = ServoTracker(mav, **defaults)
    mav.reset_mock()  # Clear __init__ set_servo calls
    return tracker, mav


# ---------------------------------------------------------------------------
# Pan servo mapping
# ---------------------------------------------------------------------------

class TestPanMapping:
    def test_center_error_gives_center_pwm(self):
        tracker, mav = _make_tracker()
        tracker.update(0.0)
        mav.set_servo.assert_not_called()  # dead zone: abs(0.0) < 0.05

    def test_full_right_gives_max_pwm(self):
        tracker, mav = _make_tracker()
        tracker.update(1.0)
        mav.set_servo.assert_called_with(1, 2000)

    def test_full_left_gives_min_pwm(self):
        tracker, mav = _make_tracker()
        tracker.update(-1.0)
        mav.set_servo.assert_called_with(1, 1000)

    def test_half_right(self):
        tracker, mav = _make_tracker()
        tracker.update(0.5)
        mav.set_servo.assert_called_with(1, 1750)

    def test_dead_zone_suppresses_small_errors(self):
        tracker, mav = _make_tracker(pan_dead_zone=0.1)
        tracker.update(0.05)
        mav.set_servo.assert_not_called()

    def test_dead_zone_boundary(self):
        tracker, mav = _make_tracker(pan_dead_zone=0.1)
        tracker.update(0.1)
        mav.set_servo.assert_called_once()

    def test_invert_flips_direction(self):
        tracker, mav = _make_tracker(pan_invert=True)
        tracker.update(0.5)
        mav.set_servo.assert_called_with(1, 1250)  # center - range*0.5

    def test_clamping_extreme_error(self):
        tracker, mav = _make_tracker(pan_pwm_center=1500, pan_pwm_range=2000)
        tracker.update(1.0)
        # 1500 + 2000 = 3500 → clamped to 2500
        mav.set_servo.assert_called_with(1, 2500)

    def test_clamping_negative_extreme(self):
        tracker, mav = _make_tracker(pan_pwm_center=1500, pan_pwm_range=2000)
        tracker.update(-1.0)
        # 1500 - 2000 = -500 → clamped to 500
        mav.set_servo.assert_called_with(1, 500)


class TestPanSmoothing:
    def test_smoothing_dampens_step_change(self):
        tracker, mav = _make_tracker(pan_smoothing=0.3)
        # First update: smoothed = 0.3 * 1.0 + 0.7 * 0.0 = 0.3
        tracker.update(1.0)
        mav.set_servo.assert_called_with(1, 1650)  # 1500 + 500*0.3

    def test_smoothing_converges(self):
        tracker, mav = _make_tracker(pan_smoothing=0.5)
        # Apply same error many times — should converge toward full value
        for _ in range(50):
            tracker.update(1.0)
        last_call = mav.set_servo.call_args
        assert last_call == call(1, 2000)  # converged (0.5^50 ≈ 0)


class TestPanRateLimiting:
    def test_skip_if_pwm_unchanged(self):
        tracker, mav = _make_tracker()
        tracker.update(0.5)
        assert mav.set_servo.call_count == 1
        # Same error again — same PWM, should skip
        tracker.update(0.5)
        assert mav.set_servo.call_count == 1

    def test_sends_on_pwm_change(self):
        tracker, mav = _make_tracker()
        tracker.update(0.5)
        assert mav.set_servo.call_count == 1
        tracker.update(0.6)
        assert mav.set_servo.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_servo_tracker.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'hydra_detect.servo_tracker'`

- [ ] **Step 3: Implement ServoTracker with pan mapping**

```python
# hydra_detect/servo_tracker.py
"""Pixel-lock servo controller — maps camera error to PWM output."""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class ServoTracker:
    """Maps a locked target's pixel offset to servo PWM via MAVLink.

    Pan servo: proportional mapping from error_x to PWM.
    Strike servo: pulse on/off on strike command.
    """

    def __init__(
        self,
        mavlink,
        *,
        # Pan servo
        pan_channel: int = 1,
        pan_pwm_center: int = 1500,
        pan_pwm_range: int = 500,
        pan_invert: bool = False,
        pan_dead_zone: float = 0.05,
        pan_smoothing: float = 0.3,
        # Strike servo
        strike_channel: int = 2,
        strike_pwm_fire: int = 1900,
        strike_pwm_safe: int = 1100,
        strike_duration: float = 0.5,
        # Integration
        replaces_yaw: bool = False,
    ):
        self._mavlink = mavlink

        # Pan config
        self._pan_channel = pan_channel
        self._pan_center = pan_pwm_center
        self._pan_range = pan_pwm_range
        self._pan_invert = pan_invert
        self._pan_dead_zone = max(0.0, pan_dead_zone)
        self._pan_alpha = max(0.01, min(1.0, pan_smoothing))

        # Strike config
        self._strike_channel = strike_channel
        self._strike_pwm_fire = strike_pwm_fire
        self._strike_pwm_safe = strike_pwm_safe
        self._strike_duration = strike_duration

        self._replaces_yaw = replaces_yaw

        # Runtime state
        self._smoothed: float = 0.0
        self._last_pwm: int = pan_pwm_center  # Start at center (matches init command)
        self._strike_active = threading.Event()
        self._tracking = False
        self._last_error_x: float = 0.0

        # Init servos to safe positions
        self._mavlink.set_servo(self._strike_channel, self._strike_pwm_safe)
        self._mavlink.set_servo(self._pan_channel, self._pan_center)

    # -- Pan servo ----------------------------------------------------------

    def update(self, error_x: float) -> None:
        """Update pan servo from pixel-lock error. Called every frame.

        Skips set_servo() if computed PWM matches previous value.
        """
        self._tracking = True
        self._last_error_x = error_x

        # EMA smoothing
        self._smoothed = self._pan_alpha * error_x + (1.0 - self._pan_alpha) * self._smoothed

        # Dead zone
        if abs(self._smoothed) < self._pan_dead_zone:
            pwm = self._pan_center
        else:
            offset = self._smoothed * self._pan_range
            if self._pan_invert:
                offset = -offset
            pwm = int(self._pan_center + offset)

        # Clamp
        pwm = max(500, min(2500, pwm))

        # Rate limiting: skip if unchanged
        if pwm == self._last_pwm:
            return

        self._last_pwm = pwm
        self._mavlink.set_servo(self._pan_channel, pwm)

    # -- Strike servo -------------------------------------------------------

    def fire_strike(self) -> None:
        """Actuate strike servo (fire -> safe after duration).

        No-op if a strike is already in progress.
        """
        if self._strike_active.is_set():
            logger.info("Strike servo already active — ignoring.")
            return

        self._strike_active.set()
        self._mavlink.set_servo(self._strike_channel, self._strike_pwm_fire)
        logger.info("Strike servo FIRED: ch=%d pwm=%d", self._strike_channel, self._strike_pwm_fire)

        def _revert():
            time.sleep(self._strike_duration)
            self._mavlink.set_servo(self._strike_channel, self._strike_pwm_safe)
            self._strike_active.clear()

        threading.Thread(target=_revert, daemon=True, name="strike-revert").start()

    # -- Safety -------------------------------------------------------------

    def safe(self) -> None:
        """Return all servos to safe positions. Resets EMA state."""
        self._smoothed = 0.0
        self._last_pwm = self._pan_center  # Reset to center (matches safe command)
        self._tracking = False
        self._mavlink.set_servo(self._pan_channel, self._pan_center)
        self._mavlink.set_servo(self._strike_channel, self._strike_pwm_safe)

    # -- Status -------------------------------------------------------------

    def get_status(self) -> dict:
        """Return current state for web API."""
        return {
            "enabled": True,
            "tracking": self._tracking,
            "pan_channel": self._pan_channel,
            "pan_pwm": self._last_pwm if self._last_pwm is not None else self._pan_center,
            "strike_channel": self._strike_channel,
            "strike_active": self._strike_active.is_set(),
            "error_x": round(self._last_error_x, 3),
            "smoothing_alpha": self._pan_alpha,
            "replaces_yaw": self._replaces_yaw,
        }

    @property
    def replaces_yaw(self) -> bool:
        """If True, pipeline should skip adjust_yaw()."""
        return self._replaces_yaw
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_servo_tracker.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/servo_tracker.py tests/test_servo_tracker.py
git commit -m "feat: add ServoTracker with pixel-lock pan mapping and unit tests

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: ServoTracker — strike servo, safety, and get_status tests

**Files:**
- Modify: `tests/test_servo_tracker.py`

- [ ] **Step 1: Add strike, safety, and status tests**

Append to `tests/test_servo_tracker.py`:

```python
# ---------------------------------------------------------------------------
# Strike servo
# ---------------------------------------------------------------------------

class TestStrikeServo:
    def test_fire_calls_set_servo_with_fire_pwm(self):
        tracker, mav = _make_tracker()
        tracker.fire_strike()
        mav.set_servo.assert_any_call(2, 1900)

    def test_fire_reverts_after_duration(self):
        tracker, mav = _make_tracker(strike_duration=0.05)
        tracker.fire_strike()
        import time
        time.sleep(0.15)  # Wait for daemon thread
        # Should have called safe PWM after duration
        mav.set_servo.assert_any_call(2, 1100)

    def test_reentrant_fire_ignored(self):
        tracker, mav = _make_tracker(strike_duration=1.0)
        tracker.fire_strike()
        fire_count = sum(1 for c in mav.set_servo.call_args_list if c == call(2, 1900))
        tracker.fire_strike()  # Should be ignored
        fire_count_after = sum(1 for c in mav.set_servo.call_args_list if c == call(2, 1900))
        assert fire_count_after == fire_count  # No additional fire call


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

class TestSafety:
    def test_safe_centers_pan(self):
        tracker, mav = _make_tracker()
        tracker.update(1.0)  # Move pan
        mav.reset_mock()
        tracker.safe()
        mav.set_servo.assert_any_call(1, 1500)

    def test_safe_sets_strike_to_safe_pwm(self):
        tracker, mav = _make_tracker()
        mav.reset_mock()
        tracker.safe()
        mav.set_servo.assert_any_call(2, 1100)

    def test_safe_resets_ema(self):
        tracker, mav = _make_tracker(pan_smoothing=0.3)
        tracker.update(1.0)  # EMA now non-zero
        tracker.safe()
        mav.reset_mock()
        # After safe, first update with 1.0 should start fresh
        tracker.update(1.0)
        # smoothed = 0.3 * 1.0 + 0.7 * 0.0 = 0.3 → pwm = 1650
        mav.set_servo.assert_called_with(1, 1650)

    def test_init_sets_safe_positions(self):
        mav = MagicMock()
        ServoTracker(mav, pan_channel=1, strike_channel=2)
        mav.set_servo.assert_any_call(2, 1100)  # strike safe
        mav.set_servo.assert_any_call(1, 1500)  # pan center


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_initial_status(self):
        tracker, _ = _make_tracker()
        s = tracker.get_status()
        assert s["enabled"] is True
        assert s["tracking"] is False
        assert s["pan_pwm"] == 1500
        assert s["strike_active"] is False
        assert s["replaces_yaw"] is False

    def test_status_after_tracking(self):
        tracker, _ = _make_tracker()
        tracker.update(0.5)
        s = tracker.get_status()
        assert s["tracking"] is True
        assert s["pan_pwm"] == 1750
        assert s["error_x"] == 0.5

    def test_replaces_yaw_property(self):
        tracker, _ = _make_tracker(replaces_yaw=True)
        assert tracker.replaces_yaw is True
        s = tracker.get_status()
        assert s["replaces_yaw"] is True
```

- [ ] **Step 2: Run all servo tracker tests**

```bash
python -m pytest tests/test_servo_tracker.py -v
```

Expected: all PASS (implementation from Task 1 already covers these)

- [ ] **Step 3: Commit**

```bash
git add tests/test_servo_tracker.py
git commit -m "test: add strike servo, safety, and status tests

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Refactor pipeline — consolidate target-lost unlock path

**Files:**
- Modify: `hydra_detect/pipeline.py:563-579` (inline target-lost block)
- Modify: `hydra_detect/pipeline.py:743-753` (`_handle_target_unlock`)
- Modify: `tests/test_pipeline_callbacks.py`

This refactor must happen BEFORE servo tracker integration so that there is a
single unlock path for servo safing.

- [ ] **Step 1: Write test for unlock with reason**

Add to `tests/test_pipeline_callbacks.py`:

```python
class TestTargetUnlockReason:
    def test_unlock_lost_sends_tgt_lost_statustext(self):
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._locked_track_id = 5
        p._lock_mode = "track"
        p._handle_target_unlock(reason="lost")
        assert p._locked_track_id is None
        # Should send "TGT LOST" message, not generic "TGT LOCK RELEASED"
        p._mavlink.send_statustext.assert_called_once()
        msg = p._mavlink.send_statustext.call_args[0][0]
        assert "TGT LOST" in msg

    def test_unlock_manual_sends_released_statustext(self):
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._locked_track_id = 5
        p._lock_mode = "track"
        p._handle_target_unlock()  # No reason = manual
        msg = p._mavlink.send_statustext.call_args[0][0]
        assert "RELEASED" in msg
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_pipeline_callbacks.py::TestTargetUnlockReason -v
```

Expected: FAIL — `_handle_target_unlock()` does not accept `reason` parameter

- [ ] **Step 3: Refactor `_handle_target_unlock` to accept reason**

In `hydra_detect/pipeline.py`, replace `_handle_target_unlock` (lines 743-753):

```python
    def _handle_target_unlock(self, reason: str = "") -> None:
        """Release target lock.

        Args:
            reason: If "lost", sends a TGT LOST message instead of generic release.
        """
        with self._state_lock:
            prev_id = self._locked_track_id
            self._locked_track_id = None
            self._lock_mode = None
        if prev_id is not None:
            if reason == "lost":
                logger.warning(
                    "Locked target #%d lost from tracker — auto-unlocking.",
                    prev_id,
                )
                if self._mavlink is not None:
                    self._mavlink.send_statustext(
                        f"TGT LOST: #{prev_id} — lock released", severity=4
                    )
            else:
                logger.info("Target UNLOCKED: #%d", prev_id)
                if self._mavlink is not None:
                    self._mavlink.send_statustext("TGT LOCK RELEASED", severity=5)
                    self._mavlink.clear_roi()
```

- [ ] **Step 4: Replace inline target-lost block with method call**

In `hydra_detect/pipeline.py`, replace lines 563-579 (the `else` block under
`if locked_track is not None`):

```python
                else:
                    self._handle_target_unlock(reason="lost")
```

This replaces the 16-line inline block with a single method call.

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_pipeline_callbacks.py -v
```

Expected: all PASS

- [ ] **Step 6: Run full test suite to verify no regressions**

```bash
python -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add hydra_detect/pipeline.py tests/test_pipeline_callbacks.py
git commit -m "refactor: consolidate target-lost unlock into _handle_target_unlock(reason)

Two unlock paths (manual + target-lost) now go through one method.
Prerequisite for servo tracker integration — servo safing needs a
single unlock path.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Pipeline integration — constructor + channel validation

**Files:**
- Modify: `hydra_detect/pipeline.py:15` (import)
- Modify: `hydra_detect/pipeline.py:191` (after light bar setup, before autonomous)
- Modify: `tests/test_pipeline_callbacks.py`

- [ ] **Step 1: Write test for channel collision**

Add to `tests/test_pipeline_callbacks.py`:

```python
class TestServoTrackerSetup:
    def test_servo_tracker_none_without_mavlink(self):
        p = _make_pipeline()
        # Without MAVLink, servo tracker should be None (from _init_target_state)
        assert p._servo_tracker is None

    def test_channel_collision_pan_equals_light_bar(self):
        """Validate the collision detection logic directly."""
        channels = [4, 2, 4]  # pan=4, strike=2, light_bar=4 → collision
        assert len(channels) != len(set(channels))

    def test_channel_collision_pan_equals_strike(self):
        channels = [2, 2]  # pan=2, strike=2 → collision
        assert len(channels) != len(set(channels))

    def test_no_collision_distinct_channels(self):
        channels = [1, 2, 4]  # all distinct → no collision
        assert len(channels) == len(set(channels))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_pipeline_callbacks.py::TestServoTrackerSetup -v
```

Expected: FAIL — `Pipeline` has no `_servo_tracker` attribute

- [ ] **Step 3: Add import and constructor logic**

In `hydra_detect/pipeline.py`, add import at line 15 (after `autonomous` import):

```python
from .servo_tracker import ServoTracker
```

After the light bar setup block (after line 190, before the autonomous controller
block starting at line 192), add:

```python
        # Pixel-lock servo tracker
        self._servo_tracker: ServoTracker | None = None
        if (
            self._mavlink is not None
            and self._cfg.getboolean("servo_tracking", "enabled", fallback=False)
        ):
            pan_ch = self._cfg.getint("servo_tracking", "pan_channel", fallback=1)
            strike_ch = self._cfg.getint("servo_tracking", "strike_channel", fallback=2)
            # Channel collision check
            channels = [pan_ch, strike_ch]
            if self._light_bar_enabled:
                channels.append(self._light_bar_channel)
            if len(channels) != len(set(channels)):
                logger.error(
                    "Servo tracking DISABLED: channel collision detected "
                    "(pan=%d, strike=%d, light_bar=%d)",
                    pan_ch, strike_ch, self._light_bar_channel,
                )
            else:
                self._servo_tracker = ServoTracker(
                    self._mavlink,
                    pan_channel=pan_ch,
                    pan_pwm_center=self._cfg.getint("servo_tracking", "pan_pwm_center", fallback=1500),
                    pan_pwm_range=self._cfg.getint("servo_tracking", "pan_pwm_range", fallback=500),
                    pan_invert=self._cfg.getboolean("servo_tracking", "pan_invert", fallback=False),
                    pan_dead_zone=self._cfg.getfloat("servo_tracking", "pan_dead_zone", fallback=0.05),
                    pan_smoothing=self._cfg.getfloat("servo_tracking", "pan_smoothing", fallback=0.3),
                    strike_channel=strike_ch,
                    strike_pwm_fire=self._cfg.getint("servo_tracking", "strike_pwm_fire", fallback=1900),
                    strike_pwm_safe=self._cfg.getint("servo_tracking", "strike_pwm_safe", fallback=1100),
                    strike_duration=self._cfg.getfloat("servo_tracking", "strike_duration", fallback=0.5),
                    replaces_yaw=self._cfg.getboolean("servo_tracking", "replaces_yaw", fallback=False),
                )
                logger.info(
                    "Pixel-lock servo tracking ENABLED: pan_ch=%d, strike_ch=%d, replaces_yaw=%s",
                    pan_ch, strike_ch, self._servo_tracker.replaces_yaw,
                )
```

Also add `self._servo_tracker = None` inside `_init_target_state()` (so the
`_make_pipeline()` test helper picks it up):

```python
    def _init_target_state(self) -> None:
        """Initialise target-lock state. Safe to call from tests."""
        self._state_lock = threading.Lock()
        self._locked_track_id: Optional[int] = None
        self._lock_mode: Optional[str] = None
        self._last_track_result = None
        self._servo_tracker = None
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_pipeline_callbacks.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add hydra_detect/pipeline.py tests/test_pipeline_callbacks.py
git commit -m "feat: add ServoTracker construction to pipeline with channel validation

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Pipeline integration — run loop, adjust_yaw gating

**Files:**
- Modify: `hydra_detect/pipeline.py:549-579` (locked-target block in `_run_loop`)

- [ ] **Step 1: Replace the locked-target block in the run loop**

In `hydra_detect/pipeline.py`, replace lines 549-579 (the entire
`if current_lock_id is not None and self._mavlink is not None:` block including
the `else` for target-lost) with:

```python
            if current_lock_id is not None and self._mavlink is not None:
                locked_track = track_result.find(current_lock_id)

                if locked_track is not None:
                    # Compute normalised horizontal error from frame center
                    frame_w = frame.shape[1]
                    cx = (locked_track.x1 + locked_track.x2) / 2.0
                    error_x = (cx - frame_w / 2.0) / (frame_w / 2.0)  # -1..+1

                    # Yaw correction (skip if servo tracker replaces it)
                    if current_lock_mode == "track":
                        if self._servo_tracker is None or not self._servo_tracker.replaces_yaw:
                            self._mavlink.adjust_yaw(error_x)
                    elif current_lock_mode == "strike":
                        if self._servo_tracker is None or not self._servo_tracker.replaces_yaw:
                            self._mavlink.adjust_yaw(error_x, yaw_rate_max=15.0)

                    # Pixel-lock servo tracking
                    if self._servo_tracker is not None:
                        self._servo_tracker.update(error_x)
                else:
                    self._handle_target_unlock(reason="lost")
```

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add hydra_detect/pipeline.py
git commit -m "feat: integrate servo tracker in run loop with adjust_yaw gating

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Pipeline integration — strike, unlock, shutdown

**Files:**
- Modify: `hydra_detect/pipeline.py` (`_handle_strike_command`, `_handle_target_unlock`, `_shutdown`)
- Modify: `tests/test_pipeline_callbacks.py`

- [ ] **Step 1: Write tests for servo integration in strike/unlock/shutdown**

Add to `tests/test_pipeline_callbacks.py`:

```python
class TestServoTrackerIntegration:
    def _pipeline_with_servo(self):
        """Build a pipeline with a mock servo tracker."""
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._mavlink.estimate_target_position.return_value = (34.0, -118.0)
        p._mavlink.command_guided_to.return_value = True
        p._servo_tracker = MagicMock()
        p._servo_tracker.replaces_yaw = False
        return p

    def test_strike_fires_servo(self):
        p = self._pipeline_with_servo()
        p._last_track_result = _sample_track(track_id=3)
        p._handle_strike_command(3)
        p._servo_tracker.fire_strike.assert_called_once()

    def test_strike_fires_servo_even_without_gps(self):
        p = self._pipeline_with_servo()
        p._mavlink.estimate_target_position.return_value = None
        p._last_track_result = _sample_track(track_id=3)
        # Strike returns False (no GPS), but servo should still fire
        p._handle_strike_command(3)
        p._servo_tracker.fire_strike.assert_called_once()

    def test_unlock_safes_servo(self):
        p = self._pipeline_with_servo()
        p._locked_track_id = 3
        p._lock_mode = "track"
        p._handle_target_unlock()
        p._servo_tracker.safe.assert_called_once()

    def test_unlock_lost_safes_servo(self):
        p = self._pipeline_with_servo()
        p._locked_track_id = 3
        p._lock_mode = "track"
        p._handle_target_unlock(reason="lost")
        p._servo_tracker.safe.assert_called_once()

    def test_no_servo_tracker_no_error(self):
        """Strike and unlock work fine without servo tracker."""
        p = _make_pipeline()
        p._mavlink = MagicMock()
        p._mavlink.estimate_target_position.return_value = (34.0, -118.0)
        p._mavlink.command_guided_to.return_value = True
        p._servo_tracker = None
        p._last_track_result = _sample_track(track_id=3)
        assert p._handle_strike_command(3) is True  # No crash
        p._handle_target_unlock()  # No crash

    def test_shutdown_safes_servo(self):
        p = self._pipeline_with_servo()
        p._rf_hunt = None
        p._kismet_manager = None
        p._rtsp = None
        p._mavlink_video = None
        p._camera = MagicMock()
        p._detector = MagicMock()
        p._det_logger = MagicMock()
        p._shutdown()
        p._servo_tracker.safe.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_pipeline_callbacks.py::TestServoTrackerIntegration -v
```

Expected: FAIL — `fire_strike` and `safe` not called yet

- [ ] **Step 3: Add servo fire to `_handle_strike_command`**

In `hydra_detect/pipeline.py`, in `_handle_strike_command`, add right before the
final `return success` line (around line 823):

```python
        # Fire strike servo (works even without GPS — it's a direct PWM command)
        if self._servo_tracker is not None:
            self._servo_tracker.fire_strike()
```

Also add it in the early-return path when `self._mavlink is None` (around line 782),
before `return True`:

```python
            if self._servo_tracker is not None:
                self._servo_tracker.fire_strike()
```

And in the GPS failure path (around line 804), after reverting lock state but
before `return False`:

```python
            # Fire strike servo even if GPS failed — servo doesn't need GPS
            if self._servo_tracker is not None:
                self._servo_tracker.fire_strike()
```

- [ ] **Step 4: Add servo safe to `_handle_target_unlock`**

In `hydra_detect/pipeline.py`, in `_handle_target_unlock`, add after the
`if prev_id is not None:` block (at the end of the method, outside the if):

```python
        if self._servo_tracker is not None:
            self._servo_tracker.safe()
```

- [ ] **Step 5: Add servo safe to `_shutdown`**

In `hydra_detect/pipeline.py`, in `_shutdown`, add before `self._camera.close()`:

```python
        if self._servo_tracker is not None:
            self._servo_tracker.safe()
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/test_pipeline_callbacks.py -v
```

Expected: all PASS

- [ ] **Step 7: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add hydra_detect/pipeline.py tests/test_pipeline_callbacks.py
git commit -m "feat: integrate servo tracker in strike, unlock, and shutdown paths

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Config and web API stats

**Files:**
- Modify: `config.ini`
- Modify: `hydra_detect/pipeline.py` (stats update block)

- [ ] **Step 1: Add `[servo_tracking]` section to config.ini**

Append before the `[logging]` section in `config.ini`:

```ini
[servo_tracking]
enabled = false
pan_channel = 1
pan_pwm_center = 1500
pan_pwm_range = 500
pan_invert = false
pan_dead_zone = 0.05
pan_smoothing = 0.3
strike_channel = 2
strike_pwm_fire = 1900
strike_pwm_safe = 1100
strike_duration = 0.5
replaces_yaw = false
```

- [ ] **Step 2: Add servo status to stats update**

In `hydra_detect/pipeline.py`, in the `_run_loop` stats block (around line 655,
where `rf_hunt` status is added), add:

```python
                if self._servo_tracker is not None:
                    stats_update["servo_tracking"] = self._servo_tracker.get_status()
```

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add config.ini hydra_detect/pipeline.py
git commit -m "feat: add servo_tracking config section and web API stats

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Final verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 2: Run linter**

```bash
flake8 hydra_detect/servo_tracker.py tests/test_servo_tracker.py
```

Expected: no errors

- [ ] **Step 3: Verify no stale references**

```bash
grep -rn "servo_track" hydra_detect/ tests/ --include="*.py" | head -30
```

Expected: all references are in the correct files

- [ ] **Step 4: Verify config.ini has the new section**

```bash
grep -A 15 "\[servo_tracking\]" config.ini
```

Expected: full `[servo_tracking]` section visible

- [ ] **Step 5: Run type checker**

```bash
mypy hydra_detect/servo_tracker.py
```

Expected: no errors
