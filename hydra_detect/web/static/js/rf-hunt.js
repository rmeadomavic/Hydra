'use strict';

/**
 * Hydra Detect — RF Hunt dashboard module
 *
 * Owns: Kismet device feed table, state-transition timeline, ambient spectrum
 * bars, replay-mode pill, converged flash, RSSI time-series chart (config view),
 * and the target-set confirm modal.
 *
 * Polls /api/rf/devices, /api/rf/events, /api/rf/status, /api/rf/ambient_scan,
 * /api/rf/rssi_history. Polling pauses when the RF tab is not active to keep
 * Jetson CPU free for the detection loop.
 *
 * CSP-safe: no inline scripts, no inline event handlers, no data: URIs.
 * Audio uses Web Audio API with a user-gesture-deferred AudioContext.
 */
const HydraRfHunt = (() => {
    // Poll cadence — 1 Hz feels live without beating up the Jetson.
    const POLL_INTERVAL_MS = 1000;
    const RSSI_HIST_MAX = 300;

    let pollTimer = null;
    let opsActive = false;
    let rfTabActive = false;
    let configActive = false;

    // Pending target-set confirmation — {bssid?, freq_mhz?, mode, label}.
    let pendingTarget = null;
    let lastState = null;
    let lastFlashMs = 0;
    let audioCtx = null;

    // Local RSSI history — fed from /api/rf/rssi_history.
    let rssiHistory = [];
    let rssiThreshold = null;
    let rssiConverge = null;

    // ── Lifecycle ─────────────────────────────────────────────────────

    function onOpsEnter() {
        opsActive = true;
        wireTargetModal();
        ensurePolling();
    }

    function onOpsLeave() {
        opsActive = false;
        ensurePolling();
    }

    function onConfigEnter() {
        configActive = true;
        wireTargetModal();
        ensurePolling();
    }

    function onConfigLeave() {
        configActive = false;
        ensurePolling();
    }

    /** Called by ops.js when the RF tab becomes visible / hidden. */
    function setRfTabActive(active) {
        rfTabActive = !!active;
        ensurePolling();
        if (rfTabActive) pollOnce();
    }

    function ensurePolling() {
        // Active whenever the RF tab is showing OR we're on the config view
        // (config view has its own RSSI chart that needs the same data).
        const shouldPoll = (opsActive && rfTabActive) || configActive;
        if (shouldPoll && !pollTimer) {
            pollTimer = setInterval(pollOnce, POLL_INTERVAL_MS);
            pollOnce();
        } else if (!shouldPoll && pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    // ── Data fetch ────────────────────────────────────────────────────

    async function pollOnce() {
        try {
            const [status, devices, events, history] = await Promise.all([
                fetchJSON('/api/rf/status'),
                fetchJSON('/api/rf/devices'),
                fetchJSON('/api/rf/events'),
                fetchJSON('/api/rf/rssi_history'),
            ]);
            renderSourcePill(status, devices);
            renderDevices(devices);
            renderEvents(events);
            if (status && typeof status === 'object') {
                setRssiThresholds(
                    status.rssi_threshold,
                    status.rssi_converge,
                );
                if (typeof status.converge_flash_ms === 'number') {
                    setConvergeFlashMs(status.converge_flash_ms);
                }
            }
            renderRssiChart(history);
            if (window.HydraRfMap) {
                try {
                    window.HydraRfMap.setSamples(history);
                    window.HydraRfMap.setStatus(status);
                } catch (_err) { /* map not attached yet */ }
            }
            maybeFlashOnConverge(status);
        } catch (_err) {
            // Swallow — the dashboard handles partial data gracefully.
        }
        // Ambient scan on its own 2 Hz cadence (small payload).
        try {
            const amb = await fetchJSON('/api/rf/ambient_scan');
            renderAmbient(amb);
        } catch (_err) {
            // no-op
        }
    }

    function fetchJSON(url) {
        return fetch(url, { credentials: 'same-origin' }).then((r) => {
            if (!r.ok) throw new Error(url + ' ' + r.status);
            return r.json();
        });
    }

    // ── Rendering: source pill (LIVE / REPLAY) ────────────────────────

    function renderSourcePill(status, devices) {
        const pill = document.getElementById('ops-rf-source-pill');
        if (!pill) return;
        const mode = (devices && devices.mode) || (status && status.source) || null;
        if (!mode || mode === 'unavailable' || mode === 'none') {
            pill.hidden = true;
            return;
        }
        pill.hidden = false;
        pill.textContent = mode === 'replay' ? 'REPLAY' : 'LIVE';
        pill.setAttribute('data-mode', mode);
    }

    // ── Rendering: device feed ────────────────────────────────────────

    function renderDevices(payload) {
        const section = document.getElementById('ops-rf-devices-section');
        const list = document.getElementById('ops-rf-device-list');
        const count = document.getElementById('ops-rf-devices-count');
        if (!list || !section) return;
        const devices = (payload && Array.isArray(payload.devices))
            ? payload.devices
            : [];
        if (count) count.textContent = String(devices.length);
        if (devices.length === 0) {
            section.setAttribute('hidden', 'hidden');
            while (list.firstChild) list.removeChild(list.firstChild);
            return;
        }
        section.removeAttribute('hidden');

        // Diff-by-bssid so click handlers stay bound and scroll position
        // doesn't reset on every poll.
        const existing = {};
        for (const row of list.querySelectorAll('.rf-device-row')) {
            existing[row.dataset.bssid] = row;
        }
        const keep = new Set();
        for (let i = 0; i < devices.length; i++) {
            const dev = devices[i];
            const key = dev.bssid || ('row-' + i);
            keep.add(key);
            let row = existing[key];
            if (!row) {
                row = buildDeviceRow(dev);
                list.appendChild(row);
            } else {
                updateDeviceRow(row, dev);
            }
        }
        // Remove rows that no longer appear in the feed.
        for (const key in existing) {
            if (!keep.has(key)) existing[key].remove();
        }
    }

    function buildDeviceRow(dev) {
        const row = document.createElement('div');
        row.className = 'rf-device-row';
        row.dataset.bssid = dev.bssid || '';
        row.setAttribute('role', 'listitem');
        row.setAttribute('tabindex', '0');

        // RSSI column: value + signal bar.
        const rssiCol = document.createElement('div');
        rssiCol.className = 'rf-device-rssi';
        const rssiVal = document.createElement('span');
        rssiVal.className = 'rf-device-rssi-value';
        rssiCol.appendChild(rssiVal);
        const bar = document.createElement('div');
        bar.className = 'rf-signal-bar';
        const fill = document.createElement('div');
        fill.className = 'rf-signal-bar-fill';
        bar.appendChild(fill);
        rssiCol.appendChild(bar);

        // Body column: SSID + meta (channel, manuf, freq).
        const body = document.createElement('div');
        body.className = 'rf-device-body';
        const ssid = document.createElement('div');
        ssid.className = 'rf-device-ssid';
        body.appendChild(ssid);
        const meta = document.createElement('div');
        meta.className = 'rf-device-meta';
        body.appendChild(meta);

        // Age column.
        const age = document.createElement('div');
        age.className = 'rf-device-age';

        row.appendChild(rssiCol);
        row.appendChild(body);
        row.appendChild(age);

        row.addEventListener('click', () => openTargetModal(row.dataset));
        row.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                openTargetModal(row.dataset);
            }
        });

        updateDeviceRow(row, dev);
        return row;
    }

    function updateDeviceRow(row, dev) {
        row.dataset.bssid = dev.bssid || '';
        row.dataset.freq = dev.freq_mhz != null ? String(dev.freq_mhz) : '';
        row.dataset.ssid = dev.ssid || '';
        row.classList.toggle('is-target', !!dev.is_target);

        const rssiVal = row.querySelector('.rf-device-rssi-value');
        const fill = row.querySelector('.rf-signal-bar-fill');
        const ssidEl = row.querySelector('.rf-device-ssid');
        const metaEl = row.querySelector('.rf-device-meta');
        const ageEl = row.querySelector('.rf-device-age');

        if (rssiVal) {
            rssiVal.textContent = (typeof dev.rssi === 'number')
                ? dev.rssi.toFixed(0) + ' dBm'
                : '--';
        }
        if (fill) {
            const pct = Math.max(0, Math.min(100, (dev.rssi || -100) + 100));
            fill.style.width = pct + '%';
            fill.setAttribute(
                'data-strength',
                pct > 55 ? 'strong' : pct > 30 ? 'medium' : 'weak',
            );
        }
        if (ssidEl) {
            if (dev.ssid) {
                ssidEl.textContent = dev.ssid;
                ssidEl.classList.remove('is-hidden');
            } else {
                ssidEl.textContent = '<hidden>';
                ssidEl.classList.add('is-hidden');
            }
        }
        if (metaEl) {
            const parts = [dev.bssid || '?'];
            if (dev.channel != null) parts.push('ch ' + dev.channel);
            else if (dev.freq_mhz != null) parts.push(
                (dev.freq_mhz).toFixed(1) + ' MHz',
            );
            if (dev.manuf) parts.push(dev.manuf);
            metaEl.textContent = parts.join(' · ');
        }
        if (ageEl) {
            const age = secondsSince(dev.last_seen);
            ageEl.textContent = formatAge(age);
        }
    }

    function secondsSince(epoch) {
        if (!epoch || epoch < 1e6) return null;
        return Math.max(0, Math.floor(Date.now() / 1000 - epoch));
    }

    function formatAge(sec) {
        if (sec == null) return '--';
        if (sec < 60) return sec + 's';
        if (sec < 3600) return Math.floor(sec / 60) + 'm';
        return Math.floor(sec / 3600) + 'h';
    }

    // ── Rendering: state timeline ─────────────────────────────────────

    function renderEvents(events) {
        const section = document.getElementById('ops-rf-events-section');
        const list = document.getElementById('ops-rf-events');
        if (!list || !section) return;
        if (!Array.isArray(events) || events.length === 0) {
            section.setAttribute('hidden', 'hidden');
            while (list.firstChild) list.removeChild(list.firstChild);
            return;
        }
        section.removeAttribute('hidden');

        // Render newest-last but scroll-friendly — keep last 10 visible.
        while (list.firstChild) list.removeChild(list.firstChild);
        const shown = events.slice(-10);
        for (const ev of shown) {
            const row = document.createElement('div');
            row.className = 'rf-event-row state-' + (ev.to || 'idle');
            row.setAttribute('role', 'listitem');

            const t = document.createElement('span');
            t.className = 'rf-event-time';
            t.textContent = formatClockTime(ev.t);
            row.appendChild(t);

            const lbl = document.createElement('span');
            lbl.className = 'rf-event-label';
            const from = document.createElement('span');
            from.textContent = (ev.from || '').toUpperCase();
            const arrow = document.createElement('span');
            arrow.className = 'rf-event-arrow';
            arrow.textContent = '→';
            const to = document.createElement('span');
            to.textContent = (ev.to || '').toUpperCase();
            lbl.appendChild(from);
            lbl.appendChild(arrow);
            lbl.appendChild(to);
            row.appendChild(lbl);

            const elapsed = document.createElement('span');
            elapsed.className = 'rf-event-elapsed';
            elapsed.textContent = (
                typeof ev.elapsed_prev_sec === 'number'
                    ? ev.elapsed_prev_sec.toFixed(1) + 's'
                    : '--'
            );
            row.appendChild(elapsed);

            list.appendChild(row);
        }
        list.scrollTop = list.scrollHeight;
    }

    function formatClockTime(epoch) {
        if (!epoch || epoch < 1e6) return '--';
        const d = new Date(epoch * 1000);
        const pad = (n) => (n < 10 ? '0' + n : '' + n);
        return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }

    // ── Rendering: ambient spectrum ───────────────────────────────────

    function renderAmbient(payload) {
        const section = document.getElementById('ops-rf-ambient-section');
        const host = document.getElementById('ops-rf-ambient');
        if (!host || !section) return;
        const samples = (payload && Array.isArray(payload.samples))
            ? payload.samples
            : [];
        const enabled = payload && payload.enabled;
        if (!enabled || samples.length === 0) {
            section.setAttribute('hidden', 'hidden');
            while (host.firstChild) host.removeChild(host.firstChild);
            return;
        }
        section.removeAttribute('hidden');
        while (host.firstChild) host.removeChild(host.firstChild);
        // Show up to 14 strongest recent samples as vertical bars.
        const sorted = samples.slice().sort(
            (a, b) => (b.rssi_dbm || -120) - (a.rssi_dbm || -120),
        ).slice(0, 14);
        const maxH = 56;
        for (const s of sorted) {
            const bar = document.createElement('div');
            bar.className = 'rf-ambient-bar';
            const fill = document.createElement('div');
            fill.className = 'rf-ambient-bar-fill';
            const pct = Math.max(0, Math.min(100, (s.rssi_dbm || -100) + 100));
            fill.style.height = Math.round((pct / 100) * maxH) + 'px';
            bar.appendChild(fill);
            const label = document.createElement('div');
            label.className = 'rf-ambient-bar-label';
            const name = s.name || s.mac || s.type || '';
            label.textContent = (name.length > 6 ? name.slice(0, 6) : name);
            bar.appendChild(label);
            host.appendChild(bar);
        }
    }

    // ── Rendering: RSSI chart (config view) ───────────────────────────

    function setRssiThresholds(threshold, converge) {
        rssiThreshold = (typeof threshold === 'number') ? threshold : null;
        rssiConverge = (typeof converge === 'number') ? converge : null;
    }

    function renderRssiChart(history) {
        const host = document.getElementById('ctrl-rf-rssi-chart');
        if (!host) return;
        if (Array.isArray(history)) {
            rssiHistory = history
                .map((s) => (typeof s.rssi === 'number' ? s.rssi : null))
                .filter((v) => v != null);
            if (rssiHistory.length > RSSI_HIST_MAX) {
                rssiHistory = rssiHistory.slice(-RSSI_HIST_MAX);
            }
        }
        // Ensure the host has the right class and scaffold.
        host.classList.add('rf-rssi-chart');
        while (host.firstChild) host.removeChild(host.firstChild);
        const svgNs = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(svgNs, 'svg');
        svg.setAttribute('viewBox', '0 0 300 120');
        svg.setAttribute('preserveAspectRatio', 'none');

        // Threshold bands (dashed).
        if (rssiThreshold != null) {
            svg.appendChild(drawThreshold(svgNs, rssiThreshold, '#eab308'));
        }
        if (rssiConverge != null) {
            svg.appendChild(drawThreshold(svgNs, rssiConverge, 'var(--olive-primary, #385723)'));
        }

        if (rssiHistory.length >= 2) {
            const n = rssiHistory.length;
            const pts = [];
            for (let i = 0; i < n; i++) {
                const x = (i / (n - 1)) * 300;
                const y = 120 - ((rssiHistory[i] + 100) / 100) * 120;
                pts.push(x.toFixed(1) + ',' + y.toFixed(1));
            }
            const poly = document.createElementNS(svgNs, 'polyline');
            poly.setAttribute('points', pts.join(' '));
            poly.setAttribute('fill', 'none');
            poly.setAttribute(
                'stroke',
                'var(--olive-primary, #385723)',
            );
            poly.setAttribute('stroke-width', '1.5');
            poly.setAttribute('vector-effect', 'non-scaling-stroke');
            svg.appendChild(poly);
        }

        host.appendChild(svg);
    }

    function drawThreshold(ns, dbm, stroke) {
        const y = 120 - ((dbm + 100) / 100) * 120;
        const line = document.createElementNS(ns, 'line');
        line.setAttribute('x1', '0');
        line.setAttribute('x2', '300');
        line.setAttribute('y1', String(y.toFixed(1)));
        line.setAttribute('y2', String(y.toFixed(1)));
        line.setAttribute('stroke', stroke);
        line.setAttribute('stroke-width', '1');
        line.setAttribute('stroke-dasharray', '4,3');
        line.setAttribute('opacity', '0.6');
        line.setAttribute('vector-effect', 'non-scaling-stroke');
        return line;
    }

    // ── Converged flash + beep ────────────────────────────────────────

    function maybeFlashOnConverge(status) {
        if (!status) return;
        const state = status.state;
        const was = lastState;
        lastState = state;
        if (state !== 'converged' || was === 'converged') return;
        const now = Date.now();
        // De-bounce: once per 5 s max — protects against poll races.
        if (now - lastFlashMs < 5000) return;
        lastFlashMs = now;
        triggerConvergeFlash();
    }

    let convergeFlashMs = 2500;

    function setConvergeFlashMs(ms) {
        if (typeof ms === 'number' && ms >= 500 && ms <= 10000) {
            convergeFlashMs = ms;
        }
    }

    function triggerConvergeFlash() {
        const el = document.getElementById('rf-converge-flash');
        if (el) {
            el.style.setProperty('--rf-flash-ms', convergeFlashMs + 'ms');
            el.classList.remove('active');
            // Force reflow so the animation restarts cleanly.
            void el.offsetWidth;
            el.classList.add('active');
            setTimeout(
                () => el.classList.remove('active'),
                convergeFlashMs + 100,
            );
        }
        beepOnce();
        if (typeof window.showToast === 'function') {
            window.showToast('Signal locked — RF hunt converged', 'success');
        }
    }

    function beepOnce() {
        try {
            if (!audioCtx) {
                const Ctx = window.AudioContext || window.webkitAudioContext;
                if (!Ctx) return;
                audioCtx = new Ctx();
            }
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.type = 'sine';
            osc.frequency.value = 660;
            const now = audioCtx.currentTime;
            gain.gain.setValueAtTime(0.0001, now);
            gain.gain.exponentialRampToValueAtTime(0.25, now + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
            osc.connect(gain).connect(audioCtx.destination);
            osc.start(now);
            osc.stop(now + 0.2);
        } catch (_err) {
            // Visual flash is the primary cue — audio is a nice-to-have.
        }
    }

    // ── Target confirmation modal ─────────────────────────────────────

    // Valid 48-bit MAC in AA:BB:CC:DD:EE:FF form — mirrors BSSID_RE on the
    // server. Non-MAC identifiers (e.g. "SDR:915.3" in the replay fixture)
    // must route through the SDR target path instead.
    const BSSID_RE = /^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$/i;

    function openTargetModal(dataset) {
        const rawBssid = (dataset && dataset.bssid) || '';
        const rawFreq = (dataset && dataset.freq) || '';
        const ssid = (dataset && dataset.ssid) || '';
        const parsedFreq = rawFreq ? parseFloat(rawFreq) : NaN;
        const hasValidMac = BSSID_RE.test(rawBssid);
        const hasFreq = !isNaN(parsedFreq);

        let mode;
        let bssid = null;
        let freq = null;
        if (hasValidMac) {
            mode = 'wifi';
            bssid = rawBssid.toUpperCase();
        } else if (hasFreq) {
            mode = 'sdr';
            freq = parsedFreq;
        } else {
            // Nothing targetable on this row — abort.
            if (typeof window.showToast === 'function') {
                window.showToast(
                    'Cannot target device: no MAC address or frequency',
                    'error',
                );
            }
            return;
        }

        const idLabel = bssid || (freq != null ? freq.toFixed(1) + ' MHz' : '--');
        const label = ssid ? (ssid + ' (' + idLabel + ')') : idLabel;
        pendingTarget = {
            bssid: bssid,
            freq_mhz: freq,
            mode: mode,
            label: label,
        };
        const modal = document.getElementById('rf-target-modal');
        const labelEl = document.getElementById('rf-target-label');
        if (labelEl) labelEl.textContent = label;
        if (modal) modal.classList.add('active');
    }

    function closeTargetModal() {
        const modal = document.getElementById('rf-target-modal');
        if (modal) modal.classList.remove('active');
        pendingTarget = null;
    }

    async function confirmTarget() {
        if (!pendingTarget) return;
        const body = { confirm: true, mode: pendingTarget.mode };
        if (pendingTarget.bssid) body.bssid = pendingTarget.bssid;
        if (pendingTarget.freq_mhz != null) body.freq_mhz = pendingTarget.freq_mhz;
        const token = getBearerToken();
        const headers = { 'Content-Type': 'application/json' };
        if (token) headers.Authorization = 'Bearer ' + token;
        const label = pendingTarget.label;
        closeTargetModal();
        try {
            const resp = await fetch('/api/rf/target', {
                method: 'POST',
                credentials: 'same-origin',
                headers: headers,
                body: JSON.stringify(body),
            });
            if (resp.ok) {
                if (typeof window.showToast === 'function') {
                    window.showToast('RF hunt target set: ' + label, 'success');
                }
                pollOnce();
            } else {
                const err = await resp.json().catch(() => ({}));
                if (typeof window.showToast === 'function') {
                    window.showToast(
                        'Failed to set target: ' + (err.error || resp.status),
                        'error',
                    );
                }
            }
        } catch (_err) {
            if (typeof window.showToast === 'function') {
                window.showToast('Network error setting RF target', 'error');
            }
        }
    }

    function getBearerToken() {
        if (typeof window.getApiToken === 'function') return window.getApiToken();
        if (window.HydraApp && window.HydraApp.state) {
            return window.HydraApp.state.apiToken || '';
        }
        return '';
    }

    function wireTargetModal() {
        const modal = document.getElementById('rf-target-modal');
        if (!modal || modal._rfWired) return;
        modal._rfWired = true;
        const confirmBtn = document.getElementById('rf-target-confirm');
        const cancelBtn = document.getElementById('rf-target-cancel');
        if (confirmBtn) confirmBtn.addEventListener('click', confirmTarget);
        if (cancelBtn) cancelBtn.addEventListener('click', closeTargetModal);
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeTargetModal();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && modal.classList.contains('active')) {
                closeTargetModal();
            }
        });
    }

    return {
        onOpsEnter: onOpsEnter,
        onOpsLeave: onOpsLeave,
        onConfigEnter: onConfigEnter,
        onConfigLeave: onConfigLeave,
        setRfTabActive: setRfTabActive,
        setRssiThresholds: setRssiThresholds,
        pollOnce: pollOnce,
    };
})();

if (typeof window !== 'undefined') {
    window.HydraRfHunt = HydraRfHunt;
}
