'use strict';

/**
 * Hydra Detect v2.0 — Ops HUD View Logic
 *
 * Minimal heads-up display: full-width video with telemetry overlay,
 * quick-action buttons, lock info, clickable bounding box overlays,
 * context menu for target engagement, and confirmation dialogs.
 */
const HydraOps = (() => {
    let updateTimer = null;
    let streamPolling = false;
    let streamBackoff = 1000;
    let handlersWired = false;

    // Bounding box / context menu state
    let contextMenuTrack = null;   // track object for open context menu
    let confirmAction = null;      // pending confirmation {action, trackId, label}

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
        hideContextMenu();
        hideConfirmOverlay();
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
            drawBoundingBoxes();
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

    // ── Video-to-Canvas Coordinate Mapping ──
    // The img uses object-fit:contain, so the actual video may be
    // letterboxed inside the element. We need to compute the offset
    // and scale from frame coordinates to canvas/display coordinates.
    function getVideoMapping() {
        var img = document.getElementById('ops-video-frame');
        if (!img) return null;

        var displayW = img.clientWidth;
        var displayH = img.clientHeight;
        var frameW = img.naturalWidth || 640;
        var frameH = img.naturalHeight || 480;

        if (frameW === 0 || frameH === 0 || displayW === 0 || displayH === 0) {
            return null;
        }

        // object-fit:contain — compute rendered size and offset
        var displayAspect = displayW / displayH;
        var frameAspect = frameW / frameH;
        var renderW, renderH, offsetX, offsetY;

        if (frameAspect > displayAspect) {
            // Video wider than container — letterbox top/bottom
            renderW = displayW;
            renderH = displayW / frameAspect;
            offsetX = 0;
            offsetY = (displayH - renderH) / 2;
        } else {
            // Video taller than container — pillarbox left/right
            renderH = displayH;
            renderW = displayH * frameAspect;
            offsetX = (displayW - renderW) / 2;
            offsetY = 0;
        }

        return {
            frameW: frameW,
            frameH: frameH,
            renderW: renderW,
            renderH: renderH,
            offsetX: offsetX,
            offsetY: offsetY,
            scaleX: renderW / frameW,
            scaleY: renderH / frameH,
        };
    }

    // ── Bounding Box Drawing ──
    function drawBoundingBoxes() {
        var canvas = document.getElementById('ops-bbox-canvas');
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        if (!ctx) return;

        ctx.clearRect(0, 0, canvas.width, canvas.height);

        var tracks = HydraApp.state.tracks;
        if (!tracks || tracks.length === 0) return;

        var mapping = getVideoMapping();
        if (!mapping) return;

        var target = HydraApp.state.target;
        var lockedId = (target && target.locked) ? target.track_id : null;

        for (var i = 0; i < tracks.length; i++) {
            var t = tracks[i];
            var bbox = t.bbox;
            if (!bbox || bbox.length < 4) continue;

            var x1 = bbox[0] * mapping.scaleX + mapping.offsetX;
            var y1 = bbox[1] * mapping.scaleY + mapping.offsetY;
            var x2 = bbox[2] * mapping.scaleX + mapping.offsetX;
            var y2 = bbox[3] * mapping.scaleY + mapping.offsetY;
            var w = x2 - x1;
            var h = y2 - y1;

            var isLocked = (lockedId !== null && t.track_id === lockedId);

            // Draw rectangle
            ctx.strokeStyle = isLocked ? '#ffffff' : '#6aaa4a';
            ctx.lineWidth = isLocked ? 3 : 2;
            ctx.strokeRect(x1, y1, w, h);

            // Draw label background + text
            var label = '#' + t.track_id + ' ' + (t.label || '?') + ' ' + Math.round((t.confidence || 0) * 100) + '%';
            ctx.font = '11px monospace';
            var textMetrics = ctx.measureText(label);
            var textW = textMetrics.width + 6;
            var textH = 14;
            var labelY = y1 - textH - 1;
            if (labelY < 0) labelY = y1 + 1; // flip below if clipped at top

            ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
            ctx.fillRect(x1, labelY, textW, textH);

            ctx.fillStyle = isLocked ? '#ffffff' : '#6aaa4a';
            ctx.fillText(label, x1 + 3, labelY + 11);

            // Locked target: corner brackets for emphasis
            if (isLocked) {
                var bracketLen = Math.min(12, w * 0.25, h * 0.25);
                ctx.strokeStyle = '#ffffff';
                ctx.lineWidth = 2;
                // Top-left
                ctx.beginPath();
                ctx.moveTo(x1, y1 + bracketLen);
                ctx.lineTo(x1, y1);
                ctx.lineTo(x1 + bracketLen, y1);
                ctx.stroke();
                // Top-right
                ctx.beginPath();
                ctx.moveTo(x2 - bracketLen, y1);
                ctx.lineTo(x2, y1);
                ctx.lineTo(x2, y1 + bracketLen);
                ctx.stroke();
                // Bottom-left
                ctx.beginPath();
                ctx.moveTo(x1, y2 - bracketLen);
                ctx.lineTo(x1, y2);
                ctx.lineTo(x1 + bracketLen, y2);
                ctx.stroke();
                // Bottom-right
                ctx.beginPath();
                ctx.moveTo(x2 - bracketLen, y2);
                ctx.lineTo(x2, y2);
                ctx.lineTo(x2, y2 - bracketLen);
                ctx.stroke();
            }
        }
    }

    // ── Click Hit-Testing ──
    function onCanvasClick(e) {
        var canvas = document.getElementById('ops-bbox-canvas');
        if (!canvas) return;

        var mapping = getVideoMapping();
        if (!mapping) {
            hideContextMenu();
            return;
        }

        var rect = canvas.getBoundingClientRect();
        var clickX = e.clientX - rect.left;
        var clickY = e.clientY - rect.top;

        // Convert display coords to frame coords
        var frameX = (clickX - mapping.offsetX) / mapping.scaleX;
        var frameY = (clickY - mapping.offsetY) / mapping.scaleY;

        var tracks = HydraApp.state.tracks;
        if (!tracks) {
            hideContextMenu();
            return;
        }

        // Check each track bbox for containment (last drawn = on top, check reverse)
        var hitTrack = null;
        for (var i = tracks.length - 1; i >= 0; i--) {
            var t = tracks[i];
            var bbox = t.bbox;
            if (!bbox || bbox.length < 4) continue;
            if (frameX >= bbox[0] && frameX <= bbox[2] &&
                frameY >= bbox[1] && frameY <= bbox[3]) {
                hitTrack = t;
                break;
            }
        }

        if (hitTrack) {
            showContextMenu(hitTrack, clickX, clickY);
        } else {
            hideContextMenu();
        }
    }

    // ── Context Menu ──
    function getOrCreateContextMenu() {
        var menu = document.getElementById('ops-context-menu');
        if (menu) return menu;

        var container = document.getElementById('ops-video-container');
        if (!container) return null;

        menu = document.createElement('div');
        menu.id = 'ops-context-menu';
        menu.className = 'ops-context-menu';
        container.appendChild(menu);
        return menu;
    }

    function showContextMenu(track, clickX, clickY) {
        var menu = getOrCreateContextMenu();
        if (!menu) return;

        contextMenuTrack = track;

        // Clear previous content
        while (menu.firstChild) menu.removeChild(menu.firstChild);

        // Header
        var header = document.createElement('div');
        header.className = 'ops-context-menu-header';
        header.textContent = '#' + track.track_id + ' ' + (track.label || '?') +
            ' (' + Math.round((track.confidence || 0) * 100) + '%)';
        menu.appendChild(header);

        // Action buttons
        var actions = [
            { label: 'Follow', action: 'follow' },
            { label: 'P-Lock', action: 'pixel_lock' },
            { label: 'Lock', action: 'lock' },
            { label: 'Loiter', action: 'loiter' },
            { label: 'Drop', action: 'drop', cls: 'warning' },
            { label: 'Strike', action: 'strike', cls: 'danger' },
        ];

        for (var i = 0; i < actions.length; i++) {
            var a = actions[i];
            var btn = document.createElement('button');
            btn.className = 'ops-context-menu-btn';
            if (a.cls) btn.className += ' ' + a.cls;
            btn.textContent = a.label;
            btn.dataset.action = a.action;
            btn.addEventListener('click', onContextMenuAction);
            menu.appendChild(btn);
        }

        // Position: clamp to stay within container
        var container = document.getElementById('ops-video-container');
        var containerRect = container ? container.getBoundingClientRect() : null;
        menu.classList.add('visible');

        // Measure menu after making visible
        var menuW = menu.offsetWidth;
        var menuH = menu.offsetHeight;
        var maxX = (containerRect ? containerRect.width : 800) - menuW - 4;
        var maxY = (containerRect ? containerRect.height : 600) - menuH - 4;
        var posX = Math.max(4, Math.min(clickX, maxX));
        var posY = Math.max(4, Math.min(clickY, maxY));

        menu.style.left = posX + 'px';
        menu.style.top = posY + 'px';
    }

    function hideContextMenu() {
        var menu = document.getElementById('ops-context-menu');
        if (menu) menu.classList.remove('visible');
        contextMenuTrack = null;
    }

    function onContextMenuAction(e) {
        var action = e.currentTarget.dataset.action;
        if (!contextMenuTrack) return;
        var trackId = contextMenuTrack.track_id;
        var trackLabel = contextMenuTrack.label || '?';

        hideContextMenu();

        if (action === 'strike') {
            showConfirmOverlay('strike', trackId, trackLabel);
            return;
        }
        if (action === 'drop') {
            showConfirmOverlay('drop', trackId, trackLabel);
            return;
        }

        executeAction(action, trackId);
    }

    function executeAction(action, trackId) {
        if (action === 'follow') {
            HydraApp.apiPost('/api/approach/follow/' + trackId, {}).then(function (r) {
                if (r) HydraApp.showToast('Follow engaged on #' + trackId, 'success');
            });
        } else if (action === 'pixel_lock') {
            HydraApp.apiPost('/api/approach/pixel_lock/' + trackId, {}).then(function (r) {
                if (r) HydraApp.showToast('Pixel-lock on #' + trackId, 'success');
            });
        } else if (action === 'lock') {
            HydraApp.apiPost('/api/target/lock', { track_id: trackId }).then(function (r) {
                if (r) HydraApp.showToast('Locked #' + trackId, 'success');
            });
        } else if (action === 'loiter') {
            HydraApp.apiPost('/api/vehicle/mode', { mode: 'LOITER' }).then(function (r) {
                if (r) HydraApp.showToast('Loiter command sent', 'info');
            });
        } else if (action === 'strike') {
            HydraApp.apiPost('/api/approach/strike/' + trackId, {}).then(function (r) {
                if (r) HydraApp.showToast('Strike engaged on #' + trackId, 'success');
            });
        } else if (action === 'drop') {
            HydraApp.apiPost('/api/approach/drop/' + trackId, {}).then(function (r) {
                if (r) HydraApp.showToast('Drop engaged on #' + trackId, 'success');
            });
        }
    }

    // ── Confirmation Overlay ──
    function getOrCreateConfirmOverlay() {
        var overlay = document.getElementById('ops-confirm-overlay');
        if (overlay) return overlay;

        var container = document.getElementById('ops-video-container');
        if (!container) return null;

        overlay = document.createElement('div');
        overlay.id = 'ops-confirm-overlay';
        overlay.className = 'ops-confirm-overlay';
        container.appendChild(overlay);
        return overlay;
    }

    function showConfirmOverlay(action, trackId, trackLabel) {
        var overlay = getOrCreateConfirmOverlay();
        if (!overlay) return;

        confirmAction = { action: action, trackId: trackId, label: trackLabel };

        // Clear previous content
        while (overlay.firstChild) overlay.removeChild(overlay.firstChild);

        var card = document.createElement('div');
        card.className = 'ops-confirm-card';

        var title = document.createElement('div');
        title.className = 'ops-confirm-title';
        if (action === 'strike') {
            title.className += ' danger';
            title.textContent = 'Confirm Strike';
        } else {
            title.className += ' warning';
            title.textContent = 'Confirm Drop';
        }
        card.appendChild(title);

        var desc = document.createElement('div');
        desc.style.cssText = 'font-family: var(--font-mono); font-size: var(--font-sm); color: var(--text-secondary); margin-bottom: var(--gap-md);';
        desc.textContent = 'Target #' + trackId + ' ' + trackLabel;
        card.appendChild(desc);

        var actionsDiv = document.createElement('div');
        actionsDiv.className = 'ops-confirm-actions';

        var cancelBtn = document.createElement('button');
        cancelBtn.className = 'btn';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.addEventListener('click', function () {
            hideConfirmOverlay();
        });
        actionsDiv.appendChild(cancelBtn);

        var confirmBtn = document.createElement('button');
        if (action === 'strike') {
            confirmBtn.className = 'btn btn-danger';
        } else {
            confirmBtn.className = 'btn';
            confirmBtn.style.cssText = 'background: var(--warning); color: #fff;';
        }
        confirmBtn.textContent = action === 'strike' ? 'STRIKE' : 'DROP';
        confirmBtn.addEventListener('click', function () {
            if (confirmAction) {
                executeAction(confirmAction.action, confirmAction.trackId);
            }
            hideConfirmOverlay();
        });
        actionsDiv.appendChild(confirmBtn);

        card.appendChild(actionsDiv);
        overlay.appendChild(card);
        overlay.classList.add('active');
    }

    function hideConfirmOverlay() {
        var overlay = document.getElementById('ops-confirm-overlay');
        if (overlay) overlay.classList.remove('active');
        confirmAction = null;
    }

    // ── HUD Updates ──
    function updateHUD() {
        var stats = HydraApp.state.stats;
        if (!stats) return;
        updateTelemetry(stats);
        updateLockInfo(HydraApp.state.target);
        drawBoundingBoxes();
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

        // Canvas click for bounding box hit-testing
        var canvas = document.getElementById('ops-bbox-canvas');
        if (canvas) {
            canvas.addEventListener('click', onCanvasClick);
        }

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

        // Window resize: keep canvas in sync and redraw
        window.addEventListener('resize', function () {
            resizeCanvas();
            drawBoundingBoxes();
        });

        // Click outside context menu to dismiss
        document.addEventListener('click', function (e) {
            var menu = document.getElementById('ops-context-menu');
            if (!menu) return;
            if (!menu.contains(e.target)) {
                // Let onCanvasClick handle canvas clicks (it calls show/hide)
                var canvas = document.getElementById('ops-bbox-canvas');
                if (canvas && canvas.contains(e.target)) return;
                hideContextMenu();
            }
        });

        // Escape to dismiss context menu and confirm overlay
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') {
                hideContextMenu();
                hideConfirmOverlay();
            }
        });
    }

    return {
        onEnter: onEnter,
        onLeave: onLeave,
        updateTelemetry: updateTelemetry,
        updateLockInfo: updateLockInfo,
        drawBoundingBoxes: drawBoundingBoxes,
    };
})();
