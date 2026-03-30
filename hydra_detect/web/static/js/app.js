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
    let apiToken = sessionStorage.getItem('hydra_token') || '';

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

    function setApiToken(token) {
        apiToken = token;
        sessionStorage.setItem('hydra_token', token);
    }

    function promptForToken() {
        const token = prompt('API token required.\nEnter the api_token from config.ini:');
        if (token) {
            setApiToken(token.trim());
            return true;
        }
        return false;
    }

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

        // Pause/resume stream polling on view switch to avoid CSS-triggered
        // error events (Settings view sets img width/height to 0, which aborts
        // pending requests and fires error events).
        if (view === 'operations') resumeStream();
        else pauseStream();

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
    let _callsignSet = false;
    let _duplicateWarningShown = false;

    function updateTopBarStats(data) {
        const fpsEl = document.getElementById('fps-display');
        if (fpsEl) fpsEl.textContent = `${(data.fps || 0).toFixed(1)} FPS`;

        // Display callsign in topbar brand (once)
        if (data.callsign && !_callsignSet) {
            const brandEl = document.querySelector('.topbar-brand');
            if (brandEl) {
                brandEl.textContent = `${data.callsign}`;
                document.title = `${data.callsign} — SORCC`;
                _callsignSet = true;
            }
        }

        // Duplicate callsign warning
        if (data.duplicate_callsign && !_duplicateWarningShown) {
            showToast(`DUPLICATE CALLSIGN: another ${data.callsign} detected on network`, 'error');
            _duplicateWarningShown = true;
        }

        // Low-light indicator
        const badge = document.getElementById('low-light-badge');
        if (badge) {
            badge.classList.toggle('visible', !!data.low_light);
        }
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
            let resp = await fetch(url, {
                method: 'POST',
                headers: authHeaders(),
                body: JSON.stringify(body),
            });
            // If 401, prompt for token and retry once
            if (resp.status === 401 && promptForToken()) {
                resp = await fetch(url, {
                    method: 'POST',
                    headers: authHeaders(),
                    body: JSON.stringify(body),
                });
            }
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
            '> I SEE YOU......',
            '> STEVE....',
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
                // Hold for 3 seconds, then glitch out
                setTimeout(glitchOut, 3000);
                return;
            }
            lineEls[lineIdx].classList.add('visible');
            lineIdx++;
            // "I SEE YOU" and "STEVE" get dramatic pauses
            let delay = 700;
            if (lineIdx >= lineEls.length) delay = 0;
            else if (lineIdx === lineEls.length - 1) delay = 2000;  // pause before STEVE
            else if (lineIdx === lineEls.length - 2) delay = 1000;  // pause after I SEE YOU
            setTimeout(showNextLine, delay);
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

        // Start typing after overlay fades in
        setTimeout(showNextLine, 800);
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

    // ── MJPEG Stream — snapshot polling ──
    let lastFrameTime = Date.now();
    let staleTimer = null;
    let streamPolling = false;
    let streamBackoff = 1000;

    function initStreamWatcher() {
        const streamImg = document.getElementById('mjpeg-stream');
        if (!streamImg) return;

        function pollFrame() {
            if (!streamPolling) return;
            streamImg.src = '/stream.jpg?t=' + Date.now();
        }

        streamImg.addEventListener('load', () => {
            const lost = document.getElementById('ops-stream-lost');
            if (lost) lost.style.display = 'none';
            lastFrameTime = Date.now();
            hideStaleOverlay();
            streamBackoff = 1000;
            if (streamPolling) setTimeout(pollFrame, 33);
        });

        streamImg.addEventListener('error', () => {
            // Only show lost badge if we're actively polling (not paused)
            if (streamPolling) {
                const lost = document.getElementById('ops-stream-lost');
                if (lost) lost.style.display = '';
                setTimeout(pollFrame, streamBackoff);
                streamBackoff = Math.min(streamBackoff * 2, 10000);
            }
        });

        // Pause polling when tab is hidden to save Jetson CPU
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                pauseStream();
            } else if (currentView === 'operations') {
                resumeStream();
            }
        });

        // Start polling if we're on operations view
        if (currentView === 'operations') {
            resumeStream();
        }

        // Mirror stream to thumbnail — poll /stream.jpg directly so it
        // updates even when the main stream is paused (settings view).
        const thumb = document.getElementById('mjpeg-thumbnail');
        if (thumb) {
            setInterval(() => {
                if (currentView !== 'operations') {
                    thumb.src = '/stream.jpg?thumb=1&t=' + Date.now();
                } else if (streamImg.naturalWidth > 0) {
                    // In operations view, copy from the main img (no extra request)
                    const canvas = document.createElement('canvas');
                    const ctx = canvas.getContext('2d');
                    canvas.width = 120;
                    canvas.height = Math.round(120 * streamImg.naturalHeight / streamImg.naturalWidth);
                    ctx.drawImage(streamImg, 0, 0, canvas.width, canvas.height);
                    thumb.src = canvas.toDataURL('image/jpeg', 0.5);
                }
            }, 2000);
        }

        // Double-click video to toggle fullscreen
        streamImg.addEventListener('dblclick', toggleFullscreen);
    }

    function toggleFullscreen() {
        const el = document.getElementById('mjpeg-stream');
        if (!el) return;
        if (document.fullscreenElement) {
            document.exitFullscreen();
        } else {
            el.requestFullscreen().catch(() => {});
        }
    }

    function pauseStream() {
        streamPolling = false;
    }

    function resumeStream() {
        if (streamPolling) return;  // Already running
        streamPolling = true;
        streamBackoff = 1000;
        const streamImg = document.getElementById('mjpeg-stream');
        if (streamImg) streamImg.src = '/stream.jpg?t=' + Date.now();
    }

    function setupStaleVideoDetection(streamImg) {
        // Disabled — snapshot polling handles its own error/retry state.
        // The VIDEO LOST overlay was triggering false positives during
        // the MJPEG-to-snapshot migration. Re-enable once streaming is stable.
    }

    function showStaleOverlay(message, critical) {
        let overlay = document.getElementById('stale-video-overlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'stale-video-overlay';
            const streamImg = document.getElementById('mjpeg-stream');
            const streamContainer = streamImg ? streamImg.parentElement : null;
            if (streamContainer) {
                streamContainer.style.position = 'relative';
                streamContainer.appendChild(overlay);
            }
        }
        overlay.textContent = message;
        overlay.className = critical ? 'stale-overlay critical' : 'stale-overlay warning';
        overlay.style.display = 'flex';
    }

    function hideStaleOverlay() {
        const overlay = document.getElementById('stale-video-overlay');
        if (overlay) overlay.style.display = 'none';
    }

    // ── Adaptive MJPEG Quality ──
    let lowBandwidthMode = false;
    let adaptiveFrameTimes = [];

    function initAdaptiveQuality() {
        const streamImg = document.getElementById('mjpeg-stream');
        if (!streamImg) return;

        // Track frame delivery rate for adaptive quality
        streamImg.addEventListener('load', () => {
            const now = Date.now();
            adaptiveFrameTimes.push(now);
            // Keep last 30 frame timestamps
            if (adaptiveFrameTimes.length > 30) adaptiveFrameTimes.shift();
        });

        // Auto-adapt every 5 seconds
        setInterval(() => {
            if (lowBandwidthMode || adaptiveFrameTimes.length < 5) return;
            const recent = adaptiveFrameTimes.slice(-10);
            if (recent.length < 2) return;
            const elapsed = (recent[recent.length - 1] - recent[0]) / 1000;
            const fps = (recent.length - 1) / elapsed;
            // If delivered FPS drops below 3, reduce quality
            if (fps < 3) {
                apiGet('/api/stream/quality').then(data => {
                    if (data && data.quality > 30) {
                        apiPost('/api/stream/quality', { quality: data.quality - 10 });
                    }
                });
            }
        }, 5000);
    }

    function toggleLowBandwidth() {
        lowBandwidthMode = !lowBandwidthMode;
        const btn = document.getElementById('bandwidth-toggle');
        if (btn) btn.classList.toggle('active', lowBandwidthMode);
        const quality = lowBandwidthMode ? 30 : 70;
        apiPost('/api/stream/quality', { quality });
    }

    // ── Pre-Flight Checklist ──
    async function runPreflight() {
        try {
            const resp = await fetch('/api/preflight');
            if (!resp.ok) return;
            const data = await resp.json();
            if (data.overall === 'fail' || data.overall === 'warn') {
                showPreflightOverlay(data.checks, data.overall === 'fail');
            }
        } catch (e) {
            console.warn('Preflight check failed:', e);
        }
    }

    function showPreflightOverlay(checks, blocking) {
        let overlay = document.getElementById('preflight-overlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'preflight-overlay';
            document.body.appendChild(overlay);
        }
        // Clear previous content safely
        while (overlay.firstChild) overlay.removeChild(overlay.firstChild);

        const card = document.createElement('div');
        card.className = 'preflight-card';

        const title = document.createElement('h2');
        title.className = 'preflight-title ' + (blocking ? 'fail' : 'warn');
        title.textContent = blocking ? 'PRE-FLIGHT FAILED' : 'PRE-FLIGHT WARNING';
        card.appendChild(title);

        const checksDiv = document.createElement('div');
        checksDiv.className = 'preflight-checks';
        checks.forEach(function(c) {
            const item = document.createElement('div');
            item.className = 'preflight-item preflight-' + c.status;

            const icon = document.createElement('span');
            icon.className = 'preflight-icon';
            icon.textContent = c.status === 'pass' ? '\u2713' : c.status === 'warn' ? '\u26A0' : '\u2717';
            item.appendChild(icon);

            const name = document.createElement('span');
            name.className = 'preflight-name';
            name.textContent = c.name;
            item.appendChild(name);

            const msg = document.createElement('span');
            msg.className = 'preflight-msg';
            msg.textContent = c.message;
            item.appendChild(msg);

            checksDiv.appendChild(item);
        });
        card.appendChild(checksDiv);

        if (blocking) {
            const note = document.createElement('p');
            note.className = 'preflight-note';
            note.textContent = 'Fix critical issues before operating';
            card.appendChild(note);

            const btn = document.createElement('button');
            btn.className = 'preflight-btn';
            btn.textContent = 'Re-check';
            btn.addEventListener('click', runPreflight);
            card.appendChild(btn);
        } else {
            const btn = document.createElement('button');
            btn.className = 'preflight-btn';
            btn.textContent = 'Continue';
            btn.addEventListener('click', dismissPreflight);
            card.appendChild(btn);
        }

        overlay.appendChild(card);
        overlay.style.display = 'flex';
    }

    function dismissPreflight() {
        var overlay = document.getElementById('preflight-overlay');
        if (overlay) overlay.style.display = 'none';
    }

    // ── Init ──
    function init() {
        runPreflight();
        initRouter();
        initPresentationMode();
        initKonamiListener();
        initModalEscape();
        initStreamWatcher();
        initAdaptiveQuality();
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
        toggleLowBandwidth,
        toggleFullscreen,
        runPreflight,
        dismissPreflight,
    };
})();
