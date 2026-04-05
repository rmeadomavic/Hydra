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
        if (img) img.src = '/stream.jpg?raw=1&t=' + Date.now();
    }

    function stopVideoPolling() {
        streamPolling = false;
    }

    function pollFrame() {
        if (!streamPolling) return;
        var img = document.getElementById('ops-video-frame');
        if (img) img.src = '/stream.jpg?raw=1&t=' + Date.now();
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
        var rect = img.getBoundingClientRect();
        var dpr = window.devicePixelRatio || 1;
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        canvas.style.width = rect.width + 'px';
        canvas.style.height = rect.height + 'px';
        var ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);
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

    // ── Radial Context Menu ──
    function getOrCreateRadialMenu() {
        var menu = document.getElementById('ops-radial-menu');
        if (menu) return menu;

        var container = document.getElementById('ops-video-container');
        if (!container) return null;

        menu = document.createElement('div');
        menu.id = 'ops-radial-menu';
        menu.className = 'ops-radial-menu';
        container.appendChild(menu);
        return menu;
    }

    function showContextMenu(track, clickX, clickY) {
        var menu = getOrCreateRadialMenu();
        if (!menu) return;

        contextMenuTrack = track;

        // Clear previous content
        while (menu.firstChild) menu.removeChild(menu.firstChild);

        // Clamp center so the full wheel (center + radius + item size) stays visible
        var container = document.getElementById('ops-video-container');
        var containerRect = container ? container.getBoundingClientRect() : null;
        var radius = 80;
        var itemHalf = 25; // half of 50px item
        var margin = radius + itemHalf + 4;
        var cW = containerRect ? containerRect.width : 800;
        var cH = containerRect ? containerRect.height : 600;
        var centerX = Math.max(margin, Math.min(clickX, cW - margin));
        var centerY = Math.max(margin, Math.min(clickY, cH - margin));

        // Center label: track info
        var center = document.createElement('div');
        center.className = 'ops-radial-center';
        center.style.left = centerX + 'px';
        center.style.top = centerY + 'px';
        var centerLabel = document.createElement('span');
        centerLabel.className = 'ops-radial-center-label';
        centerLabel.textContent = '#' + track.track_id + ' ' + (track.label || '?');
        center.appendChild(centerLabel);
        menu.appendChild(center);

        // Action items arranged radially
        var actions = [
            { label: 'Follow', action: 'follow', angle: 0 },
            { label: 'Lock', action: 'lock', angle: 60 },
            { label: 'Keep Frame', action: 'keep_in_frame', angle: 120 },
            { label: 'Loiter', action: 'loiter', angle: 180 },
            { label: 'Drop', action: 'drop', cls: 'warning', angle: 240 },
            { label: 'Strike', action: 'strike', cls: 'danger', angle: 300 },
        ];

        for (var i = 0; i < actions.length; i++) {
            var a = actions[i];
            var item = document.createElement('button');
            item.className = 'ops-radial-item';
            if (a.cls) item.className += ' ' + a.cls;
            item.textContent = a.label;
            item.dataset.action = a.action;

            // Position using angle: 0 = top, clockwise
            var rad = a.angle * Math.PI / 180;
            var ix = centerX + radius * Math.sin(rad);
            var iy = centerY - radius * Math.cos(rad);
            item.style.left = ix + 'px';
            item.style.top = iy + 'px';

            item.addEventListener('click', onContextMenuAction);
            menu.appendChild(item);
        }

        // Show with scale-up animation
        menu.classList.add('visible');
    }

    function hideContextMenu() {
        var menu = document.getElementById('ops-radial-menu');
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
        } else if (action === 'pixel_lock' || action === 'keep_in_frame') {
            HydraApp.apiPost('/api/approach/pixel_lock/' + trackId, {}).then(function (r) {
                if (r) HydraApp.showToast('Keep-in-frame engaged on #' + trackId, 'success');
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
        card.setAttribute('role', 'dialog');
        card.setAttribute('aria-modal', 'true');
        card.setAttribute('tabindex', '-1');

        var title = document.createElement('div');
        title.className = 'ops-confirm-title';
        if (action === 'strike') {
            title.className += ' danger';
            title.textContent = 'Confirm Strike';
        } else {
            title.className += ' warning';
            title.textContent = 'Confirm Drop';
        }
        var titleId = 'ops-confirm-title-' + action + '-' + trackId;
        title.id = titleId;
        card.setAttribute('aria-labelledby', titleId);
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
        HydraApp.openModal(overlay);
    }

    function hideConfirmOverlay() {
        var overlay = document.getElementById('ops-confirm-overlay');
        if (overlay) HydraApp.closeModal(overlay);
        confirmAction = null;
    }

    // ── HUD Updates ──
    function updateHUD() {
        var stats = HydraApp.state.stats;
        if (!stats) return;
        updateTelemetry(stats);
        updateLockInfo(HydraApp.state.target);
        updateSidebarTracks();
        updateSidebarVehicle(stats);
        drawBoundingBoxes();
    }

    function updateSidebarTracks() {
        var container = document.getElementById('ops-track-list');
        if (!container) return;
        var tracks = HydraApp.state.tracks || [];
        var target = HydraApp.state.target || {};
        var lockedId = target.track_id;

        if (tracks.length === 0) {
            if (container.children.length !== 1 || !container.querySelector('.ops-track-empty')) {
                container.textContent = '';
                var empty = document.createElement('div');
                empty.className = 'ops-track-empty';
                empty.textContent = 'No tracks';
                container.appendChild(empty);
            }
            return;
        }

        // DOM diffing: reuse existing rows
        while (container.children.length > tracks.length) {
            container.removeChild(container.lastChild);
        }
        // Remove empty placeholder if present
        var emptyEl = container.querySelector('.ops-track-empty');
        if (emptyEl) container.removeChild(emptyEl);

        tracks.forEach(function(t, i) {
            var row = container.children[i];
            if (!row || !row.classList.contains('ops-track-row')) {
                row = document.createElement('div');
                row.className = 'ops-track-row';
                var idSpan = document.createElement('span');
                idSpan.className = 'ops-track-id';
                var labelSpan = document.createElement('span');
                labelSpan.className = 'ops-track-label';
                var confSpan = document.createElement('span');
                confSpan.className = 'ops-track-conf';
                row.appendChild(idSpan);
                row.appendChild(labelSpan);
                row.appendChild(confSpan);
                if (i < container.children.length) {
                    container.replaceChild(row, container.children[i]);
                } else {
                    container.appendChild(row);
                }
            }
            row.children[0].textContent = '#' + t.id;
            row.children[1].textContent = t.label || 'unknown';
            row.children[2].textContent = ((t.confidence || 0) * 100).toFixed(0) + '%';
            row.classList.toggle('locked', t.id === lockedId);
        });
    }

    function updateSidebarVehicle(stats) {
        var mode = document.getElementById('ops-info-mode');
        var armed = document.getElementById('ops-info-armed');
        var battery = document.getElementById('ops-info-battery');
        var position = document.getElementById('ops-info-position');

        if (mode) mode.textContent = stats.vehicle_mode || '--';
        if (armed) armed.textContent = stats.armed ? 'ARMED' : 'DISARMED';
        if (battery) battery.textContent = (stats.battery_pct || '--') + '%';
        if (position) position.textContent = stats.position || '--';
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

        // Click outside radial menu to dismiss
        document.addEventListener('click', function (e) {
            var menu = document.getElementById('ops-radial-menu');
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
