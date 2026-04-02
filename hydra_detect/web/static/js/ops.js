'use strict';

/**
 * Hydra Detect v2.0 — Ops HUD View Logic
 *
 * Minimal heads-up display: full-width video with telemetry overlay,
 * quick-action buttons, and lock info. Canvas element prepared for
 * future clickable bounding box hit-testing.
 */
const HydraOps = (() => {
    let updateTimer = null;
    let streamPolling = false;
    let streamBackoff = 1000;
    let handlersWired = false;

    // ── Lifecycle ──
    function onEnter() {
        wireEventHandlers();
        startVideoPolling();
        updateTimer = setInterval(updateHUD, 500);
        updateHUD();
    }

    function onLeave() {
        if (updateTimer) {
            clearInterval(updateTimer);
            updateTimer = null;
        }
        stopVideoPolling();
    }

    // ── Video Polling (independent of main stream for ops-specific img) ──
    function startVideoPolling() {
        if (streamPolling) return;
        streamPolling = true;
        streamBackoff = 1000;
        var img = document.getElementById('ops-video-frame');
        if (img) img.src = '/stream.jpg?t=' + Date.now();
    }

    function stopVideoPolling() {
        streamPolling = false;
    }

    function pollFrame() {
        if (!streamPolling) return;
        var img = document.getElementById('ops-video-frame');
        if (img) img.src = '/stream.jpg?t=' + Date.now();
    }

    function initVideoListeners() {
        var img = document.getElementById('ops-video-frame');
        if (!img) return;

        img.addEventListener('load', function () {
            var lost = document.getElementById('ops-hud-stream-lost');
            if (lost) lost.style.display = 'none';
            streamBackoff = 1000;
            if (streamPolling) setTimeout(pollFrame, 33);
            // Resize canvas to match video dimensions
            resizeCanvas();
        });

        img.addEventListener('error', function () {
            if (streamPolling) {
                var lost = document.getElementById('ops-hud-stream-lost');
                if (lost) lost.style.display = '';
                setTimeout(pollFrame, streamBackoff);
                streamBackoff = Math.min(streamBackoff * 2, 10000);
            }
        });

        // Double-click for fullscreen
        img.addEventListener('dblclick', function () {
            var container = document.getElementById('ops-video-container');
            if (!container) return;
            if (document.fullscreenElement) {
                document.exitFullscreen();
            } else {
                container.requestFullscreen().catch(function () {});
            }
        });
    }

    // ── Canvas Sizing ──
    function resizeCanvas() {
        var canvas = document.getElementById('ops-bbox-canvas');
        var img = document.getElementById('ops-video-frame');
        if (!canvas || !img) return;
        // Match canvas pixel dimensions to the displayed image size
        var rect = img.getBoundingClientRect();
        canvas.width = rect.width;
        canvas.height = rect.height;
    }

    // ── HUD Updates ──
    function updateHUD() {
        var stats = HydraApp.state.stats;
        if (!stats) return;
        updateTelemetry(stats);
        updateLockInfo(HydraApp.state.target);
    }

    function updateTelemetry(stats) {
        var mode = document.getElementById('ops-telem-mode');
        var battery = document.getElementById('ops-telem-battery');
        var speed = document.getElementById('ops-telem-speed');
        var alt = document.getElementById('ops-telem-alt');
        var heading = document.getElementById('ops-telem-heading');
        var gps = document.getElementById('ops-telem-gps');

        if (mode) mode.textContent = stats.mode || '--';
        if (battery) {
            var batt = stats.battery;
            if (batt !== undefined && batt !== null) {
                battery.textContent = batt.toFixed(0) + '%';
            } else {
                battery.textContent = '--';
            }
        }
        if (speed) {
            var spd = stats.speed;
            if (spd !== undefined && spd !== null) {
                speed.textContent = spd.toFixed(1) + ' m/s';
            } else {
                speed.textContent = '--';
            }
        }
        if (alt) {
            var altitude = stats.altitude;
            if (altitude !== undefined && altitude !== null) {
                alt.textContent = altitude.toFixed(0) + ' m';
            } else {
                alt.textContent = '--';
            }
        }
        if (heading) {
            var hdg = stats.heading;
            if (hdg !== undefined && hdg !== null) {
                heading.textContent = hdg.toFixed(0) + '\u00B0';
            } else {
                heading.textContent = '--';
            }
        }
        if (gps) {
            var fix = stats.gps_fix;
            if (fix !== undefined && fix !== null) {
                gps.textContent = fix >= 3 ? '3D FIX' : fix >= 2 ? '2D' : 'NO FIX';
            } else {
                gps.textContent = '--';
            }
        }
    }

    function updateLockInfo(target) {
        var overlay = document.getElementById('ops-lock-overlay');
        if (!overlay) return;

        if (!target || !target.locked) {
            overlay.style.display = 'none';
            return;
        }

        overlay.style.display = '';
        var isStrike = target.approach_mode === 'strike' || target.approach_mode === 'drop';
        overlay.classList.toggle('strike', isStrike);

        var label = document.getElementById('ops-lock-label');
        var modeEl = document.getElementById('ops-lock-mode');
        var elapsed = document.getElementById('ops-lock-elapsed');

        if (label) label.textContent = target.label || target.track_id || '--';
        if (modeEl) modeEl.textContent = (target.approach_mode || 'TRACK').toUpperCase();
        if (elapsed && target.elapsed) {
            elapsed.textContent = target.elapsed.toFixed(0) + 's';
        } else if (elapsed) {
            elapsed.textContent = '';
        }
    }

    // ── Event Handlers ──
    function wireEventHandlers() {
        if (handlersWired) return;
        handlersWired = true;

        initVideoListeners();

        // Quick action: Abort
        var abortBtn = document.getElementById('ops-btn-abort');
        if (abortBtn) {
            abortBtn.addEventListener('click', function () {
                HydraApp.apiPost('/api/abort', {});
                HydraApp.showToast('Abort command sent', 'info');
            });
        }

        // Quick action: Loiter
        var loiterBtn = document.getElementById('ops-btn-loiter');
        if (loiterBtn) {
            loiterBtn.addEventListener('click', function () {
                HydraApp.apiPost('/api/vehicle/loiter', {});
                HydraApp.showToast('Loiter command sent', 'info');
            });
        }

        // Quick action: RTL
        var rtlBtn = document.getElementById('ops-btn-rtl');
        if (rtlBtn) {
            rtlBtn.addEventListener('click', function () {
                HydraApp.apiPost('/api/vehicle/mode', { mode: 'RTL' });
                HydraApp.showToast('RTL command sent', 'info');
            });
        }

        // Window resize: keep canvas in sync
        window.addEventListener('resize', resizeCanvas);
    }

    return {
        onEnter: onEnter,
        onLeave: onLeave,
        updateTelemetry: updateTelemetry,
        updateLockInfo: updateLockInfo,
    };
})();
