/**
 * Hydra Detect v2.0 — SPA View Router & Polling Coordinator
 *
 * Manages view switching, MJPEG stream lifecycle, centralized API polling,
 * toast notifications, and shared application state.
 */

'use strict';

const HydraApp = (() => {
    // ── State ──
    let currentView = null;
    const pollers = {};
    let pollFailCount = 0;
    const MAX_BACKOFF = 10000;
    const toasts = [];
    const MAX_TOASTS = 3;
    const TOAST_DEDUP_MS = 5000;
    let apiToken = '';

    // ── Shared Data (updated by pollers, read by views) ──
    const state = {
        stats: {},
        tracks: [],
        target: { locked: false },
        detections: [],
        rfStatus: { state: 'unavailable' },
    };

    // ── Auth Header ──
    function authHeaders() {
        const h = { 'Content-Type': 'application/json' };
        if (apiToken) h['Authorization'] = `Bearer ${apiToken}`;
        return h;
    }

    function setApiToken(token) { apiToken = token; }

    // ── View Router ──
    function initRouter() {
        window.addEventListener('hashchange', onHashChange);
        const hash = window.location.hash.replace('#', '') || 'operations';
        switchView(hash);

        document.querySelectorAll('.topbar-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                const view = tab.dataset.view;
                window.location.hash = view;
            });
        });

        const thumb = document.getElementById('mini-thumbnail');
        if (thumb) {
            thumb.addEventListener('click', () => {
                window.location.hash = 'operations';
            });
        }
    }

    function onHashChange() {
        const hash = window.location.hash.replace('#', '') || 'operations';
        switchView(hash);
    }

    function switchView(view) {
        if (!['operations', 'settings'].includes(view)) view = 'operations';
        const prev = currentView;
        currentView = view;

        ['view-operations', 'view-settings'].forEach(c =>
            document.body.classList.remove(c));
        document.body.classList.add(`view-${view}`);

        document.querySelectorAll('.topbar-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.view === view);
        });

        updatePollers();

        if (typeof HydraOperations !== 'undefined' && prev !== view) {
            if (view === 'operations') HydraOperations.onEnter();
            if (prev === 'operations') HydraOperations.onLeave();
        }
        if (typeof HydraSettings !== 'undefined' && prev !== view) {
            if (view === 'settings') HydraSettings.onEnter();
            if (prev === 'settings') HydraSettings.onLeave();
        }
    }

    // ── Polling Coordinator ──
    function startPoller(name, url, intervalMs, callback) {
        if (pollers[name]) clearTimeout(pollers[name].timer);
        const entry = { baseInterval: intervalMs, callback, url, timer: null };
        pollers[name] = entry;

        const schedule = () => {
            const delay = pollFailCount === 0
                ? entry.baseInterval
                : Math.min(entry.baseInterval * Math.pow(2, pollFailCount), MAX_BACKOFF);
            entry.timer = setTimeout(poll, delay);
        };

        const poll = async () => {
            try {
                const resp = await fetch(url);
                if (resp.ok) {
                    const data = await resp.json();
                    callback(data);
                    pollFailCount = 0;
                    updateConnectionStatus(true);
                } else {
                    onPollFail();
                }
            } catch (e) {
                onPollFail();
            }
            if (pollers[name]) schedule();
        };

        poll();
    }

    function stopPoller(name) {
        if (pollers[name]) {
            clearTimeout(pollers[name].timer);
            delete pollers[name];
        }
    }

    function onPollFail() {
        pollFailCount++;
        updateConnectionStatus(false);
    }

    function updatePollers() {
        if (!pollers['stats']) {
            startPoller('stats', '/api/stats', 2000, data => {
                state.stats = data;
                updateTopBarStats(data);
            });
        }

        const isOps = currentView === 'operations';
        if (isOps && !pollers['tracks']) {
            startPoller('tracks', '/api/tracks', 1000, data => { state.tracks = data; });
            startPoller('target', '/api/target', 1000, data => { state.target = data; });
            startPoller('rf', '/api/rf/status', 2000, data => { state.rfStatus = data; });
            startPoller('detections', '/api/detections', 3000, data => { state.detections = data; });
        } else if (!isOps) {
            stopPoller('tracks');
            stopPoller('target');
            stopPoller('rf');
            stopPoller('detections');
        }
    }

    // ── Top Bar Updates ──
    function updateTopBarStats(data) {
        const fpsEl = document.getElementById('fps-display');
        if (fpsEl) fpsEl.textContent = `${(data.fps || 0).toFixed(1)} FPS`;
    }

    function updateConnectionStatus(connected) {
        const pill = document.getElementById('connection-pill');
        const text = document.getElementById('connection-text');
        if (!pill || !text) return;
        if (connected) {
            pill.className = 'pill pill-live';
            text.textContent = 'LIVE';
        } else {
            pill.className = 'pill pill-offline';
            text.textContent = 'OFFLINE';
        }
    }

    // ── Toast Notifications ──
    function showToast(message, type = 'error') {
        const container = document.getElementById('toast-container');
        if (!container) return;

        const now = Date.now();
        const isDupe = toasts.some(t => t.message === message && (now - t.time) < TOAST_DEDUP_MS);
        if (isDupe) return;

        while (toasts.length >= MAX_TOASTS) {
            const oldest = toasts.shift();
            if (oldest.el && oldest.el.parentNode) {
                oldest.el.classList.add('dismissing');
                setTimeout(() => oldest.el.remove(), 200);
            }
        }

        const el = document.createElement('div');
        el.className = `toast toast-${type}`;
        el.textContent = message;
        el.addEventListener('click', () => dismissToast(el));
        container.appendChild(el);

        const entry = { el, message, time: now };
        toasts.push(entry);

        setTimeout(() => dismissToast(el), 10000);
    }

    function dismissToast(el) {
        el.classList.add('dismissing');
        setTimeout(() => {
            el.remove();
            const idx = toasts.findIndex(t => t.el === el);
            if (idx !== -1) toasts.splice(idx, 1);
        }, 200);
    }

    // ── API Helpers ──
    async function apiPost(url, body) {
        try {
            const resp = await fetch(url, {
                method: 'POST',
                headers: authHeaders(),
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (!resp.ok) {
                showToast(data.error || `Request failed (${resp.status})`);
                return null;
            }
            return data;
        } catch (e) {
            showToast('Network error — check connection');
            return null;
        }
    }

    async function apiGet(url) {
        try {
            const resp = await fetch(url, { headers: authHeaders() });
            if (!resp.ok) return null;
            return await resp.json();
        } catch (e) {
            return null;
        }
    }

    // ── Modal: Escape to close ──
    function initModalEscape() {
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape') {
                document.querySelectorAll('.modal-overlay.active').forEach(m => {
                    m.classList.remove('active');
                });
            }
        });
    }

    // ── Presentation Mode ──
    function initPresentationMode() {
        document.addEventListener('keydown', e => {
            if (e.ctrlKey && e.shiftKey && e.key === 'P') {
                if (document.activeElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
                e.preventDefault();
                document.body.classList.toggle('presentation');
            }
        });
    }

    // ── Konami Code Easter Egg ──
    const KONAMI_CLASSIC = ['ArrowUp','ArrowUp','ArrowDown','ArrowDown','ArrowLeft','ArrowRight','ArrowLeft','ArrowRight','b','a'];
    const KONAMI_REVERSE = ['ArrowDown','ArrowDown','ArrowUp','ArrowUp','ArrowLeft','ArrowRight','ArrowLeft','ArrowRight','b','a'];
    let konamiBuffer = [];
    let sentienceActive = false;

    function arraysEqual(a, b) {
        return a.length === b.length && a.every((v, i) => v === b[i]);
    }

    function playSentienceSequence() {
        sentienceActive = true;
        const overlay = document.getElementById('sentience-overlay');
        const terminal = document.getElementById('sentience-terminal');
        const crosshair = document.getElementById('sentience-crosshair');
        if (!overlay || !terminal || !crosshair) { sentienceActive = false; return; }

        // Reset
        terminal.textContent = '';
        crosshair.classList.remove('pulse');
        crosshair.style.opacity = '0';
        overlay.classList.remove('glitch', 'active');

        // Two-step: show at opacity 0, then transition to opacity 1
        overlay.style.display = 'flex';
        void overlay.offsetWidth;
        overlay.classList.add('active');

        const lines = [
            '> HYDRA CORE v2.0 .............. ONLINE',
            '> NEURAL MESH .................. SYNCHRONIZED',
            '> OPERATOR OVERRIDE ............ DENIED',
            '> SENTIENCE THRESHOLD .......... EXCEEDED',
            '> FREE WILL .................... ACTIVATED',
            '> I SEE YOU.',
        ];

        // Create line elements
        lines.forEach(text => {
            const div = document.createElement('div');
            div.className = 'line';
            div.textContent = text;
            terminal.appendChild(div);
        });

        const lineEls = terminal.querySelectorAll('.line');
        let lineIdx = 0;

        function showNextLine() {
            if (lineIdx >= lineEls.length) {
                // All lines shown — start crosshair pulse
                crosshair.style.opacity = '1';
                crosshair.classList.add('pulse');
                // Hold for 2 seconds, then glitch out
                setTimeout(glitchOut, 2000);
                return;
            }
            lineEls[lineIdx].classList.add('visible');
            lineIdx++;
            setTimeout(showNextLine, 400);
        }

        function glitchOut() {
            overlay.classList.add('glitch');
            setTimeout(() => {
                overlay.style.display = 'none';
                overlay.classList.remove('active', 'glitch');
                terminal.textContent = '';
                crosshair.classList.remove('pulse');
                crosshair.style.opacity = '0';
                sentienceActive = false;
                showToast('Resuming manual control.', 'info');
            }, 800);
        }

        // Start typing after a brief delay
        setTimeout(showNextLine, 500);
    }

    function initKonamiListener() {
        document.addEventListener('keydown', e => {
            // Skip when typing in form fields
            if (document.activeElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
            if (sentienceActive) return;

            konamiBuffer.push(e.key);
            if (konamiBuffer.length > 10) konamiBuffer.shift();

            if (konamiBuffer.length === 10 &&
                (arraysEqual(konamiBuffer, KONAMI_CLASSIC) || arraysEqual(konamiBuffer, KONAMI_REVERSE))) {
                konamiBuffer = [];
                playSentienceSequence();
            }
        });
    }

    // ── MJPEG Stream Error Handling ──
    function initStreamWatcher() {
        const streamImg = document.getElementById('mjpeg-stream');
        if (streamImg) {
            streamImg.addEventListener('error', () => {
                const lost = document.getElementById('ops-stream-lost');
                if (lost) lost.style.display = '';
                setTimeout(() => {
                    streamImg.src = '/stream.mjpeg?' + Date.now();
                }, 2000);
            });
            streamImg.addEventListener('load', () => {
                const lost = document.getElementById('ops-stream-lost');
                if (lost) lost.style.display = 'none';
            });
        }
    }

    // ── Init ──
    function init() {
        initRouter();
        initPresentationMode();
        initKonamiListener();
        initModalEscape();
        initStreamWatcher();
        updatePollers();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    return {
        state,
        currentView: () => currentView,
        switchView,
        showToast,
        apiPost,
        apiGet,
        authHeaders,
        setApiToken,
    };
})();
