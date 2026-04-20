'use strict';

/**
 * Hydra Detect v2.0 — Systems View Logic
 *
 * Port of design_reference SystemsView (other-views.jsx:667-794).
 * Polls /api/stats at 1 Hz, keeps a 60-sample ring buffer per metric,
 * and DOM-diff updates the four sparkline cards (FPS, CPU, GPU, RAM)
 * plus the subsystems matrix and pre-flight checklist.
 *
 * Threshold bands are baked in (mock-aligned data-driven cutoffs):
 *   FPS: >25 ok, 15-25 deg, <15 crit
 *   CPU: <60 ok, 60-75 deg, >75 crit
 *   GPU: <70 ok, 70-80 deg, >80 crit
 *   RAM: <70 ok, 70-85 deg, >85 crit
 *
 * Lifecycle: HydraSystems.onEnter() / onLeave() — same shape as HydraTak,
 * so the later main.js dispatch is a one-line wire-up.
 */
const HydraSystems = (() => {
    const POLL_INTERVAL_MS = 1000;
    const RING_SIZE = 60;
    const SPARK_W = 280;
    const SPARK_H = 44;

    // Threshold tables. Lower bound is treated as ok ceiling; upper as deg ceiling.
    // For "higher is better" (fps), invert: fps >= ok wins.
    const THRESHOLDS = {
        fps: { higherIsBetter: true,  ok: 25, deg: 15 },
        cpu: { higherIsBetter: false, ok: 60, deg: 75 },
        gpu: { higherIsBetter: false, ok: 70, deg: 80 },
        ram: { higherIsBetter: false, ok: 70, deg: 85 },
    };

    // Stable per-card colors (reflect token palette via CSS class swap).
    const TONE_VAR = {
        ok:   'var(--olive-muted)',
        deg:  'var(--warning)',
        crit: 'var(--danger)',
        dim:  'var(--text-dim)',
    };

    // Persistent per-metric ring buffers across re-enters in same page load.
    const history = {
        fps: [], cpu: [], gpu: [], ram: [],
    };

    let pollTimer = null;
    let inFlight = false;
    let backoffMs = POLL_INTERVAL_MS;
    let tickCount = 0;

    // Cache subsystem-row pill text per row to skip DOM writes when unchanged.
    const lastRow = Object.create(null);
    const lastCheck = Object.create(null);

    // ── Lifecycle ──
    function onEnter() {
        // Don't wipe history — keeps sparklines populated across view switches.
        startPolling();
    }

    function onLeave() {
        stopPolling();
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
            schedule(POLL_INTERVAL_MS);
            return;
        }
        if (document.visibilityState === 'hidden') {
            schedule(POLL_INTERVAL_MS);
            return;
        }
        inFlight = true;
        try {
            const resp = await fetch('/api/stats', { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            tickCount += 1;
            applyStats(data || {});
            backoffMs = POLL_INTERVAL_MS;
        } catch (err) {
            backoffMs = Math.min(backoffMs * 2, 10000);
            applyError();
        } finally {
            inFlight = false;
        }
        schedule(backoffMs);
    }

    // ── Application of stats to DOM ──
    function applyStats(s) {
        const fps = numOrNull(s.fps);
        const cpu = numOrNull(s.cpu_temp_c);
        const gpu = numOrNull(s.gpu_temp_c);
        const ramUsed = numOrNull(s.ram_used_mb);
        const ramTotal = numOrNull(s.ram_total_mb);
        const ramPct = (ramUsed != null && ramTotal != null && ramTotal > 0)
            ? (ramUsed / ramTotal) * 100
            : null;

        pushSample('fps', fps);
        pushSample('cpu', cpu);
        pushSample('gpu', gpu);
        pushSample('ram', ramPct);

        renderMetric('fps', fps, formatFps);
        renderMetric('cpu', cpu, formatTemp);
        renderMetric('gpu', gpu, formatTemp);
        renderMetric('ram', ramPct, formatPct);

        updateRamSub(ramUsed, ramTotal);

        renderSubsystems(s);
        renderPreflight(s, ramPct);
        renderStatusStrip(s, fps);
    }

    function applyError() {
        // Mark status strip as degraded, leave history alone.
        const chip = document.getElementById('systems-status-chip');
        const txt  = document.getElementById('systems-status-text');
        if (chip) {
            chip.textContent = 'NO DATA';
            chip.className = 'systems-status-chip systems-pill systems-pill-warn';
        }
        if (txt) txt.textContent = '/api/stats unreachable — backing off ' + (backoffMs / 1000).toFixed(0) + 's';
    }

    function pushSample(key, value) {
        const ring = history[key];
        ring.push(value == null ? null : Number(value));
        if (ring.length > RING_SIZE) ring.shift();
    }

    // ── Metric card renderer (big number, unit, pill, sparkline) ──
    function renderMetric(key, value, formatter) {
        const root = document.getElementById('systems-card-' + key);
        if (!root) return;

        const valEl = document.getElementById('systems-' + key + '-value');
        const pillEl = document.getElementById('systems-' + key + '-pill');
        const lineEl = root.querySelector('.systems-spark-line');
        const fillEl = root.querySelector('.systems-spark-fill');
        const bandsG = root.querySelector('.systems-spark-bands');

        const tone = classifyTone(key, value);
        const toneClass = 'systems-tone-' + tone;

        // Card border / accent color follows tone via CSS color cascade.
        if (root.dataset.tone !== tone) {
            root.classList.remove('systems-tone-ok', 'systems-tone-deg', 'systems-tone-crit', 'systems-tone-dim');
            root.classList.add(toneClass);
            root.dataset.tone = tone;
        }

        // Big number (only write if changed to avoid layout thrash on identical polls)
        const display = (value == null || !isFinite(value)) ? '--' : formatter(value);
        if (valEl && valEl.textContent !== display) {
            valEl.textContent = display;
            valEl.style.color = (value == null) ? TONE_VAR.dim : TONE_VAR[tone];
        }

        // Threshold pill text + class
        if (pillEl) {
            const pillText = pillTextFor(tone, value);
            const pillClass = 'systems-pill systems-pill-' + tone;
            if (pillEl.textContent !== pillText) pillEl.textContent = pillText;
            if (pillEl.className !== pillClass) pillEl.className = pillClass;
        }

        // Sparkline geometry — draw in viewBox space (0..SPARK_W, 0..SPARK_H)
        const ring = history[key];
        const numeric = ring.filter(v => v != null && isFinite(v));
        if (lineEl && fillEl) {
            if (numeric.length < 2) {
                lineEl.setAttribute('d', '');
                fillEl.setAttribute('d', '');
            } else {
                const min = Math.min(...numeric);
                const max = Math.max(...numeric);
                const range = (max - min) || 1;
                // Use the FULL ring length for x positioning so gaps where data
                // was null still consume horizontal space (truthful trend line).
                const n = ring.length;
                const denom = (n - 1) || 1;
                const pts = [];
                for (let i = 0; i < n; i++) {
                    const v = ring[i];
                    if (v == null || !isFinite(v)) continue;
                    const x = (i / denom) * SPARK_W;
                    const y = SPARK_H - ((v - min) / range) * (SPARK_H - 2) - 1;
                    pts.push([x, y]);
                }
                if (pts.length < 2) {
                    lineEl.setAttribute('d', '');
                    fillEl.setAttribute('d', '');
                } else {
                    const d = 'M ' + pts.map(p => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' L ');
                    const area = d
                        + ' L ' + pts[pts.length - 1][0].toFixed(1) + ',' + SPARK_H
                        + ' L ' + pts[0][0].toFixed(1) + ',' + SPARK_H + ' Z';
                    lineEl.setAttribute('d', d);
                    fillEl.setAttribute('d', area);
                }
            }
        }

        // Threshold bands (drawn once per metric — cheap so just diff by attr count)
        if (bandsG && bandsG.childElementCount === 0) {
            drawBands(bandsG, key);
        }
    }

    function drawBands(g, key) {
        const t = THRESHOLDS[key];
        if (!t) return;
        // Bands are full-width horizontal stripes at the threshold y-positions.
        // Since min/max scale with data, we draw thin guideline bars at the
        // top and bottom of the box rather than precise threshold positions —
        // the *value color* already encodes the tone semantically.
        const ns = 'http://www.w3.org/2000/svg';
        const top = document.createElementNS(ns, 'rect');
        top.setAttribute('x', '0');
        top.setAttribute('y', '0');
        top.setAttribute('width', String(SPARK_W));
        top.setAttribute('height', '1');
        top.setAttribute('fill', t.higherIsBetter ? TONE_VAR.ok : TONE_VAR.crit);
        top.setAttribute('opacity', '0.18');
        const bot = document.createElementNS(ns, 'rect');
        bot.setAttribute('x', '0');
        bot.setAttribute('y', String(SPARK_H - 1));
        bot.setAttribute('width', String(SPARK_W));
        bot.setAttribute('height', '1');
        bot.setAttribute('fill', t.higherIsBetter ? TONE_VAR.crit : TONE_VAR.ok);
        bot.setAttribute('opacity', '0.18');
        g.appendChild(top);
        g.appendChild(bot);
    }

    function classifyTone(key, value) {
        if (value == null || !isFinite(value)) return 'dim';
        const t = THRESHOLDS[key];
        if (!t) return 'dim';
        if (t.higherIsBetter) {
            if (value >= t.ok) return 'ok';
            if (value >= t.deg) return 'deg';
            return 'crit';
        }
        if (value < t.ok) return 'ok';
        if (value < t.deg) return 'deg';
        return 'crit';
    }

    function pillTextFor(tone, value) {
        if (value == null) return '--';
        if (tone === 'ok') return 'OK';
        if (tone === 'deg') return 'DEG';
        if (tone === 'crit') return 'CRIT';
        return '--';
    }

    function updateRamSub(used, total) {
        const sub = document.getElementById('systems-ram-sub');
        if (!sub) return;
        if (used == null || total == null || total <= 0) {
            sub.textContent = '-- / -- GB shared';
            sub.style.color = 'var(--text-dim)';
            return;
        }
        const u = (used / 1024).toFixed(1);
        const t = (total / 1024).toFixed(1);
        const next = u + ' / ' + t + ' GB shared';
        if (sub.textContent !== next) sub.textContent = next;
    }

    // ── Subsystems matrix ──
    function renderSubsystems(s) {
        const rows = buildRowSpec(s);
        let okCount = 0;
        let total = 0;
        for (const r of rows) {
            applyRow(r);
            total += 1;
            if (r.state === 'ok') okCount += 1;
        }
        const meta = document.getElementById('systems-subsystems-meta');
        if (meta) {
            const next = okCount + ' / ' + total + ' ok';
            if (meta.textContent !== next) meta.textContent = next;
        }
    }

    function buildRowSpec(s) {
        const fps = numOrNull(s.fps);
        const inferenceMs = numOrNull(s.inference_ms);
        const detector = (typeof s.detector === 'string' && s.detector) ? s.detector : null;
        const mavlinkUp = !!s.mavlink;
        const gpsFix = numOrNull(s.gps_fix);
        const isSimGps = !!s.is_sim_gps;
        const rtspClients = numOrNull(s.rtsp_clients);
        const mavVideoFps = numOrNull(s.mavlink_video_fps);
        const mavVideoKbps = numOrNull(s.mavlink_video_kbps);
        const approach = s.approach || null;
        const rfHunt = s.rf_hunt || null;

        const rows = [];

        // Detection pipeline — ok if FPS > 0
        rows.push({
            key: 'pipeline',
            detail: (fps != null)
                ? fps.toFixed(1) + ' fps · ' + (inferenceMs != null ? inferenceMs.toFixed(0) + ' ms inf' : '-- ms inf')
                : '--',
            state: (fps != null && fps > 0) ? 'ok' : (fps === 0 ? 'crit' : 'dim'),
        });

        // MAVLink link
        rows.push({
            key: 'mavlink',
            detail: mavlinkUp ? 'connected' : 'not connected',
            state: mavlinkUp ? 'ok' : 'dim',
        });

        // GPS
        let gpsState = 'dim';
        let gpsDetail = '--';
        if (gpsFix != null) {
            if (gpsFix >= 3) { gpsState = 'ok';   gpsDetail = '3D fix' + (isSimGps ? ' · SIM' : ''); }
            else if (gpsFix === 2) { gpsState = 'deg'; gpsDetail = '2D fix' + (isSimGps ? ' · SIM' : ''); }
            else { gpsState = 'crit'; gpsDetail = 'no fix' + (isSimGps ? ' · SIM' : ''); }
        }
        rows.push({ key: 'gps', detail: gpsDetail, state: gpsState });

        // RTSP server
        rows.push({
            key: 'rtsp',
            detail: (rtspClients != null) ? (rtspClients + ' client' + (rtspClients === 1 ? '' : 's')) : 'idle',
            state: (rtspClients != null && rtspClients > 0) ? 'ok' : (rtspClients === 0 ? 'dim' : 'dim'),
        });

        // MAVLink video
        let mvDetail = 'disabled';
        let mvState = 'dim';
        if (mavVideoFps != null && mavVideoFps > 0) {
            mvDetail = mavVideoFps.toFixed(1) + ' fps · ' + (mavVideoKbps != null ? mavVideoKbps.toFixed(0) + ' kbps' : '-- kbps');
            mvState = 'ok';
        }
        rows.push({ key: 'mavvideo', detail: mvDetail, state: mvState });

        // Detector
        rows.push({
            key: 'detector',
            detail: detector ? detector : 'n/a',
            state: detector ? 'ok' : 'dim',
        });

        // Approach controller
        const approachMode = approach && (approach.mode || approach.state);
        rows.push({
            key: 'approach',
            detail: approachMode ? String(approachMode) : 'idle',
            state: approachMode && approachMode !== 'idle' && approachMode !== 'IDLE' ? 'ok' : 'dim',
        });

        // RF hunt
        const rfState = rfHunt && (rfHunt.state || rfHunt.mode);
        rows.push({
            key: 'rfhunt',
            detail: rfState ? String(rfState) : 'idle',
            state: rfState && rfState !== 'idle' && rfState !== 'IDLE' ? 'ok' : 'dim',
        });

        return rows;
    }

    function applyRow(r) {
        const cached = lastRow[r.key];
        const sig = r.detail + '|' + r.state;
        if (cached === sig) return;
        lastRow[r.key] = sig;

        const dot   = document.getElementById('systems-row-' + r.key + '-dot');
        const det   = document.getElementById('systems-row-' + r.key + '-detail');
        const pill  = document.getElementById('systems-row-' + r.key + '-pill');

        if (dot) dot.className = 'systems-dot systems-dot-' + r.state;
        if (det) det.textContent = r.detail;
        if (pill) {
            pill.textContent = (r.state === 'dim') ? '--' : r.state.toUpperCase();
            pill.className = 'systems-pill systems-pill-' + r.state;
        }
    }

    // ── Pre-flight checklist (derived from /api/stats — partial scope) ──
    function renderPreflight(s, ramPct) {
        const fps = numOrNull(s.fps);
        const inferenceMs = numOrNull(s.inference_ms);
        const detector = s.detector;
        const mavlinkUp = !!s.mavlink;
        const gpsFix = numOrNull(s.gps_fix);
        const cpu = numOrNull(s.cpu_temp_c);
        const gpu = numOrNull(s.gpu_temp_c);

        const checks = [
            { key: 'camera',   pass: (fps != null && fps > 0),
              sub: (fps != null) ? (fps > 0 ? 'frames flowing' : 'no frames') : 'no data' },
            { key: 'model',    pass: !!detector,
              sub: detector ? String(detector) : 'not loaded' },
            { key: 'mavlink',  pass: mavlinkUp,
              sub: mavlinkUp ? 'heartbeat ok' : 'no heartbeat' },
            { key: 'gps',      pass: (gpsFix != null && gpsFix >= 3),
              warn: (gpsFix === 2),
              sub: gpsFix == null ? 'no data' : (gpsFix >= 3 ? '3D fix' : (gpsFix === 2 ? '2D · degraded' : 'no fix')) },
            { key: 'ram',      pass: (ramPct != null && ramPct < 85),
              warn: (ramPct != null && ramPct >= 85 && ramPct < 95),
              sub: ramPct != null ? ramPct.toFixed(0) + '% used' : 'no data' },
            { key: 'thermal',  pass: (cpu != null && gpu != null && cpu < 75 && gpu < 80),
              warn: (cpu != null && gpu != null && (cpu >= 75 || gpu >= 80) && cpu < 85 && gpu < 90),
              sub: (cpu != null && gpu != null) ? ('CPU ' + cpu.toFixed(0) + '°C · GPU ' + gpu.toFixed(0) + '°C') : 'no data' },
            { key: 'pipeline', pass: (fps != null && fps > 5),
              warn: (fps != null && fps > 0 && fps <= 5),
              sub: fps != null ? (fps.toFixed(1) + ' fps' + (inferenceMs != null ? ' · ' + inferenceMs.toFixed(0) + ' ms' : '')) : 'no data' },
        ];

        let warnCount = 0;
        let failCount = 0;
        for (const c of checks) {
            const tone = c.pass ? 'pass' : (c.warn ? 'warn' : 'fail');
            applyCheck(c.key, tone, c.sub);
            if (tone === 'warn') warnCount += 1;
            if (tone === 'fail') failCount += 1;
        }
        const summary = document.getElementById('systems-preflight-summary');
        if (summary) {
            let txt, cls;
            if (failCount > 0) { txt = failCount + ' FAIL'; cls = 'systems-pill systems-pill-crit'; }
            else if (warnCount > 0) { txt = warnCount + ' WARN'; cls = 'systems-pill systems-pill-deg'; }
            else { txt = 'ALL OK'; cls = 'systems-pill systems-pill-ok'; }
            if (summary.textContent !== txt) summary.textContent = txt;
            if (summary.className !== cls) summary.className = cls;
        }
    }

    function applyCheck(key, tone, sub) {
        const sig = tone + '|' + sub;
        if (lastCheck[key] === sig) return;
        lastCheck[key] = sig;
        const glyph = document.getElementById('systems-check-' + key + '-glyph');
        const subEl = document.getElementById('systems-check-' + key + '-sub');
        if (glyph) {
            const g = (tone === 'pass') ? '\u2713' : (tone === 'warn') ? '\u26A0' : '\u2715';
            glyph.textContent = g;
            glyph.className = 'systems-check-glyph systems-tone-' + (tone === 'pass' ? 'ok' : tone === 'warn' ? 'deg' : 'crit');
        }
        if (subEl) {
            subEl.textContent = sub;
        }
    }

    // ── Status strip ──
    function renderStatusStrip(s, fps) {
        const chip = document.getElementById('systems-status-chip');
        const txt  = document.getElementById('systems-status-text');
        const meta = document.getElementById('systems-status-meta');
        if (!chip || !txt) return;

        let chipText, chipClass, body;
        if (fps == null) {
            chipText = 'IDLE'; chipClass = 'systems-status-chip systems-pill systems-pill-dim';
            body = 'No frames yet — pipeline may still be warming up';
        } else if (fps > 5) {
            chipText = 'LIVE'; chipClass = 'systems-status-chip systems-pill systems-pill-ok';
            body = 'Detection loop nominal';
        } else if (fps > 0) {
            chipText = 'DEG';  chipClass = 'systems-status-chip systems-pill systems-pill-deg';
            body = 'Detection loop running below 5 FPS';
        } else {
            chipText = 'STALL'; chipClass = 'systems-status-chip systems-pill systems-pill-crit';
            body = 'No frames being processed';
        }

        if (chip.textContent !== chipText) chip.textContent = chipText;
        if (chip.className !== chipClass) chip.className = chipClass;
        if (txt.textContent !== body) txt.textContent = body;
        if (meta) {
            const next = 'tick ' + tickCount;
            if (meta.textContent !== next) meta.textContent = next;
        }
    }

    // ── Helpers ──
    function numOrNull(v) {
        if (v == null) return null;
        const n = Number(v);
        return isFinite(n) ? n : null;
    }

    function formatFps(v) { return v.toFixed(1); }
    function formatTemp(v) { return v.toFixed(1); }
    function formatPct(v)  { return v.toFixed(0); }

    return { onEnter, onLeave };
})();

if (typeof window !== 'undefined') {
    window.HydraSystems = HydraSystems;
}
