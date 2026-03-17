'use strict';

const HydraMonitor = (() => {
    let idleTimer = null;
    let updateTimer = null;
    let streamRetryTimer = null;
    let streamLoaded = false;

    function onEnter() {
        startIdleCheck();
        startUpdates();
        initStreamWatcher();
        initToolbarHandlers();
    }

    function onLeave() {
        stopIdleCheck();
        stopUpdates();
        if (streamRetryTimer) clearInterval(streamRetryTimer);
    }

    // ── Auto-hide overlays when idle ──
    function startIdleCheck() {
        idleTimer = setInterval(() => {
            if (HydraApp.isIdle(5000)) {
                document.body.classList.add('monitor-idle');
            } else {
                document.body.classList.remove('monitor-idle');
            }
        }, 500);
    }

    function stopIdleCheck() {
        if (idleTimer) clearInterval(idleTimer);
        document.body.classList.remove('monitor-idle');
    }

    // ── Data updates ──
    function startUpdates() {
        updateTimer = setInterval(updateAll, 500);
        updateAll();
    }

    function stopUpdates() {
        if (updateTimer) clearInterval(updateTimer);
    }

    function updateAll() {
        updateVitals();
        updateVehicle();
        updateTracks();
        updateRF();
        updateLockIndicator();
    }

    function updateVitals() {
        const s = HydraApp.state.stats;
        setText('mon-fps', s.fps != null ? s.fps.toFixed(1) : '--');
        setText('mon-inference', s.inference_ms != null ? s.inference_ms.toFixed(0) + 'ms' : '--');
        setText('mon-gpu', s.gpu_temp_c != null ? s.gpu_temp_c.toFixed(0) + '°C ' + (s.gpu_load_pct || 0).toFixed(0) + '%' : '--');
    }

    function updateVehicle() {
        const s = HydraApp.state.stats;
        setText('mon-mode', s.vehicle_mode || '--');
        setText('mon-armed', s.armed ? 'ARMED' : 'DISARMED');
        const armEl = document.getElementById('mon-armed');
        if (armEl) armEl.style.color = s.armed ? '#fca5a5' : 'var(--ogt-muted)';

        if (s.battery_pct != null) {
            setText('mon-battery', s.battery_pct + '%');
            const batEl = document.getElementById('mon-battery');
            if (batEl) batEl.style.color = s.battery_pct < 20 ? 'var(--danger)' : s.battery_pct < 40 ? 'var(--warning)' : 'var(--text-primary)';
        } else {
            setText('mon-battery', '--');
        }

        setText('mon-alt', s.altitude_m != null ? s.altitude_m.toFixed(1) + 'm' : '--');
        setText('mon-heading', s.heading_deg != null ? s.heading_deg.toFixed(0) + '°' : '--');
        setText('mon-speed', s.groundspeed != null ? s.groundspeed.toFixed(1) + 'm/s' : '--');
    }

    function updateTracks() {
        const tracks = HydraApp.state.tracks;
        const s = HydraApp.state.stats;
        setText('mon-tracks', tracks.length);
        setText('mon-total-det', s.total_detections || 0);

        const container = document.getElementById('mon-track-labels');
        if (!container) return;
        // Clear existing children safely
        while (container.firstChild) {
            container.removeChild(container.firstChild);
        }
        const labels = {};
        tracks.forEach(t => { labels[t.label] = (labels[t.label] || 0) + 1; });
        Object.entries(labels).forEach(([label, count]) => {
            const pill = document.createElement('span');
            pill.className = 'track-label-pill';
            pill.textContent = count > 1 ? `${label} \u00d7${count}` : label;
            container.appendChild(pill);
        });
    }

    function updateRF() {
        const rf = HydraApp.state.rfStatus;
        const section = document.getElementById('mon-rf-section');
        if (!section) return;
        if (rf.state === 'unavailable' || rf.state === 'IDLE') {
            section.style.display = 'none';
        } else {
            section.style.display = '';
            setText('mon-rf-state', rf.state || '--');
            setText('mon-rf-rssi', rf.rssi_dbm != null ? rf.rssi_dbm.toFixed(0) + ' dBm' : '--');
        }
    }

    function updateLockIndicator() {
        const t = HydraApp.state.target;
        const el = document.getElementById('monitor-lock-indicator');
        if (!el) return;
        if (t.locked) {
            el.style.display = '';
            setText('lock-label', `#${t.track_id} ${t.label || ''}`);
            setText('lock-mode', (t.mode || 'track').toUpperCase());
            el.classList.toggle('strike', t.mode === 'strike');
        } else {
            el.style.display = 'none';
        }
    }

    // ── Stream watcher ──
    function initStreamWatcher() {
        const img = document.getElementById('mjpeg-stream');
        const loading = document.getElementById('monitor-loading');
        const lost = document.getElementById('monitor-stream-lost');
        if (!img) return;

        img.addEventListener('load', () => {
            streamLoaded = true;
            if (loading) loading.style.display = 'none';
            if (lost) lost.style.display = 'none';
        }, { once: true });

        img.addEventListener('error', () => {
            if (lost) lost.style.display = '';
            if (!streamRetryTimer) {
                streamRetryTimer = setInterval(() => {
                    img.src = '/stream.mjpeg?' + Date.now();
                }, 2000);
            }
        });

        // Hide loading if already loaded
        if (img.complete && img.naturalWidth > 0) {
            streamLoaded = true;
            if (loading) loading.style.display = 'none';
        }
    }

    // ── Quick action handlers ──
    function initToolbarHandlers() {
        on('mon-btn-lock', async () => {
            const tracks = HydraApp.state.tracks;
            if (tracks.length === 0) {
                HydraApp.showToast('No active tracks to lock');
                return;
            }
            // Lock the first available track (or highest confidence)
            const best = tracks.reduce((a, b) => a.confidence > b.confidence ? a : b);
            await HydraApp.apiPost('/api/target/lock', { track_id: best.track_id });
        });

        on('mon-btn-strike', async () => {
            const t = HydraApp.state.target;
            if (!t.locked) {
                HydraApp.showToast('No target locked \u2014 lock a target first');
                return;
            }
            // Open strike modal
            const modal = document.getElementById('strike-modal');
            const label = document.getElementById('strike-target-label');
            if (label) label.textContent = `#${t.track_id} ${t.label || ''}`;
            if (modal) modal.classList.add('active');

            // Wire confirm/cancel
            const confirmBtn = document.getElementById('strike-confirm');
            const cancelBtn = document.getElementById('strike-cancel');
            const handler = async () => {
                if (modal) modal.classList.remove('active');
                await HydraApp.apiPost('/api/target/strike', { track_id: t.track_id, confirm: true });
                confirmBtn.removeEventListener('click', handler);
            };
            confirmBtn.addEventListener('click', handler);
            cancelBtn.addEventListener('click', () => {
                if (modal) modal.classList.remove('active');
                confirmBtn.removeEventListener('click', handler);
            }, { once: true });
        });

        on('mon-btn-release', () => HydraApp.apiPost('/api/target/unlock', {}));
        on('mon-btn-loiter', () => HydraApp.apiPost('/api/vehicle/mode', { mode: 'LOITER' }));
        on('mon-btn-rtl', () => HydraApp.apiPost('/api/vehicle/mode', { mode: 'RTL' }));
        on('mon-btn-auto', () => HydraApp.apiPost('/api/vehicle/mode', { mode: 'AUTO' }));
    }

    // ── Helpers ──
    function setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    function on(id, handler) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', handler);
    }

    return { onEnter, onLeave };
})();
