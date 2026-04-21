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

    // ── FlightHUD / Cockpit-strip auxiliary state ──
    // 1Hz pollers for the new zones — kept independent from updateHUD so a
    // failure in one zone never starves the rest. SVG repaints DOM-diff via
    // a tiny `_lastSig` cache on each rendered element.
    let auxTimer = null;
    let sdrTickTimer = null;
    let sdrTickValue = 0;
    let hudLayoutLoaded = false;
    let zonePending = { hud: false, cockpit: false };

    // ── Detection-log filter state ──
    // Purely client-side filter over HydraApp.state.detections; persisted per
    // callsign so each team's preferences survive across reloads.
    let detlogFilter = { cls: '', minConf: 0 };
    let detlogFilterWired = false;
    let detlogKnownClasses = [];

    // ── OpsSidebar tab state (5 tabs: tracks/rf/mavlink/tak/events) ──
    // Default 'tracks'. HydraApp.state.opsActiveTab is the source of truth;
    // we never update panels that are hidden to keep overhead negligible.
    let tabTimer = null;
    let takTimer = null;
    let mavLog = [];            // local TX/RX ring, last 30, mirrors mock pattern
    let mavLogSeq = 0;
    let mavLogLastMode = null;
    let mavLogLastLock = null;
    const MAV_LOG_MAX = 30;

    // ── Lifecycle ──
    function onEnter() {
        initTabState();
        wireEventHandlers();
        startVideoPolling();
        if (window.HydraRfHunt && typeof window.HydraRfHunt.onOpsEnter === 'function') {
            window.HydraRfHunt.onOpsEnter();
        }
        updateTimer = setInterval(updateHUD, 500);
        // Slower 1Hz tick for FlightHUD + Cockpit polls — keeps FPS overhead
        // negligible while still feeling alive in the demo.
        auxTimer = setInterval(refreshAuxZones, 1000);
        // SDR spectrum animates every 700ms (mirrors mock setInterval).
        sdrTickTimer = setInterval(animateSdrSpectrum, 700);
        // TAK tab poller — 2s cadence, only fetches when TAK tab is active.
        takTimer = setInterval(refreshTakTab, 2000);
        loadHudLayoutFromConfig();
        loadDetlogFilter();
        wireDetlogFilter();
        wireOpsTabs();
        updateHUD();
        refreshAuxZones();
        animateSdrSpectrum();
        refreshTakTab();
    }

    function onLeave() {
        if (updateTimer) {
            clearInterval(updateTimer);
            updateTimer = null;
        }
        if (auxTimer) {
            clearInterval(auxTimer);
            auxTimer = null;
        }
        if (sdrTickTimer) {
            clearInterval(sdrTickTimer);
            sdrTickTimer = null;
        }
        if (takTimer) {
            clearInterval(takTimer);
            takTimer = null;
        }
        if (window.HydraRfHunt && typeof window.HydraRfHunt.onOpsLeave === 'function') {
            window.HydraRfHunt.onOpsLeave();
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
    // Tactical target display — corner brackets, class-color coding,
    // confidence micro-bar, velocity trails, acquisition pulse on new tracks,
    // and a full target-designator reticle on the locked track.
    //
    // Per-track history is kept in `_bboxHistory` keyed by track_id so the
    // trail polyline + velocity vector can render without needing backend
    // velocity data. `_trackFirstSeen` drives the acquisition flash.
    var _bboxHistory = Object.create(null);   // {track_id: [{t, cx, cy}, ...]}
    var _trackFirstSeen = Object.create(null); // {track_id: timestamp_ms}
    var TRAIL_MAX = 16;              // max breadcrumbs per track
    var TRAIL_MS = 2000;             // fade breadcrumbs after 2s
    var ACQUIRE_MS = 550;            // acquisition-pulse duration

    // Category → tactical color palette. Matches the categorization used by
    // the server (`TACTICAL_CATEGORIES` in server.py) — keep in sync.
    // Colors are slightly desaturated to avoid eye-strain in low-light ops.
    var CAT_COLORS = {
        'People':           '#fbbf24',  // amber
        'Ground Vehicles':  '#38bdf8',  // cyan
        'Aircraft':         '#c4b5fd',  // violet
        'Watercraft':       '#2dd4bf',  // teal
        'Weapons/Threats':  '#ef4444',  // red
        'Equipment':        '#e5e7eb',  // neutral
        'Animals':          '#a78bfa',  // mauve
        'Infrastructure':   '#94a3b8',  // slate
        'Other':            '#86c05a',  // olive-brighter for legibility
    };

    // Minimal client-side mirror of server _CATEGORY_LOOKUP so bbox strokes
    // can adopt category tints without a round-trip. Only uses lowercase.
    var _LABEL_TO_CAT = {
        person: 'People', pedestrian: 'People', people: 'People',
        soldier: 'People', combatant: 'People', civilian: 'People',
        car: 'Ground Vehicles', truck: 'Ground Vehicles', bus: 'Ground Vehicles',
        van: 'Ground Vehicles', motorcycle: 'Ground Vehicles',
        bicycle: 'Ground Vehicles', tank: 'Ground Vehicles', apc: 'Ground Vehicles',
        afv: 'Ground Vehicles', humvee: 'Ground Vehicles', train: 'Ground Vehicles',
        airplane: 'Aircraft', helicopter: 'Aircraft', drone: 'Aircraft',
        'fighter jet': 'Aircraft', 'fighter plane': 'Aircraft',
        boat: 'Watercraft', ship: 'Watercraft', warship: 'Watercraft',
        yacht: 'Watercraft', sailboat: 'Watercraft', kayak: 'Watercraft',
        gun: 'Weapons/Threats', knife: 'Weapons/Threats', grenade: 'Weapons/Threats',
        rifle: 'Weapons/Threats', pistol: 'Weapons/Threats', rpg: 'Weapons/Threats',
        missile: 'Weapons/Threats',
        backpack: 'Equipment', suitcase: 'Equipment', handbag: 'Equipment',
        'cell phone': 'Equipment', laptop: 'Equipment', radio: 'Equipment',
        dog: 'Animals', horse: 'Animals', bird: 'Animals', cat: 'Animals',
        cow: 'Animals', sheep: 'Animals', bear: 'Animals',
        'fire hydrant': 'Infrastructure', 'stop sign': 'Infrastructure',
        'traffic light': 'Infrastructure',
    };

    function _categoryOf(label) {
        if (!label) return 'Other';
        return _LABEL_TO_CAT[String(label).toLowerCase()] || 'Other';
    }

    function _colorFor(track) {
        return CAT_COLORS[_categoryOf(track.label)] || CAT_COLORS.Other;
    }

    // Convert '#rrggbb' to 'rgba(r,g,b,a)' — used for glow fills without
    // allocating a hex-parse per frame (small cache).
    var _rgbaCache = Object.create(null);
    function _withAlpha(hex, alpha) {
        var key = hex + '|' + alpha;
        if (_rgbaCache[key]) return _rgbaCache[key];
        var h = hex.replace('#', '');
        if (h.length === 3) {
            h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
        }
        var r = parseInt(h.slice(0, 2), 16);
        var g = parseInt(h.slice(2, 4), 16);
        var b = parseInt(h.slice(4, 6), 16);
        var out = 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
        _rgbaCache[key] = out;
        return out;
    }

    function drawBoundingBoxes() {
        var canvas = document.getElementById('ops-bbox-canvas');
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        if (!ctx) return;

        ctx.clearRect(0, 0, canvas.width, canvas.height);

        var tracks = HydraApp.state.tracks;
        var mapping = getVideoMapping();
        if (!mapping) return;

        var target = HydraApp.state.target;
        var lockedId = (target && target.locked) ? target.track_id : null;
        var now = Date.now();

        // ── prune breadcrumbs for tracks that no longer exist ──
        var activeIds = Object.create(null);
        if (tracks) for (var j = 0; j < tracks.length; j++) {
            activeIds[tracks[j].track_id] = true;
        }
        for (var tid in _bboxHistory) {
            if (!activeIds[tid]) {
                delete _bboxHistory[tid];
                delete _trackFirstSeen[tid];
            }
        }

        // ── no tracks: render a faint centre reticle so operator knows
        //    the feed is live but nothing is classified yet ──
        if (!tracks || tracks.length === 0) {
            _drawCenterReticle(ctx, mapping);
            return;
        }

        // Pass 1: trails (behind everything else, dim)
        for (var i = 0; i < tracks.length; i++) {
            _drawTrail(ctx, tracks[i], mapping, now);
        }

        // Pass 2: boxes + labels
        for (var k = 0; k < tracks.length; k++) {
            _drawTrackBox(ctx, tracks[k], mapping, now, lockedId);
        }

        // Pass 3: locked-track reticle always on top
        if (lockedId !== null) {
            for (var m = 0; m < tracks.length; m++) {
                if (tracks[m].track_id === lockedId) {
                    _drawLockReticle(ctx, tracks[m], mapping, now);
                    break;
                }
            }
        }
    }

    function _drawCenterReticle(ctx, mapping) {
        var cx = mapping.offsetX + mapping.renderW / 2;
        var cy = mapping.offsetY + mapping.renderH / 2;
        ctx.save();
        ctx.strokeStyle = 'rgba(166, 188, 146, 0.18)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(cx - 18, cy); ctx.lineTo(cx - 4, cy);
        ctx.moveTo(cx + 4, cy);  ctx.lineTo(cx + 18, cy);
        ctx.moveTo(cx, cy - 18); ctx.lineTo(cx, cy - 4);
        ctx.moveTo(cx, cy + 4);  ctx.lineTo(cx, cy + 18);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cx, cy, 2, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
    }

    function _drawTrail(ctx, t, mapping, now) {
        var bbox = t.bbox;
        if (!bbox || bbox.length < 4) return;
        var cx = ((bbox[0] + bbox[2]) / 2) * mapping.scaleX + mapping.offsetX;
        var cy = ((bbox[1] + bbox[3]) / 2) * mapping.scaleY + mapping.offsetY;

        var hist = _bboxHistory[t.track_id];
        if (!hist) {
            hist = [];
            _bboxHistory[t.track_id] = hist;
            _trackFirstSeen[t.track_id] = now;
        }
        hist.push({ t: now, cx: cx, cy: cy });
        // trim by age AND max length
        while (hist.length > TRAIL_MAX || (hist.length > 0 && now - hist[0].t > TRAIL_MS)) {
            hist.shift();
        }
        if (hist.length < 2) return;

        var color = _colorFor(t);
        ctx.save();
        ctx.lineWidth = 1.5;
        ctx.lineCap = 'round';
        for (var i = 1; i < hist.length; i++) {
            var age = (now - hist[i].t) / TRAIL_MS;
            var alpha = Math.max(0, 0.35 * (1 - age));
            ctx.strokeStyle = _withAlpha(color, alpha);
            ctx.beginPath();
            ctx.moveTo(hist[i - 1].cx, hist[i - 1].cy);
            ctx.lineTo(hist[i].cx, hist[i].cy);
            ctx.stroke();
        }
        ctx.restore();
    }

    function _drawTrackBox(ctx, t, mapping, now, lockedId) {
        var bbox = t.bbox;
        if (!bbox || bbox.length < 4) return;

        var x1 = bbox[0] * mapping.scaleX + mapping.offsetX;
        var y1 = bbox[1] * mapping.scaleY + mapping.offsetY;
        var x2 = bbox[2] * mapping.scaleX + mapping.offsetX;
        var y2 = bbox[3] * mapping.scaleY + mapping.offsetY;
        var w = x2 - x1;
        var h = y2 - y1;
        if (w <= 0 || h <= 0) return;

        var isLocked = (lockedId !== null && t.track_id === lockedId);
        var color = isLocked ? '#ffffff' : _colorFor(t);
        var conf = Math.max(0, Math.min(1, t.confidence || 0));

        // Box opacity scales with confidence so low-confidence clutter
        // doesn't overwhelm the view. Floor at 0.45 to stay visible.
        var boxAlpha = isLocked ? 1.0 : (0.45 + 0.55 * conf);

        ctx.save();

        // Faint filled interior for stronger figure/ground (locked only)
        if (isLocked) {
            ctx.fillStyle = _withAlpha(color, 0.06);
            ctx.fillRect(x1, y1, w, h);
        }

        // Hairline full-rect stroke (very faint) to keep the shape readable
        ctx.strokeStyle = _withAlpha(color, isLocked ? 0.35 : 0.22 * boxAlpha);
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.strokeRect(x1 + 0.5, y1 + 0.5, w - 1, h - 1);
        ctx.setLineDash([]);

        // ── Tactical corner brackets — the signature element.
        // Short L-shapes at each corner, thicker than the hairline rect.
        var bLen = Math.max(6, Math.min(18, Math.min(w, h) * 0.22));
        ctx.strokeStyle = _withAlpha(color, boxAlpha);
        ctx.lineWidth = isLocked ? 2.5 : 2;
        ctx.lineCap = 'square';
        ctx.shadowColor = _withAlpha(color, isLocked ? 0.8 : 0.45);
        ctx.shadowBlur = isLocked ? 10 : 4;

        ctx.beginPath();
        // TL
        ctx.moveTo(x1, y1 + bLen); ctx.lineTo(x1, y1); ctx.lineTo(x1 + bLen, y1);
        // TR
        ctx.moveTo(x2 - bLen, y1); ctx.lineTo(x2, y1); ctx.lineTo(x2, y1 + bLen);
        // BL
        ctx.moveTo(x1, y2 - bLen); ctx.lineTo(x1, y2); ctx.lineTo(x1 + bLen, y2);
        // BR
        ctx.moveTo(x2 - bLen, y2); ctx.lineTo(x2, y2); ctx.lineTo(x2, y2 - bLen);
        ctx.stroke();
        ctx.shadowBlur = 0;

        // ── Acquisition pulse on newly seen tracks ──
        var firstSeen = _trackFirstSeen[t.track_id];
        if (firstSeen !== undefined) {
            var age = now - firstSeen;
            if (age < ACQUIRE_MS) {
                var prog = age / ACQUIRE_MS;
                var expand = 12 * prog;
                var pulseAlpha = 0.9 * (1 - prog);
                ctx.strokeStyle = _withAlpha(color, pulseAlpha);
                ctx.lineWidth = 1.5;
                ctx.strokeRect(x1 - expand, y1 - expand, w + expand * 2, h + expand * 2);
            }
        }

        // ── Label chip (track id · label · confidence) ──
        // Layout:
        //   [#123] LABEL                 87%
        //   ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░         (confidence micro-bar)
        var idTag = '#' + t.track_id;
        var lbl = (t.label || '?').toUpperCase();
        var confTxt = Math.round(conf * 100) + '%';

        ctx.font = '600 11px ' + (getComputedStyle(document.body).getPropertyValue('--font-mono') || 'monospace');
        var idW = ctx.measureText(idTag).width + 10;
        var lblW = ctx.measureText(lbl).width + 10;
        var confW = ctx.measureText(confTxt).width + 8;
        var chipH = 16;
        var barH = 2;
        var chipW = Math.max(idW + lblW + confW, Math.min(w, 160));
        var chipY = y1 - chipH - barH - 3;
        var placeBelow = false;
        if (chipY < 2) {
            chipY = y2 + 3;
            placeBelow = true;
        }
        // clamp to right edge
        var chipX = x1;
        if (chipX + chipW > mapping.offsetX + mapping.renderW) {
            chipX = mapping.offsetX + mapping.renderW - chipW;
        }

        // Chip background — slanted left edge for a tactical feel
        ctx.fillStyle = 'rgba(4, 8, 10, 0.82)';
        ctx.beginPath();
        ctx.moveTo(chipX + 4, chipY);
        ctx.lineTo(chipX + chipW, chipY);
        ctx.lineTo(chipX + chipW, chipY + chipH);
        ctx.lineTo(chipX, chipY + chipH);
        ctx.closePath();
        ctx.fill();

        // ID tag — solid color block with dark text
        ctx.fillStyle = _withAlpha(color, 0.95);
        ctx.beginPath();
        ctx.moveTo(chipX + 4, chipY);
        ctx.lineTo(chipX + idW, chipY);
        ctx.lineTo(chipX + idW, chipY + chipH);
        ctx.lineTo(chipX, chipY + chipH);
        ctx.closePath();
        ctx.fill();

        ctx.fillStyle = '#04080a';
        ctx.textBaseline = 'middle';
        ctx.fillText(idTag, chipX + 5, chipY + chipH / 2 + 1);

        // Label
        ctx.fillStyle = '#EFF5EB';
        ctx.fillText(lbl, chipX + idW + 4, chipY + chipH / 2 + 1);

        // Confidence (right-aligned)
        ctx.fillStyle = _withAlpha(color, 0.95);
        ctx.textAlign = 'right';
        ctx.fillText(confTxt, chipX + chipW - 4, chipY + chipH / 2 + 1);
        ctx.textAlign = 'start';

        // Confidence micro-bar under the chip
        var barY = placeBelow ? chipY + chipH + 1 : chipY + chipH + 1;
        ctx.fillStyle = 'rgba(255,255,255,0.08)';
        ctx.fillRect(chipX, barY, chipW, barH);
        ctx.fillStyle = _withAlpha(color, 0.9);
        ctx.fillRect(chipX, barY, chipW * conf, barH);

        ctx.restore();
    }

    function _drawLockReticle(ctx, t, mapping, now) {
        var bbox = t.bbox;
        if (!bbox || bbox.length < 4) return;
        var x1 = bbox[0] * mapping.scaleX + mapping.offsetX;
        var y1 = bbox[1] * mapping.scaleY + mapping.offsetY;
        var x2 = bbox[2] * mapping.scaleX + mapping.offsetX;
        var y2 = bbox[3] * mapping.scaleY + mapping.offsetY;
        var cx = (x1 + x2) / 2;
        var cy = (y1 + y2) / 2;

        // Pulse 0..1 over a 1.2s cycle for a slow, breathing reticle
        var pulse = 0.5 + 0.5 * Math.sin((now % 1200) / 1200 * Math.PI * 2);

        ctx.save();
        // Range lines from frame edges to target (very faint — signals "designated")
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.12)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 6]);
        ctx.beginPath();
        ctx.moveTo(mapping.offsetX, cy); ctx.lineTo(x1, cy);
        ctx.moveTo(x2, cy); ctx.lineTo(mapping.offsetX + mapping.renderW, cy);
        ctx.moveTo(cx, mapping.offsetY); ctx.lineTo(cx, y1);
        ctx.moveTo(cx, y2); ctx.lineTo(cx, mapping.offsetY + mapping.renderH);
        ctx.stroke();
        ctx.setLineDash([]);

        // Breathing reticle ring at target center
        var ringR = 14 + pulse * 6;
        ctx.strokeStyle = 'rgba(255, 255, 255, ' + (0.35 + 0.45 * pulse).toFixed(3) + ')';
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.arc(cx, cy, ringR, 0, Math.PI * 2);
        ctx.stroke();

        // Crosshair ticks
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.moveTo(cx - 10, cy); ctx.lineTo(cx - 3, cy);
        ctx.moveTo(cx + 3, cy);  ctx.lineTo(cx + 10, cy);
        ctx.moveTo(cx, cy - 10); ctx.lineTo(cx, cy - 3);
        ctx.moveTo(cx, cy + 3);  ctx.lineTo(cx, cy + 10);
        ctx.stroke();

        // Centre dot
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(cx, cy, 1.6, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
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
        desc.style.cssText = 'font-family: var(--font-mono); font-size: var(--font-sm); color: var(--text-dim); margin-bottom: var(--s-3);';
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
    // Always-on: telemetry strip, lock overlay, approach panel, bbox overlay,
    // mission rail (mission/pipeline/vehicle — visible as left column).
    // Per-tab: only the active tab's DOM is touched each tick.
    function updateHUD() {
        var stats = HydraApp.state.stats;
        if (!stats) return;
        updateTelemetry(stats);
        updateLockInfo(HydraApp.state.target);
        updateSidebarVehicle(stats);
        updateApproachPanel(stats);
        drawBoundingBoxes();
        // Mission rail (always visible, always updated)
        updateSidebarMission(stats);
        updateSidebarPipeline(stats);
        // Tab count badges — cheap, always update
        updateTabCounts();
        // Per-tab updaters
        var activeTab = getActiveTab();
        if (activeTab === 'tracks') {
            updateSidebarTracks();
        } else if (activeTab === 'rf') {
            updateSidebarRF(HydraApp.state.rfStatus);
        } else if (activeTab === 'mavlink') {
            updateTabMavlink(stats);
        } else if (activeTab === 'events') {
            updateSidebarDetLog(HydraApp.state.detections);
        }
        // Synthesize a MAV log event from mode/lock changes regardless of tab
        // visibility — otherwise opening the MAVLink tab shows an empty log.
        recordMavLogFromState(stats);
        // FlightHUD (HDG/SPD/ALT/Cards) sourced from the same stats sample —
        // keeps the rail in lock-step with the telemetry strip.
        updateFlightHud(stats);
    }

    // ── Tab state + wiring ──
    function initTabState() {
        if (!window.HydraApp) return;
        if (!window.HydraApp.state) window.HydraApp.state = {};
        if (!window.HydraApp.state.opsActiveTab) {
            window.HydraApp.state.opsActiveTab = 'tracks';
        }
    }

    function getActiveTab() {
        if (window.HydraApp && window.HydraApp.state && window.HydraApp.state.opsActiveTab) {
            return window.HydraApp.state.opsActiveTab;
        }
        return 'tracks';
    }

    function wireOpsTabs() {
        var tabs = document.querySelectorAll('.ops-tab');
        if (!tabs || tabs.length === 0) return;
        for (var i = 0; i < tabs.length; i++) {
            var btn = tabs[i];
            if (btn._wired) continue;
            btn._wired = true;
            btn.addEventListener('click', function (e) {
                var id = e.currentTarget.dataset.tab;
                if (id) setActiveTab(id);
            });
        }
        setActiveTab(getActiveTab());
    }

    function setActiveTab(tabId) {
        var valid = ['tracks', 'rf', 'mavlink', 'tak', 'events'];
        if (valid.indexOf(tabId) === -1) tabId = 'tracks';
        if (window.HydraApp && window.HydraApp.state) {
            window.HydraApp.state.opsActiveTab = tabId;
        }
        var tabs = document.querySelectorAll('.ops-tab');
        for (var i = 0; i < tabs.length; i++) {
            var t = tabs[i];
            var is = t.dataset.tab === tabId;
            t.classList.toggle('active', is);
            t.setAttribute('aria-selected', is ? 'true' : 'false');
        }
        var panels = document.querySelectorAll('.ops-tab-panel');
        for (var j = 0; j < panels.length; j++) {
            var p = panels[j];
            var show = p.dataset.tab === tabId;
            p.classList.toggle('active', show);
            if (show) {
                p.removeAttribute('hidden');
            } else {
                p.setAttribute('hidden', 'hidden');
            }
        }
        // Fire the newly-active tab's updater immediately so the panel
        // paints without waiting for the next 500ms tick.
        var stats = (HydraApp.state && HydraApp.state.stats) || {};
        if (tabId === 'tracks') updateSidebarTracks();
        else if (tabId === 'rf') updateSidebarRF(HydraApp.state.rfStatus);
        else if (tabId === 'mavlink') updateTabMavlink(stats);
        else if (tabId === 'tak') refreshTakTab();
        else if (tabId === 'events') {
            updateSidebarDetLog(HydraApp.state.detections);
            refreshAuditLog();
        }
        // Let the RF module throttle its polling to match tab visibility.
        if (window.HydraRfHunt && typeof window.HydraRfHunt.setRfTabActive === 'function') {
            window.HydraRfHunt.setRfTabActive(tabId === 'rf');
        }
    }

    function updateTabCounts() {
        var tracks = HydraApp.state.tracks || [];
        var dets = HydraApp.state.detections || [];
        var rf = HydraApp.state.rfStatus || {};

        var tCount = document.getElementById('ops-tab-count-tracks');
        if (tCount) tCount.textContent = String(tracks.length);

        var eCount = document.getElementById('ops-tab-count-events');
        if (eCount) eCount.textContent = String(Array.isArray(dets) ? dets.length : 0);

        var rCount = document.getElementById('ops-tab-count-rf');
        if (rCount) {
            if (rf && typeof rf.samples === 'number') {
                rCount.textContent = String(rf.samples);
            } else {
                rCount.textContent = '';
            }
        }
        // MAVLink count mirrors local log length (approx. recent traffic).
        var mCount = document.getElementById('ops-tab-count-mavlink');
        if (mCount) mCount.textContent = mavLog.length ? String(mavLog.length) : '';
        // TAK count is set by refreshTakTab once peers arrive.
    }

    // ── MAVLink tab ──
    function nowHHMMSS() {
        var d = new Date();
        function pad(v) { return v < 10 ? '0' + v : String(v); }
        return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }

    function recordMavLogFromState(stats) {
        var s = stats || {};
        var target = HydraApp.state.target || {};
        var mode = s.mode || s.vehicle_mode || null;
        var lock = target.locked ? target.track_id : null;

        if (mode && mode !== mavLogLastMode) {
            mavLogLastMode = mode;
            mavLog.push({
                t: nowHHMMSS(), dir: 'TX', msg: 'SET_MODE',
                detail: 'mode=' + mode,
            });
        }
        if (lock !== mavLogLastLock) {
            mavLogLastLock = lock;
            if (lock != null) {
                mavLog.push({
                    t: nowHHMMSS(), dir: 'TX', msg: 'STATUSTEXT',
                    detail: 'HYDRA: LOCK track #' + lock,
                });
            }
        }
        if (mavLog.length > MAV_LOG_MAX) {
            mavLog = mavLog.slice(-MAV_LOG_MAX);
        }
    }

    function updateTabMavlink(stats) {
        var s = stats || {};
        var fpsEl = document.getElementById('ops-tab-mav-fps');
        var latEl = document.getElementById('ops-tab-mav-latency');
        if (fpsEl) {
            if (typeof s.fps === 'number') {
                fpsEl.textContent = s.fps.toFixed(1);
                fpsEl.style.color = '';
            } else {
                fpsEl.textContent = '--';
                fpsEl.style.color = 'var(--text-dim)';
            }
        }
        if (latEl) {
            var lat = (typeof s.mavlink_latency_ms === 'number') ? s.mavlink_latency_ms
                : (typeof s.mavlinkMs === 'number') ? s.mavlinkMs : null;
            if (lat != null) {
                latEl.textContent = Math.round(lat);
                latEl.style.color = '';
            } else {
                latEl.textContent = '--';
                latEl.style.color = 'var(--text-dim)';
            }
        }
        var log = document.getElementById('ops-tab-mav-log');
        if (!log) return;
        if (mavLog.length === 0) {
            if (!log.querySelector('.ops-tab-empty')) {
                while (log.firstChild) log.removeChild(log.firstChild);
                var empty = document.createElement('div');
                empty.className = 'ops-tab-empty';
                empty.textContent = 'No MAVLink traffic';
                log.appendChild(empty);
            }
            return;
        }
        var emptyEl = log.querySelector('.ops-tab-empty');
        if (emptyEl) log.removeChild(emptyEl);

        var reversed = mavLog.slice().reverse();
        while (log.children.length > reversed.length) log.removeChild(log.lastChild);
        while (log.children.length < reversed.length) {
            var row = document.createElement('div');
            row.className = 'ops-mavlink-log-row';
            var head = document.createElement('div');
            head.className = 'ops-mavlink-log-head';
            var tSpan = document.createElement('span');
            tSpan.className = 'ops-mavlink-log-time';
            var dSpan = document.createElement('span');
            dSpan.className = 'ops-mavlink-log-dir';
            var mSpan = document.createElement('span');
            mSpan.className = 'ops-mavlink-log-msg';
            head.appendChild(tSpan);
            head.appendChild(dSpan);
            head.appendChild(mSpan);
            row.appendChild(head);
            var detail = document.createElement('div');
            detail.className = 'ops-mavlink-log-detail';
            row.appendChild(detail);
            log.appendChild(row);
        }
        for (var i = 0; i < reversed.length; i++) {
            var m = reversed[i];
            var r = log.children[i];
            if (!r) continue;
            var isTx = m.dir === 'TX';
            var isCmd = /SET_MODE|SERVO|POSITION_TARGET|YAW/.test(m.msg || '');
            var isStatus = m.msg === 'STATUSTEXT';
            r.classList.toggle('is-cmd', isCmd);
            r.classList.toggle('is-status', !isCmd && isStatus);
            var head2 = r.children[0];
            var det = r.children[1];
            _setText(head2.children[0], m.t || '--');
            var dirEl = head2.children[1];
            _setText(dirEl, m.dir || '--');
            dirEl.classList.toggle('is-tx', isTx);
            dirEl.classList.toggle('is-rx', !isTx);
            var msgEl = head2.children[2];
            _setText(msgEl, m.msg || '');
            msgEl.classList.toggle('is-cmd', isCmd);
            msgEl.classList.toggle('is-status', !isCmd && isStatus);
            _setText(det, m.detail || '');
        }
    }

    // ── TAK tab ──
    function refreshTakTab() {
        if (getActiveTab() !== 'tak') return;
        if (!HydraApp || typeof HydraApp.apiGet !== 'function') return;
        Promise.all([
            HydraApp.apiGet('/api/tak/peers').catch(function () { return null; }),
            HydraApp.apiGet('/api/tak/commands?limit=50').catch(function () { return null; }),
        ]).then(function (arr) {
            renderTakTab(arr[0] || {}, arr[1] || {});
        });
    }

    function renderTakTab(peersData, cmdsData) {
        var stats = HydraApp.state.stats || {};
        var cs = stats.callsign || 'HYDRA-1';
        var csEl = document.getElementById('ops-tab-tak-callsign');
        if (csEl) csEl.textContent = cs;

        var peers = (peersData && Array.isArray(peersData.peers)) ? peersData.peers : [];
        var peersEl = document.getElementById('ops-tab-tak-peers');
        if (peersEl) {
            peersEl.textContent = peers.length ? (peers.length + ' connected') : '--';
            peersEl.style.color = peers.length ? '' : 'var(--text-dim)';
        }

        var cmds = (cmdsData && Array.isArray(cmdsData.commands)) ? cmdsData.commands : [];
        var inEl = document.getElementById('ops-tab-tak-inbound');
        if (inEl) {
            inEl.textContent = cmds.length ? (cmds.length + ' recent') : '--';
            inEl.style.color = cmds.length ? '' : 'var(--text-dim)';
        }

        var takCount = document.getElementById('ops-tab-count-tak');
        if (takCount) takCount.textContent = peers.length ? String(peers.length) : '';

        var list = document.getElementById('ops-tab-tak-commands');
        if (!list) return;

        if (cmds.length === 0) {
            if (!list.querySelector('.ops-tab-empty')) {
                while (list.firstChild) list.removeChild(list.firstChild);
                var empty = document.createElement('div');
                empty.className = 'ops-tab-empty';
                empty.textContent = 'No inbound commands';
                list.appendChild(empty);
            }
            return;
        }
        var emptyEl = list.querySelector('.ops-tab-empty');
        if (emptyEl) list.removeChild(emptyEl);

        var shown = cmds.slice(-30).reverse();
        while (list.children.length > shown.length) list.removeChild(list.lastChild);
        while (list.children.length < shown.length) {
            var row = document.createElement('div');
            row.className = 'ops-tak-command-row';
            for (var k = 0; k < 3; k++) row.appendChild(document.createElement('span'));
            row.children[0].className = 'ops-tak-command-time';
            row.children[1].className = 'ops-tak-command-callsign';
            row.children[2].className = 'ops-tak-command-type';
            list.appendChild(row);
        }
        for (var i = 0; i < shown.length; i++) {
            var c = shown[i] || {};
            var r = list.children[i];
            if (!r) continue;
            var ts = String(c.timestamp || c.time || '--');
            if (ts.length > 8) ts = ts.slice(-8);
            _setText(r.children[0], ts);
            _setText(r.children[1], c.callsign || c.uid || '--');
            _setText(r.children[2], c.cot_type || c.type || c.command || '--');
        }
    }

    // ── Audit log (Events tab — below detection log) ──
    function refreshAuditLog() {
        if (getActiveTab() !== 'events') return;
        if (!HydraApp || typeof HydraApp.apiGet !== 'function') return;
        HydraApp.apiGet('/api/audit/summary?recent=20').then(function (data) {
            renderAuditLog(data || {});
        }).catch(function () { /* non-fatal */ });
    }

    function renderAuditLog(data) {
        var list = document.getElementById('ops-tab-events-audit');
        if (!list) return;
        var events = (data && Array.isArray(data.recent_events)) ? data.recent_events : [];
        if (events.length === 0) {
            if (!list.querySelector('.ops-tab-empty')) {
                while (list.firstChild) list.removeChild(list.firstChild);
                var empty = document.createElement('div');
                empty.className = 'ops-tab-empty';
                empty.textContent = 'No audit events';
                list.appendChild(empty);
            }
            return;
        }
        var emptyEl = list.querySelector('.ops-tab-empty');
        if (emptyEl) list.removeChild(emptyEl);

        var shown = events.slice(-20).reverse();
        while (list.children.length > shown.length) list.removeChild(list.lastChild);
        while (list.children.length < shown.length) {
            var row = document.createElement('div');
            row.className = 'ops-audit-row';
            for (var k = 0; k < 2; k++) row.appendChild(document.createElement('span'));
            row.children[0].className = 'ops-audit-time';
            row.children[1].className = 'ops-audit-text';
            list.appendChild(row);
        }
        for (var i = 0; i < shown.length; i++) {
            var ev = shown[i] || {};
            var r = list.children[i];
            if (!r) continue;
            var ts = String(ev.timestamp || ev.time || '--');
            if (ts.length > 8) ts = ts.slice(-8);
            _setText(r.children[0], ts);
            var text = ev.event || ev.action || ev.message || ev.kind || '--';
            _setText(r.children[1], String(text));
        }
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
            var tid = (t.track_id != null) ? t.track_id : t.id;
            row.children[0].textContent = '#' + (tid != null ? tid : '?');
            row.children[1].textContent = t.label || 'unknown';
            row.children[2].textContent = ((t.confidence || 0) * 100).toFixed(0) + '%';
            row.classList.toggle('locked', tid === lockedId);
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
        if (position) position.textContent = (window.HydraSimGps ? window.HydraSimGps.withSimSuffix(stats.position || '--') : (stats.position || '--'));
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
                battery.style.color = '';
            } else {
                battery.textContent = '--';
                battery.style.color = 'var(--text-dim)';
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
                if (fix >= 3) {
                    gps.textContent = '3D FIX';
                    gps.style.color = 'var(--success)';
                } else if (fix >= 2) {
                    gps.textContent = '2D';
                    gps.style.color = 'var(--warning)';
                } else {
                    gps.textContent = 'NO FIX';
                    gps.style.color = 'var(--danger)';
                }
            } else {
                gps.textContent = '--';
                gps.style.color = 'var(--text-dim)';
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

    // ── Approach Status Panel ──
    function updateApproachPanel(stats) {
        var approach = stats && stats.approach;
        var section = document.getElementById('ops-approach-section');
        if (!section) return;
        if (!approach || typeof approach.mode !== 'string' || approach.mode === 'idle') {
            section.style.display = 'none';
            return;
        }

        section.style.display = '';

        var modeEl = document.getElementById('ops-approach-mode');
        var elapsedEl = document.getElementById('ops-approach-elapsed');
        var wpEl = document.getElementById('ops-approach-wp');
        if (modeEl) modeEl.textContent = approach.mode.toUpperCase();
        if (elapsedEl) elapsedEl.textContent = (approach.elapsed_sec || 0) + 's';
        if (wpEl) wpEl.textContent = approach.waypoints_sent || 0;

        var armPanel = document.getElementById('ops-approach-arm-status');
        if (armPanel) {
            if (approach.mode === 'strike') {
                armPanel.style.display = 'block';
                var swArm = document.getElementById('ops-approach-sw-arm');
                var hwArm = document.getElementById('ops-approach-hw-arm');
                if (swArm) {
                    swArm.textContent = approach.software_arm ? 'ARMED' : 'SAFE';
                    swArm.style.color = approach.software_arm ? 'var(--olive-muted)' : 'var(--text-dim)';
                }
                if (hwArm) {
                    if (approach.hardware_arm_status === null || approach.hardware_arm_status === undefined) {
                        hwArm.textContent = 'N/A';
                        hwArm.style.color = 'var(--text-dim)';
                    } else {
                        hwArm.textContent = approach.hardware_arm_status ? 'ARMED' : 'SAFE';
                        hwArm.style.color = approach.hardware_arm_status ? 'var(--olive-muted)' : 'var(--text-dim)';
                    }
                }
            } else {
                armPanel.style.display = 'none';
            }
        }
    }

    async function abortApproach() {
        var resp = await HydraApp.apiPost('/api/approach/abort', {});
        if (resp) {
            HydraApp.showToast('Approach abort sent', 'info');
        } else {
            HydraApp.showToast('Approach abort failed — verify with MAV GCS', 'error');
        }
    }

    // ── Sidebar Cards: RF / Mission / Pipeline / Det-Log ──
    // These read the same HydraApp.state that config.js consumes; compact
    // mirrors only. Missing fields render '--' with var(--text-dim).

    var RF_BADGE_CLASS = {
        searching: 'on', homing: 'on', scanning: 'on', converged: 'on',
        idle: 'off', lost: 'warn', aborted: 'off', unavailable: 'off',
    };
    var RF_BADGE_TEXT = {
        idle: 'IDLE', searching: 'SEARCH', homing: 'HOMING',
        converged: 'DONE', lost: 'LOST', aborted: 'STOP', unavailable: 'N/A',
        scanning: 'SCAN',
    };

    // Small history buffer so we can draw a 30-point RSSI sparkline without
    // calling config.js. Trimmed on every tick; survives view switches.
    var rssiHistory = [];
    var RSSI_HIST_MAX = 30;

    function setDim(el, value, isMissing) {
        if (!el) return;
        el.textContent = value;
        el.style.color = isMissing ? 'var(--text-dim)' : '';
    }

    function updateSidebarRF(rf) {
        var section = document.getElementById('ops-rf-section');
        if (!section) return;

        var badge = document.getElementById('ops-rf-state-badge');
        var rssiEl = document.getElementById('ops-rf-rssi');
        var bestEl = document.getElementById('ops-rf-best');
        var samplesEl = document.getElementById('ops-rf-samples');
        var wpEl = document.getElementById('ops-rf-wp');
        var barFill = document.getElementById('ops-rf-bar-fill');
        var spark = document.getElementById('ops-rf-spark');

        if (!rf || typeof rf !== 'object') {
            if (badge) { badge.textContent = 'N/A'; badge.className = 'ops-card-badge off'; }
            setDim(rssiEl, '--', true);
            setDim(bestEl, '\u2605 --', true);
            setDim(samplesEl, '--', true);
            setDim(wpEl, '--', true);
            if (barFill) barFill.style.width = '0%';
            return;
        }

        var state = rf.state || 'unavailable';
        if (badge) {
            badge.textContent = RF_BADGE_TEXT[state] || state.toUpperCase();
            badge.className = 'ops-card-badge ' + (RF_BADGE_CLASS[state] || 'off');
        }

        var rssi = (typeof rf.best_rssi === 'number') ? rf.best_rssi : null;
        var curRssi = (typeof rf.current_rssi === 'number') ? rf.current_rssi : rssi;

        if (rssiEl) {
            if (curRssi != null) {
                rssiEl.textContent = curRssi.toFixed(0) + ' dBm';
                rssiEl.style.color = '';
            } else {
                setDim(rssiEl, '--', true);
            }
        }
        if (bestEl) {
            if (rssi != null) {
                bestEl.textContent = '\u2605 ' + rssi.toFixed(0) + ' dBm';
                bestEl.style.color = 'var(--gold)';
            } else {
                bestEl.textContent = '\u2605 --';
                bestEl.style.color = 'var(--text-dim)';
            }
        }
        if (samplesEl) setDim(samplesEl, rf.samples != null ? String(rf.samples) : '--', rf.samples == null);
        if (wpEl) setDim(wpEl, rf.wp_progress || '--', !rf.wp_progress);

        if (barFill && curRssi != null) {
            var pct = Math.max(0, Math.min(100, curRssi + 100));
            barFill.style.width = pct + '%';
            if (pct > 60) {
                barFill.style.background = 'var(--olive-primary)';
            } else if (pct > 30) {
                barFill.style.background = '#eab308';
            } else {
                barFill.style.background = '#c53030';
            }
        } else if (barFill) {
            barFill.style.width = '0%';
        }

        // Track RSSI history + redraw sparkline (DOM-diff: only rewrite SVG
        // when the most recent point changes).
        if (curRssi != null) {
            var last = rssiHistory[rssiHistory.length - 1];
            if (last !== curRssi) {
                rssiHistory.push(curRssi);
                while (rssiHistory.length > RSSI_HIST_MAX) rssiHistory.shift();
                renderOpsSparkline(spark, rssiHistory);
            }
        }
    }

    function renderOpsSparkline(container, data) {
        if (!container) return;
        while (container.firstChild) container.removeChild(container.firstChild);
        if (!data || data.length < 2) return;

        var svgNs = 'http://www.w3.org/2000/svg';
        var svg = document.createElementNS(svgNs, 'svg');
        svg.setAttribute('viewBox', '0 0 100 24');
        svg.setAttribute('preserveAspectRatio', 'none');

        var min = -100;
        var max = 0;
        var n = data.length;
        var pts = [];
        for (var i = 0; i < n; i++) {
            var x = (i / (n - 1)) * 100;
            var y = 24 - ((data[i] - min) / (max - min)) * 24;
            pts.push(x.toFixed(1) + ',' + y.toFixed(1));
        }

        var poly = document.createElementNS(svgNs, 'polyline');
        poly.setAttribute('points', pts.join(' '));
        poly.setAttribute('fill', 'none');
        poly.setAttribute('stroke', 'var(--olive-primary)');
        poly.setAttribute('stroke-width', '1');
        svg.appendChild(poly);
        container.appendChild(svg);
    }

    function formatElapsed(seconds) {
        if (seconds == null || isNaN(seconds) || seconds < 0) return '--';
        var s = Math.floor(seconds);
        var h = Math.floor(s / 3600);
        var m = Math.floor((s % 3600) / 60);
        var sec = s % 60;
        function pad(v) { return v < 10 ? '0' + v : String(v); }
        return (h > 0 ? h + ':' + pad(m) : m) + ':' + pad(sec);
    }

    function updateSidebarMission(stats) {
        var badge = document.getElementById('ops-mission-badge');
        var nameEl = document.getElementById('ops-mission-name');
        var elapsedEl = document.getElementById('ops-mission-elapsed');
        var detsEl = document.getElementById('ops-mission-dets');
        var endBtn = document.getElementById('ops-btn-mission-end');

        var s = stats || {};
        var isActive = !!s.mission_name;

        if (badge) {
            badge.textContent = isActive ? 'ACTIVE' : 'IDLE';
            badge.className = 'ops-card-badge ' + (isActive ? 'on' : 'off');
        }
        if (nameEl) setDim(nameEl, s.mission_name || '--', !isActive);

        if (elapsedEl) {
            if (isActive && typeof s.mission_elapsed_sec === 'number') {
                elapsedEl.textContent = formatElapsed(s.mission_elapsed_sec);
                elapsedEl.style.color = '';
            } else {
                setDim(elapsedEl, '--', true);
            }
        }
        if (detsEl) {
            var dets = HydraApp.state.detections;
            if (Array.isArray(dets)) {
                detsEl.textContent = String(dets.length);
                detsEl.style.color = '';
            } else {
                setDim(detsEl, '--', true);
            }
        }
        if (endBtn) endBtn.disabled = !isActive;
    }

    function updateSidebarPipeline(stats) {
        var s = stats || {};
        var badge = document.getElementById('ops-pipeline-badge');
        var fpsEl = document.getElementById('ops-pipeline-fps');
        var infEl = document.getElementById('ops-pipeline-inf');
        var pauseBtn = document.getElementById('ops-btn-pipeline-pause');

        var paused = !!s.pipeline_paused;
        if (badge) {
            badge.textContent = paused ? 'PAUSED' : 'RUN';
            badge.className = 'ops-card-badge ' + (paused ? 'warn' : 'on');
        }
        if (fpsEl) {
            if (typeof s.fps === 'number') {
                fpsEl.textContent = s.fps.toFixed(1);
                fpsEl.style.color = '';
            } else {
                setDim(fpsEl, '--', true);
            }
        }
        if (infEl) {
            if (typeof s.inference_ms === 'number') {
                infEl.textContent = s.inference_ms.toFixed(0) + ' ms';
                infEl.style.color = '';
            } else {
                setDim(infEl, '--', true);
            }
        }
        if (pauseBtn) pauseBtn.textContent = paused ? 'Resume' : 'Pause';
    }

    function detlogCallsign() {
        try {
            var app = window.HydraApp;
            if (!app || !app.state) return 'default';
            if (app.state.callsign) return app.state.callsign;
            if (app.state.stats && app.state.stats.callsign) return app.state.stats.callsign;
        } catch (_) { /* noop */ }
        return 'default';
    }

    function detlogStorageKey() {
        return 'hydra-detlog-filter-' + detlogCallsign();
    }

    function loadDetlogFilter() {
        detlogFilter = { cls: '', minConf: 0 };
        try {
            var raw = localStorage.getItem(detlogStorageKey());
            if (!raw) return;
            var parsed = JSON.parse(raw);
            if (parsed && typeof parsed === 'object') {
                if (typeof parsed.cls === 'string') detlogFilter.cls = parsed.cls;
                if (typeof parsed.minConf === 'number' && isFinite(parsed.minConf)) {
                    detlogFilter.minConf = Math.max(0, Math.min(1, parsed.minConf));
                }
            }
        } catch (_) { /* ignore malformed storage */ }
    }

    function saveDetlogFilter() {
        try {
            localStorage.setItem(detlogStorageKey(), JSON.stringify(detlogFilter));
        } catch (_) { /* quota / disabled storage — non-fatal */ }
    }

    function clearDetlogFilter() {
        detlogFilter = { cls: '', minConf: 0 };
        try { localStorage.removeItem(detlogStorageKey()); } catch (_) { /* noop */ }
        syncDetlogFilterControls();
        updateSidebarDetLog(HydraApp.state.detections);
    }

    function syncDetlogFilterControls() {
        var sel = document.getElementById('ops-detlog-class');
        var rng = document.getElementById('ops-detlog-conf');
        var lbl = document.getElementById('ops-detlog-conf-value');
        if (sel) sel.value = detlogFilter.cls || '';
        if (rng) rng.value = String(detlogFilter.minConf || 0);
        if (lbl) lbl.textContent = Math.round((detlogFilter.minConf || 0) * 100) + '%';
    }

    function wireDetlogFilter() {
        if (detlogFilterWired) {
            syncDetlogFilterControls();
            return;
        }
        var sel = document.getElementById('ops-detlog-class');
        var rng = document.getElementById('ops-detlog-conf');
        var btn = document.getElementById('ops-detlog-clear');
        if (sel) {
            sel.addEventListener('change', function () {
                detlogFilter.cls = sel.value || '';
                saveDetlogFilter();
                updateSidebarDetLog(HydraApp.state.detections);
            });
        }
        if (rng) {
            rng.addEventListener('input', function () {
                var v = parseFloat(rng.value);
                detlogFilter.minConf = isFinite(v) ? v : 0;
                var lbl = document.getElementById('ops-detlog-conf-value');
                if (lbl) lbl.textContent = Math.round(detlogFilter.minConf * 100) + '%';
                saveDetlogFilter();
                updateSidebarDetLog(HydraApp.state.detections);
            });
        }
        if (btn) {
            btn.addEventListener('click', clearDetlogFilter);
        }
        syncDetlogFilterControls();
        detlogFilterWired = true;
    }

    function refreshDetlogClassOptions(detections) {
        var sel = document.getElementById('ops-detlog-class');
        if (!sel) return;
        var seen = {};
        var classes = [];
        for (var i = 0; i < detections.length; i++) {
            var lbl = detections[i] && detections[i].label;
            if (typeof lbl === 'string' && lbl && !seen[lbl]) {
                seen[lbl] = true;
                classes.push(lbl);
            }
        }
        classes.sort();
        if (classes.length === detlogKnownClasses.length) {
            var same = true;
            for (var j = 0; j < classes.length; j++) {
                if (classes[j] !== detlogKnownClasses[j]) { same = false; break; }
            }
            if (same) return;
        }
        detlogKnownClasses = classes;
        var current = detlogFilter.cls || '';
        while (sel.firstChild) sel.removeChild(sel.firstChild);
        var allOpt = document.createElement('option');
        allOpt.value = '';
        allOpt.textContent = 'All';
        sel.appendChild(allOpt);
        for (var k = 0; k < classes.length; k++) {
            var opt = document.createElement('option');
            opt.value = classes[k];
            opt.textContent = classes[k];
            sel.appendChild(opt);
        }
        // Preserve selection even when the class has not yet appeared in the
        // current poll — operators should see "no matches" rather than silent
        // reset to All.
        if (current && !seen[current]) {
            var ghost = document.createElement('option');
            ghost.value = current;
            ghost.textContent = current;
            sel.appendChild(ghost);
        }
        sel.value = current;
    }

    function updateSidebarDetLog(detections) {
        var log = document.getElementById('ops-detlog');
        if (!log) return;

        var dets = Array.isArray(detections) ? detections : [];
        refreshDetlogClassOptions(dets);

        var cls = detlogFilter.cls || '';
        var minConf = detlogFilter.minConf || 0;
        var filtered = dets;
        if (cls || minConf > 0) {
            filtered = [];
            for (var fi = 0; fi < dets.length; fi++) {
                var d0 = dets[fi] || {};
                if (cls && d0.label !== cls) continue;
                var c0 = typeof d0.confidence === 'number' ? d0.confidence : 0;
                if (c0 < minConf) continue;
                filtered.push(d0);
            }
        }

        if (filtered.length === 0) {
            if (!log.querySelector('.ops-detlog-empty')) {
                while (log.firstChild) log.removeChild(log.firstChild);
                var empty = document.createElement('div');
                empty.className = 'ops-detlog-empty';
                empty.textContent = (cls || minConf > 0) ? 'No detections match filter' : 'No detections yet';
                log.appendChild(empty);
            } else {
                var existing = log.querySelector('.ops-detlog-empty');
                existing.textContent = (cls || minConf > 0) ? 'No detections match filter' : 'No detections yet';
            }
            return;
        }

        // Render newest-first, cap to 20 rows; DOM-diff by count.
        var rows = Math.min(filtered.length, 20);
        var emptyEl = log.querySelector('.ops-detlog-empty');
        if (emptyEl) log.removeChild(emptyEl);
        while (log.children.length > rows) log.removeChild(log.lastChild);
        while (log.children.length < rows) {
            var row = document.createElement('div');
            row.className = 'ops-detlog-entry';
            row.appendChild(document.createElement('span'));
            row.appendChild(document.createElement('span'));
            row.appendChild(document.createElement('span'));
            row.children[0].className = 'ops-detlog-time';
            row.children[1].className = 'ops-detlog-label';
            row.children[2].className = 'ops-detlog-conf';
            log.appendChild(row);
        }

        for (var i = 0; i < rows; i++) {
            var d = filtered[filtered.length - 1 - i] || {};
            var row2 = log.children[i];
            if (!row2) continue;
            var t = '';
            if (typeof d.timestamp === 'string' && d.timestamp.indexOf('T') > -1) {
                t = d.timestamp.split('T')[1].split('.')[0];
            }
            row2.children[0].textContent = t || '--';
            row2.children[1].textContent = d.label || 'unknown';
            var c = typeof d.confidence === 'number' ? (d.confidence * 100).toFixed(0) + '%' : '--';
            row2.children[2].textContent = c;
        }
    }

    // ── Sidebar Actions: mission end / export, pipeline pause / stop ──
    async function endMissionFromOps() {
        var resp = await HydraApp.apiPost('/api/mission/end', {});
        if (resp && resp.status === 'ended') {
            HydraApp.showToast('Sortie ended', 'info');
        }
    }

    async function exportWaypointsFromOps() {
        try {
            var resp = await fetch('/api/export/waypoints', { credentials: 'same-origin' });
            if (!resp.ok) {
                HydraApp.showToast('Waypoint export failed', 'error');
                return;
            }
            var blob = await resp.blob();
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = 'hydra-waypoints.waypoints';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            HydraApp.showToast('Waypoints exported', 'success');
        } catch (e) {
            HydraApp.showToast('Waypoint export failed', 'error');
        }
    }

    async function togglePipelinePauseFromOps() {
        var stats = HydraApp.state.stats || {};
        var paused = !!stats.pipeline_paused;
        var msg = paused
            ? 'Resume detection loop? (does NOT restart Python or Docker)'
            : 'Pause detection loop? (does NOT restart Python or Docker)';
        if (!confirm(msg)) return;
        var resp = await HydraApp.apiPost('/api/pipeline/pause', {});
        if (resp) HydraApp.showToast(paused ? 'Pipeline resumed' : 'Pipeline paused', 'info');
    }

    async function stopPipelineFromOps() {
        if (!confirm('Stop detection loop? (does NOT restart Python or Docker)')) return;
        var resp = await HydraApp.apiPost('/api/pipeline/stop', {});
        if (resp) HydraApp.showToast('Pipeline stopped', 'info');
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

        // Quick action: Beep — apiPost handles transport error toasts; we still
        // need to distinguish backend {status:"ok"} from {status:"failed"} (HTTP
        // 200 with failed callback — operator would otherwise see "Beep sent"
        // during degraded MAVLink even though the tune never went out).
        var beepBtn = document.getElementById('ops-btn-beep');
        if (beepBtn) {
            beepBtn.addEventListener('click', function () {
                HydraApp.apiPost('/api/vehicle/beep', { tune: 'alert' }).then(function (r) {
                    if (!r) return; // transport error already toasted by apiPost
                    if (r.status === 'ok') {
                        HydraApp.showToast('Beep sent', 'success');
                    } else {
                        HydraApp.showToast('Beep failed — MAVLink degraded', 'error');
                    }
                });
            });
        }

        // Approach: Abort
        var approachAbortBtn = document.getElementById('ops-btn-approach-abort');
        if (approachAbortBtn) {
            approachAbortBtn.addEventListener('click', function () {
                abortApproach();
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

        // Sidebar card buttons (mission / pipeline mirrors)
        var missionEndBtn = document.getElementById('ops-btn-mission-end');
        if (missionEndBtn) missionEndBtn.addEventListener('click', function () {
            if (!confirm('End current sortie?')) return;
            endMissionFromOps();
        });
        var missionExportBtn = document.getElementById('ops-btn-mission-export');
        if (missionExportBtn) missionExportBtn.addEventListener('click', exportWaypointsFromOps);

        var pipelinePauseBtn = document.getElementById('ops-btn-pipeline-pause');
        if (pipelinePauseBtn) pipelinePauseBtn.addEventListener('click', togglePipelinePauseFromOps);

        var pipelineStopBtn = document.getElementById('ops-btn-pipeline-stop');
        if (pipelineStopBtn) pipelineStopBtn.addEventListener('click', stopPipelineFromOps);

        // FlightHUD layout picker + Cockpit TAK click-to-expand
        wireFlightHudPicker();
        wireCockpitMapClick();
    }

    // ── FlightHUD updates ──
    // Renders HDG tape, SPD/ALT VTapes, ReadoutCards, Status strip from
    // the existing /api/stats sample. Falls back to '--' in --text-dim
    // when the underlying field is null (matches mock spec).
    var SVG_NS = 'http://www.w3.org/2000/svg';

    function _setText(el, value) {
        if (el && el.textContent !== value) el.textContent = value;
    }

    function _setAttr(el, name, value) {
        if (el && el.getAttribute(name) !== value) el.setAttribute(name, value);
    }

    function renderHdgTape(hdg) {
        var svg = document.getElementById('ops-fhud-hdg-svg');
        var readout = document.getElementById('ops-fhud-hdg-readout');
        if (!svg) return;

        var hasHdg = (typeof hdg === 'number' && isFinite(hdg));
        var sig = hasHdg ? Math.round(hdg) : 'na';
        if (svg._lastSig === sig) return;
        svg._lastSig = sig;

        while (svg.firstChild) svg.removeChild(svg.firstChild);

        if (!hasHdg) {
            if (readout) {
                readout.textContent = '--';
                readout.style.color = 'var(--text-dim)';
            }
            return;
        }

        var range = 60;
        for (var d = -range / 2; d <= range / 2; d += 5) {
            var real = (((hdg + d) % 360) + 360) % 360;
            var major = (Math.round(real) % 30 === 0);
            var x = (d + range / 2) / range * 160; // viewBox 160 wide
            var line = document.createElementNS(SVG_NS, 'line');
            line.setAttribute('x1', x.toFixed(1));
            line.setAttribute('x2', x.toFixed(1));
            line.setAttribute('y1', major ? '0' : '6');
            line.setAttribute('y2', major ? '14' : '12');
            line.setAttribute('stroke', 'var(--olive-muted)');
            line.setAttribute('stroke-width', '1');
            line.setAttribute('opacity', major ? '0.9' : '0.35');
            svg.appendChild(line);
            if (major) {
                var txt = document.createElementNS(SVG_NS, 'text');
                txt.setAttribute('x', x.toFixed(1));
                txt.setAttribute('y', '26');
                txt.setAttribute('fill', 'var(--text-primary)');
                txt.setAttribute('font-size', '9');
                txt.setAttribute('text-anchor', 'middle');
                txt.setAttribute('font-family', 'var(--font-mono)');
                txt.textContent = String(Math.round(real)).padStart(3, '0');
                svg.appendChild(txt);
            }
        }
        if (readout) {
            readout.textContent = String(Math.round(hdg)).padStart(3, '0') + '\u00B0';
            readout.style.color = '';
        }
    }

    function renderVTape(svgId, valueId, value, step, majorEvery, decimals) {
        var svg = document.getElementById(svgId);
        var valueEl = document.getElementById(valueId);
        if (!svg) return;

        var hasValue = (typeof value === 'number' && isFinite(value));
        var sig = hasValue ? value.toFixed(decimals != null ? decimals : 1) : 'na';
        if (svg._lastSig === sig) return;
        svg._lastSig = sig;

        while (svg.firstChild) svg.removeChild(svg.firstChild);

        if (!hasValue) {
            if (valueEl) {
                valueEl.textContent = '--';
                valueEl.style.color = 'var(--text-dim)';
            }
            return;
        }

        var count = 11;
        var half = Math.floor(count / 2);
        for (var i = -half; i <= half; i++) {
            var v = value + i * step;
            var major = (Math.round(v / step) % majorEvery === 0);
            var y = ((i + count / 2) / count) * 120;
            var line = document.createElementNS(SVG_NS, 'line');
            line.setAttribute('x1', '0');
            line.setAttribute('x2', major ? '12' : '6');
            line.setAttribute('y1', y.toFixed(1));
            line.setAttribute('y2', y.toFixed(1));
            line.setAttribute('stroke', 'var(--olive-muted)');
            line.setAttribute('stroke-width', '1');
            line.setAttribute('opacity', major ? '0.9' : '0.3');
            svg.appendChild(line);
            if (major) {
                var txt = document.createElementNS(SVG_NS, 'text');
                txt.setAttribute('x', '16');
                txt.setAttribute('y', y.toFixed(1));
                txt.setAttribute('fill', '#888');
                txt.setAttribute('font-size', '8');
                txt.setAttribute('dominant-baseline', 'central');
                txt.setAttribute('font-family', 'var(--font-mono)');
                txt.textContent = v.toFixed(0);
                svg.appendChild(txt);
            }
        }
        if (valueEl) {
            valueEl.textContent = value.toFixed(decimals != null ? decimals : 1);
            valueEl.style.color = '';
        }
    }

    function _toneForBatt(pct) {
        if (pct == null) return 'dim';
        if (pct > 50) return 'good';
        if (pct > 25) return 'warn';
        return 'bad';
    }

    function _toneForLink(pct) {
        if (pct == null) return 'dim';
        if (pct > 80) return 'good';
        if (pct > 50) return 'warn';
        return 'bad';
    }

    function renderReadoutCards(stats) {
        var s = stats || {};
        // Battery
        var battEl = document.getElementById('ops-fhud-card-batt');
        var battBig = document.getElementById('ops-fhud-card-batt-big');
        var battSub = document.getElementById('ops-fhud-card-batt-sub');
        if (battEl) {
            var battPct = (typeof s.battery === 'number' && isFinite(s.battery)) ? s.battery : null;
            var battTone = _toneForBatt(battPct);
            _setAttr(battEl, 'data-tone', battTone);
            _setText(battBig, battPct != null ? battPct.toFixed(0) + '%' : '--');
            _setText(battSub, battPct != null ? 'on bus' : 'no sensor');
        }
        // Link / RSSI from RF status (mavlink RSSI not generally surfaced in stats)
        var linkEl = document.getElementById('ops-fhud-card-link');
        var linkBig = document.getElementById('ops-fhud-card-link-big');
        var linkSub = document.getElementById('ops-fhud-card-link-sub');
        if (linkEl) {
            var rf = HydraApp.state.rfStatus || {};
            var rssi = (typeof rf.current_rssi === 'number') ? rf.current_rssi : null;
            var linkPct = rssi != null ? Math.max(0, Math.min(100, rssi + 100)) : null;
            _setAttr(linkEl, 'data-tone', _toneForLink(linkPct));
            _setText(linkBig, linkPct != null ? linkPct.toFixed(0) + '%' : '--');
            _setText(linkSub, rssi != null ? rssi.toFixed(0) + ' dBm' : '--');
        }
        // Position — reuse SIM GPS suffix helper
        var posEl = document.getElementById('ops-fhud-card-pos');
        var posBig = document.getElementById('ops-fhud-card-pos-big');
        var posSub = document.getElementById('ops-fhud-card-pos-sub');
        if (posEl) {
            _setAttr(posEl, 'data-tone', 'info');
            var pos = s.position || null;
            var posStr = window.HydraSimGps && pos
                ? window.HydraSimGps.withSimSuffix(pos)
                : (pos || '--');
            _setText(posBig, pos ? '\u2022' : '--');
            _setText(posSub, posStr);
        }
        // GPS sats
        var gpsEl = document.getElementById('ops-fhud-card-gps');
        var gpsBig = document.getElementById('ops-fhud-card-gps-big');
        var gpsSub = document.getElementById('ops-fhud-card-gps-sub');
        if (gpsEl) {
            var sats = (typeof s.gps_sats === 'number') ? s.gps_sats : null;
            var fix = s.gps_fix;
            var tone = (sats != null && sats >= 10) ? 'good'
                     : (sats != null && sats >= 7) ? 'warn'
                     : (sats != null) ? 'bad' : 'dim';
            _setAttr(gpsEl, 'data-tone', tone);
            _setText(gpsBig, sats != null ? sats + ' sat' : '--');
            _setText(gpsSub, fix != null ? (fix >= 3 ? '3D fix' : fix >= 2 ? '2D' : 'no fix') : '--');
        }
    }

    function renderStatusStrip(stats) {
        var s = stats || {};
        var rf = HydraApp.state.rfStatus || {};
        var rssi = (typeof rf.current_rssi === 'number') ? rf.current_rssi : null;
        var linkPct = rssi != null ? Math.max(0, Math.min(100, rssi + 100)) : null;
        var linkText = document.getElementById('ops-fhud-status-link');
        var battEl = document.getElementById('ops-fhud-status-batt');
        if (linkText) linkText.textContent = 'LINK ' + (linkPct != null ? linkPct.toFixed(0) + '%' : '--');
        if (battEl) {
            var batt = (typeof s.battery === 'number') ? s.battery : null;
            battEl.textContent = batt != null ? batt.toFixed(0) + '%' : '--';
            battEl.classList.toggle('warn', batt != null && batt <= 30);
        }
    }

    function renderTargetBlock(stats) {
        var target = HydraApp.state.target || {};
        var wrap = document.getElementById('ops-fhud-target');
        var rows = document.getElementById('ops-fhud-target-rows');
        if (!wrap || !rows) return;

        var locked = !!target.locked;
        wrap.classList.toggle('locked', locked);

        if (!locked) {
            // DOM-diff: only rebuild if not already empty
            if (rows.children.length !== 1 || !rows.querySelector('.flight-hud-target-empty')) {
                while (rows.firstChild) rows.removeChild(rows.firstChild);
                var empty = document.createElement('div');
                empty.className = 'flight-hud-target-empty';
                empty.textContent = 'no lock';
                rows.appendChild(empty);
            }
            return;
        }

        var fields = [
            ['ID',  '#' + (target.track_id != null ? target.track_id : '--')],
            ['CLS', String(target.label || '--').toUpperCase()],
            ['CNF', target.confidence != null ? Math.round(target.confidence * 100) + '%' : '--'],
            ['RNG', target.range_m != null ? Math.round(target.range_m) + ' m' : '--'],
            ['BRG', target.bearing_deg != null ? String(Math.round(target.bearing_deg)).padStart(3, '0') + '\u00B0' : '--'],
        ];

        // Rebuild only if row count changes; otherwise diff cell text
        if (rows.children.length !== fields.length || rows.querySelector('.flight-hud-target-empty')) {
            while (rows.firstChild) rows.removeChild(rows.firstChild);
            for (var i = 0; i < fields.length; i++) {
                var row = document.createElement('div');
                row.className = 'flight-hud-target-row';
                var k = document.createElement('span');
                var v = document.createElement('span');
                row.appendChild(k);
                row.appendChild(v);
                rows.appendChild(row);
            }
        }
        for (var j = 0; j < fields.length; j++) {
            var r = rows.children[j];
            if (r) {
                _setText(r.children[0], fields[j][0]);
                _setText(r.children[1], fields[j][1]);
            }
        }
    }

    function updateFlightHud(stats) {
        var s = stats || {};
        renderHdgTape(typeof s.heading === 'number' ? s.heading : null);
        renderVTape('ops-fhud-spd-svg', 'ops-fhud-spd-value',
            typeof s.speed === 'number' ? s.speed : null, 1, 2, 1);
        renderVTape('ops-fhud-alt-svg', 'ops-fhud-alt-value',
            typeof s.altitude === 'number' ? s.altitude : null, 2, 5, 0);
        renderReadoutCards(s);
        renderStatusStrip(s);
        renderTargetBlock(s);
    }

    // ── Cockpit strip updates (1 Hz) ──
    function refreshAuxZones() {
        // ServoPanDial — /api/servo/status
        if (!zonePending.hud) {
            zonePending.hud = true;
            HydraApp.apiGet('/api/servo/status').then(function (data) {
                zonePending.hud = false;
                renderServoDial(data || {});
            }).catch(function () { zonePending.hud = false; });
        }
        // Cockpit map peers — /api/tak/peers
        // SDR device list — /api/rf/ambient_scan
        if (!zonePending.cockpit) {
            zonePending.cockpit = true;
            Promise.all([
                HydraApp.apiGet('/api/tak/peers').catch(function () { return null; }),
                HydraApp.apiGet('/api/rf/ambient_scan').catch(function () { return null; }),
            ]).then(function (arr) {
                zonePending.cockpit = false;
                renderCockpitMap(arr[0] || {});
                renderCockpitSdr(arr[1] || {});
            }).catch(function () { zonePending.cockpit = false; });
        }
        updateCockpitStrip();
    }

    function renderServoDial(data) {
        var cell = document.getElementById('ops-cockpit-servo');
        var svg = document.getElementById('ops-cockpit-servo-svg');
        var pillEl = document.getElementById('ops-cockpit-servo-pill');
        var panEl = document.getElementById('ops-cockpit-servo-pan');
        var tiltEl = document.getElementById('ops-cockpit-servo-tilt');
        var rateEl = document.getElementById('ops-cockpit-servo-rate');
        var rateLabel = document.getElementById('ops-cockpit-servo-rate-label');
        if (!svg || !cell) return;

        var enabled = data.enabled !== false;
        var pan = (typeof data.pan_deg === 'number') ? data.pan_deg : 0;
        var tilt = (typeof data.tilt_deg === 'number') ? data.tilt_deg : 0;
        var scanning = !!data.scanning;
        var locked = data.locked_track_id != null;
        var target = HydraApp.state.target || {};
        var strike = target.approach_mode === 'strike';
        var mode = strike ? 'STRIKE' : (locked ? 'LOCKED' : 'SCAN');
        cell.setAttribute('data-mode', mode);
        if (pillEl) pillEl.textContent = scanning ? 'SCAN' : (locked ? 'TRACK' : (enabled ? 'IDLE' : 'OFF'));

        var sig = enabled + '|' + Math.round(pan) + '|' + Math.round(tilt) + '|' + mode;
        if (svg._lastSig !== sig) {
            svg._lastSig = sig;
            while (svg.firstChild) svg.removeChild(svg.firstChild);

            // Defs: dial gradient
            var defs = document.createElementNS(SVG_NS, 'defs');
            var grad = document.createElementNS(SVG_NS, 'radialGradient');
            grad.setAttribute('id', 'ops-cockpit-servo-grad');
            grad.setAttribute('cx', '50%');
            grad.setAttribute('cy', '100%');
            grad.setAttribute('r', '90%');
            var stop1 = document.createElementNS(SVG_NS, 'stop');
            stop1.setAttribute('offset', '0%');
            stop1.setAttribute('stop-color', 'rgba(56,87,35,0.18)');
            var stop2 = document.createElementNS(SVG_NS, 'stop');
            stop2.setAttribute('offset', '100%');
            stop2.setAttribute('stop-color', 'rgba(56,87,35,0)');
            grad.appendChild(stop1);
            grad.appendChild(stop2);
            defs.appendChild(grad);
            svg.appendChild(defs);

            // Arc
            var arc = document.createElementNS(SVG_NS, 'path');
            arc.setAttribute('d', 'M 10 100 A 90 90 0 0 1 190 100');
            arc.setAttribute('fill', 'url(#ops-cockpit-servo-grad)');
            arc.setAttribute('stroke', 'var(--border-default)');
            arc.setAttribute('stroke-width', '1');
            svg.appendChild(arc);

            // Ticks every 15°
            for (var i = 0; i < 13; i++) {
                var angle = -90 + i * 15;
                var a = angle * Math.PI / 180;
                var x1 = 100 + Math.sin(a) * 88;
                var y1 = 100 - Math.cos(a) * 88;
                var inner = (i % 2 === 0) ? 78 : 82;
                var x2 = 100 + Math.sin(a) * inner;
                var y2 = 100 - Math.cos(a) * inner;
                var t = document.createElementNS(SVG_NS, 'line');
                t.setAttribute('x1', x1.toFixed(1));
                t.setAttribute('y1', y1.toFixed(1));
                t.setAttribute('x2', x2.toFixed(1));
                t.setAttribute('y2', y2.toFixed(1));
                t.setAttribute('stroke', (i % 2 === 0) ? '#444' : '#2a2a2a');
                t.setAttribute('stroke-width', (i % 2 === 0) ? '1' : '0.6');
                svg.appendChild(t);
                if (i % 2 === 0) {
                    var lbl = document.createElementNS(SVG_NS, 'text');
                    lbl.setAttribute('x', (100 + Math.sin(a) * 72).toFixed(1));
                    lbl.setAttribute('y', (100 - Math.cos(a) * 72 + 3).toFixed(1));
                    lbl.setAttribute('text-anchor', 'middle');
                    lbl.setAttribute('font-family', 'var(--font-mono)');
                    lbl.setAttribute('font-size', '7');
                    lbl.setAttribute('fill', '#555');
                    lbl.textContent = angle === 0 ? '0' : (angle > 0 ? '+' + angle : String(angle));
                    svg.appendChild(lbl);
                }
            }

            // FOV cone + needle (rotated by pan)
            var FOV = 60;
            var rad = FOV / 2 * Math.PI / 180;
            var coneX1 = -Math.sin(rad) * 90;
            var coneY1 = -Math.cos(rad) * 90;
            var coneX2 = Math.sin(rad) * 90;
            var coneY2 = -Math.cos(rad) * 90;
            var g = document.createElementNS(SVG_NS, 'g');
            g.setAttribute('transform', 'translate(100 100) rotate(' + pan.toFixed(1) + ')');
            var fov = document.createElementNS(SVG_NS, 'path');
            fov.setAttribute('d', 'M 0 0 L ' + coneX1.toFixed(1) + ' ' + coneY1.toFixed(1)
                + ' A 90 90 0 0 1 ' + coneX2.toFixed(1) + ' ' + coneY2.toFixed(1) + ' Z');
            fov.setAttribute('fill', mode === 'LOCKED' ? 'rgba(252,211,77,0.12)' : 'rgba(166,188,146,0.1)');
            fov.setAttribute('stroke', mode === 'LOCKED' ? 'rgba(252,211,77,0.4)' : 'rgba(166,188,146,0.35)');
            fov.setAttribute('stroke-width', '0.6');
            fov.setAttribute('stroke-dasharray', '3 2');
            g.appendChild(fov);
            var needleColor = mode === 'STRIKE' ? 'var(--danger)'
                : mode === 'LOCKED' ? 'var(--warning)' : 'var(--olive-muted)';
            var needle = document.createElementNS(SVG_NS, 'line');
            needle.setAttribute('x1', '0');
            needle.setAttribute('y1', '0');
            needle.setAttribute('x2', '0');
            needle.setAttribute('y2', '-90');
            needle.setAttribute('stroke', needleColor);
            needle.setAttribute('stroke-width', '2');
            g.appendChild(needle);
            var tip = document.createElementNS(SVG_NS, 'circle');
            tip.setAttribute('cx', '0');
            tip.setAttribute('cy', '-90');
            tip.setAttribute('r', '2.5');
            tip.setAttribute('fill', needleColor);
            g.appendChild(tip);
            svg.appendChild(g);

            var hub = document.createElementNS(SVG_NS, 'circle');
            hub.setAttribute('cx', '100');
            hub.setAttribute('cy', '100');
            hub.setAttribute('r', '4');
            hub.setAttribute('fill', '#0a0a0a');
            hub.setAttribute('stroke', '#444');
            hub.setAttribute('stroke-width', '1');
            svg.appendChild(hub);
        }

        if (panEl) panEl.textContent = enabled ? (pan >= 0 ? '+' : '') + pan.toFixed(0) + '\u00B0' : '--';
        if (tiltEl) tiltEl.textContent = enabled ? (tilt >= 0 ? '+' : '') + tilt.toFixed(0) + '\u00B0' : '--';
        if (rateEl) {
            rateEl.textContent = enabled ? Math.round(1500 + pan * 5.5) : '--';
        }
        if (rateLabel) rateLabel.textContent = 'PWM';
    }

    // Cockpit TAK map is now a small Leaflet instance managed by tak-map.js.
    // Init on first call, no-op after. Called from the polling loop only to
    // kick the init — the shared module handles its own data refresh.
    var _cockpitMapCtl = null;
    function renderCockpitMap(_data) {
        if (_cockpitMapCtl) return;
        if (!window.HydraTakMap) return;
        var container = document.getElementById('ops-cockpit-tak-map');
        if (!container) return;
        _cockpitMapCtl = HydraTakMap.init({
            containerId: 'ops-cockpit-tak-map',
            pollMs: 2500,
            showZoom: false,
            showAttribution: false,
            showTracks: true,
            onTitleUpdate: function (callsign, _info) {
                var titleEl = document.getElementById('ops-cockpit-tak-title');
                if (titleEl) titleEl.textContent = 'TAK \u00B7 ' + callsign;
            },
        });
        // Attach RF overlay so RSSI dots, breadcrumb trail, and
        // best-position star render alongside TAK tracks.
        if (_cockpitMapCtl && _cockpitMapCtl.map && window.HydraRfMap) {
            window.HydraRfMap.attach(_cockpitMapCtl.map);
        }
    }

    function renderCockpitSdr(data) {
        var listEl = document.getElementById('ops-cockpit-sdr-list');
        var devEl = document.getElementById('ops-cockpit-sdr-dev');
        var newEl = document.getElementById('ops-cockpit-sdr-new');
        var droneEl = document.getElementById('ops-cockpit-sdr-drone');
        if (!listEl) return;

        var samples = (data && Array.isArray(data.samples)) ? data.samples : [];
        // Synthesize devices from samples — not all backends populate names;
        // we map sample.callsign/mac/vendor/rssi when present.
        var devices = samples.slice(0, 12).map(function (s, i) {
            return {
                type: s.type || (i === 0 ? 'wifi' : 'ble'),
                name: s.callsign || s.name || s.ssid || ('DEV-' + (s.mac || i)),
                mac: s.mac || ('00:00:00:00:00:' + String(i).padStart(2, '0')),
                vendor: s.vendor || '--',
                rssi: (typeof s.rssi === 'number') ? s.rssi : -100,
                age: (typeof s.age === 'number') ? s.age : 999,
                alert: !!s.alert,
                you: !!s.you,
            };
        }).sort(function (a, b) { return b.rssi - a.rssi; });

        if (devEl) devEl.textContent = devices.length || '--';
        if (newEl) newEl.textContent = devices.filter(function (d) { return d.age < 5; }).length || '--';
        if (droneEl) droneEl.textContent = devices.filter(function (d) {
            return /drone|rid|dji/i.test(d.type) || /dji|drone/i.test(d.name);
        }).length || '--';

        // DOM-diff list
        if (devices.length === 0) {
            if (!listEl.querySelector('.cockpit-sdr-empty')) {
                while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
                var empty = document.createElement('div');
                empty.className = 'cockpit-sdr-empty';
                empty.textContent = 'scanning…';
                listEl.appendChild(empty);
            }
            return;
        }
        var emptyEl = listEl.querySelector('.cockpit-sdr-empty');
        if (emptyEl) listEl.removeChild(emptyEl);

        while (listEl.children.length > devices.length) listEl.removeChild(listEl.lastChild);
        while (listEl.children.length < devices.length) {
            var row = document.createElement('div');
            row.className = 'cockpit-sdr-row';
            for (var i = 0; i < 5; i++) row.appendChild(document.createElement('span'));
            row.children[0].className = 'cockpit-sdr-row-type';
            row.children[1].className = 'cockpit-sdr-row-name';
            row.children[2].className = 'cockpit-sdr-row-mac';
            row.children[3].className = 'cockpit-sdr-row-vendor';
            row.children[4].className = 'cockpit-sdr-row-rssi';
            listEl.appendChild(row);
        }
        for (var k = 0; k < devices.length; k++) {
            var d = devices[k];
            var r2 = listEl.children[k];
            if (!r2) continue;
            r2.classList.toggle('is-new', d.age < 3);
            r2.classList.toggle('is-alert', d.alert);
            r2.classList.toggle('is-you', d.you);
            _setText(r2.children[0], String(d.type).split('-')[0].toUpperCase().slice(0, 4));
            _setText(r2.children[1], d.name);
            _setText(r2.children[2], d.mac);
            _setText(r2.children[3], d.vendor);
            _setText(r2.children[4], String(d.rssi));
        }
    }

    function animateSdrSpectrum() {
        var svg = document.getElementById('ops-cockpit-sdr-spectrum');
        if (!svg) return;
        sdrTickValue++;
        // Mock-style: 70 bars, width 2, x stride 2.9, sin(i*0.4 + tick*0.3)
        while (svg.firstChild) svg.removeChild(svg.firstChild);
        for (var i = 0; i < 70; i++) {
            var h = 4 + Math.abs(Math.sin(i * 0.4 + sdrTickValue * 0.3)) * 16
                + ((i % 11 === 0) ? 12 : 0) + Math.random() * 4;
            var bar = document.createElementNS(SVG_NS, 'rect');
            bar.setAttribute('x', (i * 2.9).toFixed(1));
            bar.setAttribute('y', (34 - h).toFixed(1));
            bar.setAttribute('width', '2');
            bar.setAttribute('height', h.toFixed(1));
            bar.setAttribute('fill', 'var(--olive-primary)');
            bar.setAttribute('opacity', (0.5 + (h / 34) * 0.5).toFixed(2));
            svg.appendChild(bar);
        }
    }

    // No-arg variant for the public surface — consumers call this each tick
    // and it pulls from cached HydraApp state without requiring a fresh
    // /api/* round-trip. The poller in refreshAuxZones handles the network.
    function updateCockpitStrip() {
        // Title pill mirrors current callsign each tick (cheap; no fetch)
        var titleEl = document.getElementById('ops-cockpit-tak-title');
        var stats = HydraApp.state.stats || {};
        if (titleEl) {
            var cs = stats.callsign || 'HYDRA-1';
            var want = 'TAK \u00B7 ' + cs;
            if (titleEl.textContent !== want) titleEl.textContent = want;
        }
        // Servo dial: refresh just the readout numbers from cached state
        // when a target is locked (mirror UI feedback without server poll)
        var target = HydraApp.state.target || {};
        var pillEl = document.getElementById('ops-cockpit-servo-pill');
        if (pillEl && target.locked) {
            var strike = target.approach_mode === 'strike';
            pillEl.textContent = strike ? 'STRIKE' : 'TRACK';
        }
    }

    // ── HUD layout picker ──
    function applyHudLayout(layout) {
        var validLayouts = ['classic', 'operator', 'graphs', 'hybrid'];
        if (validLayouts.indexOf(layout) === -1) layout = 'classic';
        var rail = document.getElementById('ops-flight-hud');
        if (rail) rail.setAttribute('data-hud-layout', layout);
        var picker = document.getElementById('flight-hud-layout-picker');
        if (picker && picker.value !== layout) picker.value = layout;
    }

    function loadHudLayoutFromConfig() {
        if (hudLayoutLoaded) return;
        hudLayoutLoaded = true;
        if (!HydraApp || typeof HydraApp.apiGet !== 'function') {
            applyHudLayout('classic');
            return;
        }
        HydraApp.apiGet('/api/config/full').then(function (cfg) {
            var layout = (cfg && cfg.web && cfg.web.hud_layout) ? cfg.web.hud_layout : 'classic';
            applyHudLayout(layout);
        }).catch(function () { applyHudLayout('classic'); });
    }

    function onHudLayoutPickerChange(e) {
        var layout = e && e.target ? e.target.value : 'classic';
        applyHudLayout(layout);
        if (HydraApp && typeof HydraApp.apiPost === 'function') {
            HydraApp.apiPost('/api/config/full', { web: { hud_layout: layout } }).then(function (r) {
                if (r) HydraApp.showToast('HUD layout: ' + layout, 'info');
            });
        }
    }

    function wireFlightHudPicker() {
        var picker = document.getElementById('flight-hud-layout-picker');
        if (!picker || picker._wired) return;
        picker._wired = true;
        picker.addEventListener('change', onHudLayoutPickerChange);
    }

    // ── Cockpit map expand click ──
    function wireCockpitMapClick() {
        var cell = document.getElementById('ops-cockpit-tak');
        if (!cell || cell._wired) return;
        cell._wired = true;
        cell.addEventListener('click', function () {
            // Future: open full TAK overlay. For now, switch to the TAK view
            // since that's where the full map lives.
            if (window.location.hash !== '#tak') {
                window.location.hash = '#tak';
            }
        });
    }

    return {
        onEnter: onEnter,
        onLeave: onLeave,
        updateTelemetry: updateTelemetry,
        updateLockInfo: updateLockInfo,
        updateApproachPanel: updateApproachPanel,
        abortApproach: abortApproach,
        drawBoundingBoxes: drawBoundingBoxes,
        updateSidebarRF: updateSidebarRF,
        updateSidebarMission: updateSidebarMission,
        updateSidebarPipeline: updateSidebarPipeline,
        updateSidebarDetLog: updateSidebarDetLog,
        loadDetlogFilter: loadDetlogFilter,
        clearDetlogFilter: clearDetlogFilter,
        updateFlightHud: updateFlightHud,
        updateCockpitStrip: updateCockpitStrip,
        applyHudLayout: applyHudLayout,
        setActiveTab: setActiveTab,
        getActiveTab: getActiveTab,
        updateTabCounts: updateTabCounts,
        updateTabMavlink: updateTabMavlink,
        refreshTakTab: refreshTakTab,
        refreshAuditLog: refreshAuditLog,
    };
})();
