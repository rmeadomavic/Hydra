'use strict';

/**
 * Hydra Detect v2.0 — Autonomy View Logic (dashboard reader).
 *
 * Tries /api/autonomy/status at 500 ms. When that endpoint is absent (HTTP
 * 404 / network error), degrades to /api/config/full + /api/stats at 1 s and
 * renders "[awaiting /api/autonomy/status]" placeholders for fields the
 * fallback cannot populate (gates, explainability log, live self-position).
 *
 * Mode change is a POST to /api/autonomy/mode; if the backend is unreachable
 * the picker reverts to the previously confirmed mode and a toast surfaces
 * the connection-lost state. Fail-safe: never silently advance mode.
 */
const HydraAutonomy = (() => {
    const FAST_POLL_MS = 500;          // when /api/autonomy/status is live
    const FALLBACK_POLL_MS = 1000;     // when falling back to config + stats
    const MAX_LOG_ENTRIES = 200;
    const MODE_ORDER = ['dryrun', 'shadow', 'live'];

    let pollTimer = null;
    let inFlight = false;
    let backoffMs = FAST_POLL_MS;
    let statusEndpointLive = true;     // flips to false on first 404
    let currentMode = 'dryrun';        // last confirmed mode (authoritative)
    let pendingMode = null;            // mode being confirmed via modal
    let callsign = 'HYDRA-1';
    let liveLiveStep = 0;              // 0 = not started, 1 = typed CS, 2 = final
    let lastLogKeys = [];
    let logNodes = Object.create(null);

    // ── Lifecycle ──
    function onEnter() {
        currentMode = 'dryrun';
        pendingMode = null;
        liveLiveStep = 0;
        lastLogKeys = [];
        logNodes = Object.create(null);
        clearLogList();
        applyModeUI(currentMode);
        attachModeHandlers();
        attachModalHandlers();
        startPolling();
    }

    function onLeave() {
        stopPolling();
        hideModal();
    }

    // ── Polling ──
    function startPolling() {
        if (pollTimer !== null) return;
        pollOnce();
    }

    function stopPolling() {
        if (pollTimer !== null) {
            clearTimeout(pollTimer);
            pollTimer = null;
        }
        inFlight = false;
    }

    function schedule(nextMs) {
        if (pollTimer !== null) clearTimeout(pollTimer);
        pollTimer = setTimeout(pollOnce, nextMs);
    }

    async function pollOnce() {
        pollTimer = null;
        if (inFlight) {
            schedule(currentPollInterval());
            return;
        }
        if (document.visibilityState === 'hidden') {
            schedule(currentPollInterval());
            return;
        }
        inFlight = true;
        try {
            if (statusEndpointLive) {
                const ok = await pollStatus();
                if (!ok) {
                    statusEndpointLive = false;
                    await pollFallback();
                }
            } else {
                await pollFallback();
            }
            backoffMs = currentPollInterval();
        } catch (err) {
            backoffMs = Math.min(backoffMs * 2, 10000);
        } finally {
            inFlight = false;
        }
        schedule(backoffMs);
    }

    function currentPollInterval() {
        return statusEndpointLive ? FAST_POLL_MS : FALLBACK_POLL_MS;
    }

    async function pollStatus() {
        let resp;
        try {
            resp = await fetch('/api/autonomy/status', { credentials: 'same-origin' });
        } catch (err) {
            return false;
        }
        if (resp.status === 404) return false;
        if (!resp.ok) return false;
        let data;
        try {
            data = await resp.json();
        } catch (err) {
            return false;
        }
        renderStatus(data);
        return true;
    }

    async function pollFallback() {
        let cfg = null;
        let stats = null;
        try {
            const cfgResp = await fetch('/api/config/full', { credentials: 'same-origin' });
            if (cfgResp.ok) cfg = await cfgResp.json();
        } catch (err) { /* swallow — partial render is fine */ }
        try {
            const statsResp = await fetch('/api/stats', { credentials: 'same-origin' });
            if (statsResp.ok) stats = await statsResp.json();
        } catch (err) { /* swallow */ }
        renderFallback(cfg, stats);
    }

    // ── Rendering: status endpoint path ──
    function renderStatus(data) {
        if (data && typeof data.callsign === 'string' && data.callsign) {
            callsign = data.callsign;
            setText('autonomy-geofence-callsign', callsign);
        }
        if (data && typeof data.mode === 'string' && MODE_ORDER.includes(data.mode)) {
            if (currentMode !== data.mode) {
                currentMode = data.mode;
                applyModeUI(currentMode);
            }
        }
        renderEnabledChip(!!(data && data.enabled));
        renderGeofence(data && data.geofence ? data.geofence : null, data && data.self_position);
        renderCriteria(data && data.criteria ? data.criteria : null);
        renderGates(Array.isArray(data && data.gates) ? data.gates : null);
        renderLog(Array.isArray(data && data.log) ? data.log : null);
    }

    // ── Rendering: fallback path (config + stats only) ──
    function renderFallback(cfg, stats) {
        const auto = cfg && cfg.autonomous ? cfg.autonomous : {};
        const web = cfg && cfg.web ? cfg.web : {};
        if (typeof web.callsign === 'string' && web.callsign) {
            callsign = web.callsign;
            setText('autonomy-geofence-callsign', callsign);
        } else if (stats && typeof stats.callsign === 'string' && stats.callsign) {
            callsign = stats.callsign;
            setText('autonomy-geofence-callsign', callsign);
        }
        const enabled = parseBool(auto.enabled);
        renderEnabledChip(enabled);

        const radius = parseFloat(auto.geofence_radius_m);
        const lat = parseFloat(auto.geofence_lat);
        const lon = parseFloat(auto.geofence_lon);
        const polygon = typeof auto.geofence_polygon === 'string' ? auto.geofence_polygon.trim() : '';
        const shape = polygon ? 'POLYGON' : 'CIRCLE';
        renderGeofence({
            shape,
            radius_m: isFinite(radius) ? radius : null,
            center_lat: isFinite(lat) ? lat : null,
            center_lon: isFinite(lon) ? lon : null,
            polygon,
        }, null);

        renderCriteria({
            min_confidence: parseFloat(auto.min_confidence),
            min_track_frames: parseInt(auto.min_track_frames, 10),
            strike_cooldown_sec: parseFloat(auto.strike_cooldown_sec),
            allowed_classes: parseClassList(auto.allowed_classes),
            allowed_vehicle_modes: typeof auto.allowed_vehicle_modes === 'string' ? auto.allowed_vehicle_modes : '',
            gps_max_stale_sec: parseFloat(auto.gps_max_stale_sec),
            require_operator_lock: parseBool(auto.require_operator_lock),
        });
        // Gates + log need runtime state that only /api/autonomy/status provides.
        renderGatesPlaceholder();
        renderLogPlaceholder();
    }

    // ── Enabled banner + chip ──
    function renderEnabledChip(enabled) {
        const chip = document.getElementById('autonomy-enabled-chip');
        const banner = document.getElementById('autonomy-banner');
        const bannerTitle = document.getElementById('autonomy-banner-title');
        if (chip) {
            chip.textContent = enabled ? 'ENABLED' : 'DISABLED';
            chip.classList.toggle('autonomy-enabled-on', !!enabled);
            chip.classList.toggle('autonomy-enabled-off', !enabled);
        }
        if (banner) {
            banner.style.display = enabled ? 'none' : '';
        }
        if (bannerTitle && enabled) {
            bannerTitle.textContent = 'Autonomous strike is disabled';
        }
    }

    // ── Geofence preview ──
    function renderGeofence(g, selfPos) {
        if (!g) {
            setText('autonomy-geofence-meta', '[awaiting /api/autonomy/status]');
            return;
        }
        const shape = (g.shape || 'CIRCLE').toUpperCase();
        setText('autonomy-geofence-shape', shape);
        setText('autonomy-geofence-radius', numberOr(g.radius_m, 'm'));
        setText('autonomy-geofence-lat', isFinite(g.center_lat) ? g.center_lat.toFixed(5) : '--');
        setText('autonomy-geofence-lon', isFinite(g.center_lon) ? g.center_lon.toFixed(5) : '--');
        const meta = [shape];
        if (isFinite(g.center_lat) && isFinite(g.center_lon)) {
            meta.push(g.center_lat.toFixed(4) + ' / ' + g.center_lon.toFixed(4));
        }
        setText('autonomy-geofence-meta', meta.join(' · '));

        const selfDistEl = document.getElementById('autonomy-geofence-self-dist');
        if (selfDistEl) {
            if (selfPos && isFinite(selfPos.distance_m)) {
                selfDistEl.textContent = selfPos.distance_m.toFixed(1) + ' m';
                selfDistEl.classList.remove('autonomy-placeholder');
            } else {
                selfDistEl.textContent = '[awaiting /api/autonomy/status]';
                selfDistEl.classList.add('autonomy-placeholder');
            }
        }

        drawGeofenceShape(g, selfPos);
    }

    function drawGeofenceShape(g, selfPos) {
        const group = document.getElementById('autonomy-geofence-shape-group');
        if (!group) return;
        while (group.firstChild) group.removeChild(group.firstChild);

        const svgNs = 'http://www.w3.org/2000/svg';
        const shape = (g.shape || 'CIRCLE').toUpperCase();
        const cx = 100, cy = 100;

        if (shape === 'POLYGON' && g.polygon) {
            const pts = parsePolygon(g.polygon);
            if (pts.length >= 3) {
                const bbox = polygonBBox(pts);
                const mapped = pts.map(p => mapToViewbox(p, bbox, 10, 190));
                const poly = document.createElementNS(svgNs, 'polygon');
                poly.setAttribute('points', mapped.map(p => p[0] + ',' + p[1]).join(' '));
                poly.setAttribute('class', 'autonomy-fence-poly');
                group.appendChild(poly);
                return;
            }
        }
        // Circle fallback — radius scaled to fit viewbox, capped at 85.
        const radius = isFinite(g.radius_m) ? g.radius_m : 100;
        const r = Math.max(20, Math.min(85, 30 + Math.log10(Math.max(radius, 10)) * 18));
        const circle = document.createElementNS(svgNs, 'circle');
        circle.setAttribute('cx', cx);
        circle.setAttribute('cy', cy);
        circle.setAttribute('r', r);
        circle.setAttribute('class', 'autonomy-fence-circle');
        group.appendChild(circle);
    }

    function parsePolygon(raw) {
        // "lat,lon;lat,lon;..." per config.ini format.
        const pts = [];
        const parts = raw.split(';');
        for (const p of parts) {
            const [a, b] = p.split(',');
            const lat = parseFloat(a);
            const lon = parseFloat(b);
            if (isFinite(lat) && isFinite(lon)) pts.push([lat, lon]);
        }
        return pts;
    }

    function polygonBBox(pts) {
        let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
        for (const [lat, lon] of pts) {
            if (lat < minLat) minLat = lat;
            if (lat > maxLat) maxLat = lat;
            if (lon < minLon) minLon = lon;
            if (lon > maxLon) maxLon = lon;
        }
        return { minLat, maxLat, minLon, maxLon };
    }

    function mapToViewbox(pt, bbox, pad, size) {
        const [lat, lon] = pt;
        const span = Math.max(bbox.maxLat - bbox.minLat, bbox.maxLon - bbox.minLon, 1e-9);
        const x = pad + ((lon - bbox.minLon) / span) * (size - pad);
        // y flipped — higher lat is visually up.
        const y = pad + (1 - (lat - bbox.minLat) / span) * (size - pad);
        return [x.toFixed(2), y.toFixed(2)];
    }

    // ── Qualification criteria ──
    function renderCriteria(c) {
        const list = document.getElementById('autonomy-criteria-list');
        const summary = document.getElementById('autonomy-criteria-summary');
        if (!list) return;
        list.textContent = '';
        if (!c) {
            const li = document.createElement('li');
            li.className = 'autonomy-criteria-empty';
            li.textContent = '[awaiting data]';
            list.appendChild(li);
            if (summary) summary.textContent = '';
            return;
        }
        appendCriteriaRow(list, 'Min confidence', formatFloat(c.min_confidence, 2));
        appendCriteriaRow(list, 'Min track frames', formatInt(c.min_track_frames));
        appendCriteriaRow(list, 'Strike cooldown', formatFloat(c.strike_cooldown_sec, 1, 's'));
        appendCriteriaRow(list, 'GPS max stale', formatFloat(c.gps_max_stale_sec, 1, 's'));
        appendCriteriaRow(list, 'Require operator lock', c.require_operator_lock === true ? 'yes' : (c.require_operator_lock === false ? 'no' : '--'));
        appendCriteriaRow(list, 'Allowed vehicle modes', c.allowed_vehicle_modes || '--');

        const classesRow = document.createElement('li');
        classesRow.className = 'autonomy-criteria-row autonomy-criteria-row-classes';
        const label = document.createElement('span');
        label.className = 'autonomy-criteria-label';
        label.textContent = 'Allowed classes';
        classesRow.appendChild(label);
        const chips = document.createElement('span');
        chips.className = 'autonomy-criteria-chips';
        const classList = Array.isArray(c.allowed_classes) ? c.allowed_classes : [];
        if (classList.length === 0) {
            const chip = document.createElement('span');
            chip.className = 'autonomy-chip autonomy-chip-dim';
            chip.textContent = 'none configured';
            chips.appendChild(chip);
        } else {
            for (const name of classList) {
                const chip = document.createElement('span');
                chip.className = 'autonomy-chip autonomy-chip-live';
                chip.textContent = name;
                chips.appendChild(chip);
            }
        }
        classesRow.appendChild(chips);
        list.appendChild(classesRow);

        if (summary) {
            summary.textContent = classList.length + ' class' + (classList.length === 1 ? '' : 'es');
        }
    }

    function appendCriteriaRow(list, label, value) {
        const li = document.createElement('li');
        li.className = 'autonomy-criteria-row';
        const l = document.createElement('span');
        l.className = 'autonomy-criteria-label';
        l.textContent = label;
        const v = document.createElement('span');
        v.className = 'autonomy-criteria-value';
        v.textContent = value;
        li.appendChild(l);
        li.appendChild(v);
        list.appendChild(li);
    }

    // ── Safety gates ──
    function renderGates(gates) {
        const summary = document.getElementById('autonomy-gates-summary');
        const list = document.getElementById('autonomy-gates-list');
        if (!list) return;
        if (!gates) {
            renderGatesPlaceholder();
            return;
        }
        let pass = 0;
        let fail = 0;
        for (const g of gates) {
            const id = g && g.id;
            const state = g && g.state;
            const row = list.querySelector('[data-gate="' + id + '"]');
            if (!row) continue;
            row.classList.remove('autonomy-gate-pass', 'autonomy-gate-fail', 'autonomy-gate-na');
            const pill = row.querySelector('.autonomy-gate-pill');
            const glyph = row.querySelector('.autonomy-gate-glyph');
            if (pill) pill.classList.remove('autonomy-gate-pill-pass', 'autonomy-gate-pill-fail', 'autonomy-gate-pill-na');
            if (state === 'PASS') {
                pass += 1;
                row.classList.add('autonomy-gate-pass');
                if (pill) { pill.classList.add('autonomy-gate-pill-pass'); pill.textContent = 'PASS'; }
                if (glyph) glyph.textContent = '\u2713';
            } else if (state === 'FAIL') {
                fail += 1;
                row.classList.add('autonomy-gate-fail');
                if (pill) { pill.classList.add('autonomy-gate-pill-fail'); pill.textContent = 'FAIL'; }
                if (glyph) glyph.textContent = '\u2715';
            } else {
                row.classList.add('autonomy-gate-na');
                if (pill) { pill.classList.add('autonomy-gate-pill-na'); pill.textContent = 'N/A'; }
                if (glyph) glyph.textContent = '\u00b7';
            }
            if (g && typeof g.detail === 'string' && g.detail) {
                const sub = row.querySelector('.autonomy-gate-sub');
                if (sub) sub.textContent = g.detail;
            }
        }
        if (summary) {
            if (fail === 0 && pass > 0) {
                summary.textContent = pass + ' of ' + gates.length + ' PASS';
            } else if (fail > 0) {
                summary.textContent = fail + ' FAIL · ' + pass + ' PASS';
            } else {
                summary.textContent = gates.length + ' gates · all N/A';
            }
        }
    }

    function renderGatesPlaceholder() {
        const summary = document.getElementById('autonomy-gates-summary');
        if (summary) summary.textContent = '[awaiting /api/autonomy/status]';
        const list = document.getElementById('autonomy-gates-list');
        if (!list) return;
        list.querySelectorAll('.autonomy-gate').forEach(row => {
            row.classList.remove('autonomy-gate-pass', 'autonomy-gate-fail');
            row.classList.add('autonomy-gate-na');
            const pill = row.querySelector('.autonomy-gate-pill');
            if (pill) {
                pill.classList.remove('autonomy-gate-pill-pass', 'autonomy-gate-pill-fail');
                pill.classList.add('autonomy-gate-pill-na');
                pill.textContent = 'N/A';
            }
            const glyph = row.querySelector('.autonomy-gate-glyph');
            if (glyph) glyph.textContent = '\u00b7';
        });
    }

    // ── Explainability log ──
    function renderLog(entries) {
        const empty = document.getElementById('autonomy-log-empty');
        const list = document.getElementById('autonomy-log-list');
        const count = document.getElementById('autonomy-log-count');
        if (!list) return;
        if (!entries) {
            renderLogPlaceholder();
            return;
        }
        const capped = entries.slice(0, MAX_LOG_ENTRIES);
        if (count) count.textContent = String(capped.length);
        if (capped.length === 0) {
            clearLogList();
            if (empty) {
                empty.style.display = '';
                empty.textContent = 'No autonomy decisions recorded yet.';
            }
            return;
        }
        if (empty) empty.style.display = 'none';

        const newKeys = capped.map(logKey);
        const newSet = new Set(newKeys);
        for (const oldKey of lastLogKeys) {
            if (!newSet.has(oldKey)) {
                const node = logNodes[oldKey];
                if (node && node.parentNode) node.parentNode.removeChild(node);
                delete logNodes[oldKey];
            }
        }
        for (let i = 0; i < capped.length; i++) {
            const key = newKeys[i];
            let node = logNodes[key];
            if (!node) {
                node = buildLogRow(capped[i]);
                logNodes[key] = node;
            }
            const cur = list.children[i];
            if (cur !== node) list.insertBefore(node, cur || null);
        }
        lastLogKeys = newKeys;
    }

    function renderLogPlaceholder() {
        const empty = document.getElementById('autonomy-log-empty');
        const count = document.getElementById('autonomy-log-count');
        clearLogList();
        if (empty) {
            empty.style.display = '';
            empty.textContent = '[awaiting /api/autonomy/status]';
        }
        if (count) count.textContent = '0';
    }

    function logKey(e) {
        const ts = e && (e.ts || e.timestamp) || '';
        const tid = e && (e.track_id != null ? e.track_id : '');
        const action = e && (e.action || e.verdict) || '';
        const reason = e && (e.reason || '');
        return ts + '|' + tid + '|' + action + '|' + reason;
    }

    function buildLogRow(e) {
        const row = document.createElement('li');
        const action = normalizeAction(e && (e.action || e.verdict));
        row.className = 'autonomy-log-row autonomy-log-' + action;

        const head = document.createElement('div');
        head.className = 'autonomy-log-row-head';
        const ts = document.createElement('span');
        ts.className = 'autonomy-log-ts';
        ts.textContent = formatTs(e && (e.ts || e.timestamp));
        const pill = document.createElement('span');
        pill.className = 'autonomy-log-pill autonomy-log-pill-' + action;
        pill.textContent = action.toUpperCase();
        head.appendChild(ts);
        head.appendChild(pill);
        row.appendChild(head);

        if (e && (e.track_id != null || e.label)) {
            const track = document.createElement('div');
            track.className = 'autonomy-log-track';
            const bits = [];
            if (e.track_id != null) bits.push('track #' + e.track_id);
            if (e.label) bits.push(e.label);
            track.textContent = bits.join(' · ');
            row.appendChild(track);
        }

        const reason = document.createElement('div');
        reason.className = 'autonomy-log-reason';
        reason.textContent = '\u2192 ' + (e && e.reason ? e.reason : '(no reason)');
        row.appendChild(reason);

        if (e && e.sha256) {
            const sha = document.createElement('div');
            sha.className = 'autonomy-log-sha';
            const s = String(e.sha256);
            sha.textContent = 'sha256: ' + (s.length > 12 ? s.slice(0, 8) + '\u2026' + s.slice(-4) : s);
            row.appendChild(sha);
        }
        return row;
    }

    function normalizeAction(raw) {
        const a = String(raw || '').toLowerCase();
        if (a === 'engage' || a === 'qual' || a === 'pass') return 'engage';
        if (a === 'reject' || a === 'block' || a === 'fail') return 'reject';
        if (a === 'defer' || a === 'hold' || a === 'wait') return 'defer';
        return 'passthrough';
    }

    function clearLogList() {
        const list = document.getElementById('autonomy-log-list');
        if (list) list.textContent = '';
        lastLogKeys = [];
        logNodes = Object.create(null);
    }

    // ── Mode picker ──
    function attachModeHandlers() {
        const btns = document.querySelectorAll('.autonomy-mode-btn');
        btns.forEach(btn => {
            if (btn.dataset.autonomyBound === '1') return;
            btn.dataset.autonomyBound = '1';
            btn.addEventListener('click', () => {
                const target = btn.dataset.mode;
                if (!MODE_ORDER.includes(target)) return;
                if (target === currentMode) return;
                beginModeChange(target);
            });
        });
    }

    function applyModeUI(mode) {
        const metaMap = {
            dryrun: 'Dry-run logs candidates without engaging',
            shadow: 'Shadow emits STATUSTEXT without servo',
            live: 'LIVE — servo trigger active',
        };
        const btns = document.querySelectorAll('.autonomy-mode-btn');
        btns.forEach(btn => {
            const active = btn.dataset.mode === mode;
            btn.classList.toggle('is-active', active);
            btn.setAttribute('aria-checked', active ? 'true' : 'false');
        });
        const meta = document.getElementById('autonomy-mode-meta');
        if (meta) meta.textContent = metaMap[mode] || '--';
    }

    function beginModeChange(target) {
        pendingMode = target;
        liveLiveStep = 0;
        const modal = document.getElementById('autonomy-mode-modal');
        const titleEl = document.getElementById('autonomy-mode-modal-title');
        const targetEl = document.getElementById('autonomy-mode-modal-target');
        const warningEl = document.getElementById('autonomy-mode-modal-warning');
        const csLabel = document.getElementById('autonomy-mode-modal-cs-label');
        const csTarget = document.getElementById('autonomy-mode-modal-cs-target');
        const csInput = document.getElementById('autonomy-mode-modal-cs-input');
        const csError = document.getElementById('autonomy-mode-modal-cs-error');
        const confirm = document.getElementById('autonomy-mode-modal-confirm');
        if (!modal || !titleEl || !targetEl || !confirm) return;

        if (targetEl) targetEl.textContent = target.toUpperCase();
        if (csError) csError.textContent = '';
        if (csInput) csInput.value = '';

        if (target === 'shadow') {
            titleEl.textContent = 'Confirm mode change';
            if (warningEl) warningEl.textContent = 'Shadow mode emits STATUSTEXT to the GCS but does NOT trigger the servo or MAVLink commit.';
            if (csLabel) csLabel.style.display = 'none';
            confirm.textContent = 'Switch to SHADOW';
            confirm.disabled = false;
            confirm.classList.remove('btn-danger');
        } else if (target === 'live') {
            titleEl.textContent = 'Confirm LIVE autonomous strike';
            if (warningEl) warningEl.textContent = 'LIVE mode fires the servo trigger on qualified targets. Geofence + operator soft-lock + cooldown gates must all pass before each engagement.';
            if (csLabel) csLabel.style.display = '';
            if (csTarget) csTarget.textContent = callsign;
            confirm.textContent = 'Continue';
            confirm.disabled = true;
            confirm.classList.add('btn-danger');
            liveLiveStep = 1;
        } else { // dryrun
            titleEl.textContent = 'Confirm mode change';
            if (warningEl) warningEl.textContent = 'Dry-run evaluates candidates but does not engage.';
            if (csLabel) csLabel.style.display = 'none';
            confirm.textContent = 'Switch to DRY RUN';
            confirm.disabled = false;
            confirm.classList.remove('btn-danger');
        }
        showModal();
        if (target === 'live' && csInput) {
            // focus input on next tick for screen reader announce
            setTimeout(() => { try { csInput.focus(); } catch (_) {} }, 30);
        }
    }

    function attachModalHandlers() {
        const cancel = document.getElementById('autonomy-mode-modal-cancel');
        const confirm = document.getElementById('autonomy-mode-modal-confirm');
        const csInput = document.getElementById('autonomy-mode-modal-cs-input');

        if (cancel && cancel.dataset.autonomyBound !== '1') {
            cancel.dataset.autonomyBound = '1';
            cancel.addEventListener('click', () => {
                pendingMode = null;
                liveLiveStep = 0;
                hideModal();
                applyModeUI(currentMode);
            });
        }

        if (confirm && confirm.dataset.autonomyBound !== '1') {
            confirm.dataset.autonomyBound = '1';
            confirm.addEventListener('click', () => {
                if (!pendingMode) { hideModal(); return; }
                if (pendingMode === 'live' && liveLiveStep === 1) {
                    // Move to second step — final confirm.
                    const titleEl = document.getElementById('autonomy-mode-modal-title');
                    const warningEl = document.getElementById('autonomy-mode-modal-warning');
                    const csLabel = document.getElementById('autonomy-mode-modal-cs-label');
                    if (titleEl) titleEl.textContent = 'FINAL: arm LIVE mode?';
                    if (warningEl) warningEl.textContent = 'Callsign verified. This arms autonomous engagement. Proceed only if operator is on-station and all safety gates are reviewed.';
                    if (csLabel) csLabel.style.display = 'none';
                    confirm.textContent = 'ARM LIVE';
                    confirm.disabled = false;
                    liveLiveStep = 2;
                    return;
                }
                submitModeChange(pendingMode);
            });
        }

        if (csInput && csInput.dataset.autonomyBound !== '1') {
            csInput.dataset.autonomyBound = '1';
            csInput.addEventListener('input', () => {
                const confirmBtn = document.getElementById('autonomy-mode-modal-confirm');
                const csError = document.getElementById('autonomy-mode-modal-cs-error');
                if (!confirmBtn) return;
                if (pendingMode !== 'live' || liveLiveStep !== 1) return;
                const match = csInput.value.trim() === callsign;
                confirmBtn.disabled = !match;
                if (csError) csError.textContent = match || csInput.value === '' ? '' : 'Callsign does not match';
            });
        }
    }

    async function submitModeChange(target) {
        const confirmBtn = document.getElementById('autonomy-mode-modal-confirm');
        if (confirmBtn) confirmBtn.disabled = true;
        let ok = false;
        try {
            const resp = await fetch('/api/autonomy/mode', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: target }),
            });
            ok = resp.ok;
            if (!ok && resp.status === 404) {
                // Endpoint absent — fail-safe. Do not advance mode.
                ok = false;
            }
        } catch (err) {
            ok = false;
        }
        if (ok) {
            currentMode = target;
            applyModeUI(currentMode);
            hideModal();
            toast('Mode changed to ' + target.toUpperCase(), 'success');
        } else {
            pendingMode = null;
            liveLiveStep = 0;
            hideModal();
            applyModeUI(currentMode);
            toast('CONNECTION LOST — mode retained: ' + currentMode.toUpperCase(), 'error');
        }
        pendingMode = null;
    }

    function showModal() {
        const modal = document.getElementById('autonomy-mode-modal');
        if (modal) modal.classList.add('is-active');
    }

    function hideModal() {
        const modal = document.getElementById('autonomy-mode-modal');
        if (modal) modal.classList.remove('is-active');
        liveLiveStep = 0;
    }

    // ── Helpers ──
    function setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    function parseBool(v) {
        if (typeof v === 'boolean') return v;
        if (typeof v === 'number') return v !== 0;
        if (typeof v === 'string') return ['true', 'yes', '1', 'on'].includes(v.trim().toLowerCase());
        return false;
    }

    function parseClassList(raw) {
        if (Array.isArray(raw)) return raw.filter(x => typeof x === 'string' && x.trim()).map(x => x.trim());
        if (typeof raw === 'string') {
            return raw.split(',').map(s => s.trim()).filter(Boolean);
        }
        return [];
    }

    function numberOr(v, unit) {
        if (!isFinite(v)) return '--';
        return (Math.round(v * 100) / 100) + (unit ? ' ' + unit : '');
    }

    function formatFloat(v, digits, unit) {
        if (!isFinite(v)) return '--';
        const s = Number(v).toFixed(digits);
        return unit ? s + ' ' + unit : s;
    }

    function formatInt(v) {
        const n = parseInt(v, 10);
        return isFinite(n) ? String(n) : '--';
    }

    function formatTs(ts) {
        if (typeof ts === 'string') {
            // "HH:MM:SS" passthrough if it already matches
            if (/^\d{2}:\d{2}:\d{2}/.test(ts)) return ts.slice(0, 8);
            const parsed = Date.parse(ts);
            if (!isNaN(parsed)) return tsToHMS(parsed / 1000);
            return ts;
        }
        if (typeof ts === 'number' && isFinite(ts)) return tsToHMS(ts);
        return '--:--:--';
    }

    function tsToHMS(sec) {
        const d = new Date(sec * 1000);
        const pad = (n) => (n < 10 ? '0' + n : '' + n);
        return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }

    function toast(msg, kind) {
        if (typeof window.showToast === 'function') {
            window.showToast(msg, kind || 'info');
            return;
        }
        if (window.HydraToast && typeof window.HydraToast.show === 'function') {
            window.HydraToast.show(msg, kind || 'info');
            return;
        }
        // Last-resort: console. Never silently swallow safety-critical messages.
        try { console.warn('[autonomy]', msg); } catch (_) {}
    }

    return { onEnter, onLeave };
})();

if (typeof window !== 'undefined') {
    window.HydraAutonomy = HydraAutonomy;
}
