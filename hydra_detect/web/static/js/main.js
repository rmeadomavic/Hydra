'use strict';

(function bootstrapHydraApp() {
    const modules = window.HydraModules || {};
    const store = modules.createStore();
    if (window.HydraSimGps) window.HydraSimGps.init(store);
    const toast = modules.createToastService();
    const modal = modules.createModalController();
    const api = modules.createApiClient({ store, toast });
    const preflight = modules.createPreflight({});
    const stream = modules.createStreamController({ getCurrentView: () => store.getState().currentView });

    let callsignSet = false;
    let duplicateWarningShown = false;
    let lowBandwidthMode = false;

    function formatUptime(sec) {
        if (!sec || sec < 0) return '--';
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        return `${h}h ${m}m`;
    }

    function updateConnectionStatus(connected) {
        const pill = document.getElementById('connection-pill');
        const text = document.getElementById('connection-text');
        if (!pill || !text) return;
        pill.className = connected ? 'pill pill-live' : 'pill pill-offline';
        text.textContent = connected ? 'LIVE' : 'OFFLINE';
    }

    function updateTopBarStats(data) {
        const fpsEl = document.getElementById('fps-display');
        if (fpsEl) fpsEl.textContent = `${(data.fps || 0).toFixed(1)} FPS`;

        if (data.callsign && !callsignSet) {
            const brandEl = document.querySelector('.topbar-brand');
            if (brandEl) {
                brandEl.textContent = `${data.callsign}`;
                document.title = `${data.callsign} — SORCC`;
                callsignSet = true;
            }
        }

        if (data.duplicate_callsign && !duplicateWarningShown) {
            toast.showToast(`DUPLICATE CALLSIGN: another ${data.callsign} detected on network`, 'error');
            duplicateWarningShown = true;
        }

        const badge = document.getElementById('low-light-badge');
        if (badge) badge.classList.toggle('visible', !!data.low_light);

        const dotCam = document.getElementById('dot-camera');
        const dotMav = document.getElementById('dot-mavlink');
        const dotGps = document.getElementById('dot-gps');
        if (dotCam) dotCam.className = 'status-dot ' + (data.camera_ok ? 'green' : 'red');
        if (dotMav) dotMav.className = 'status-dot ' + (data.mavlink ? 'green' : 'red');
        if (dotGps) {
            const fix = data.gps_fix || 0;
            dotGps.className = 'status-dot ' + (fix >= 3 ? 'green' : fix >= 2 ? 'yellow' : 'red');
        }

        const trackBadge = document.getElementById('track-count-badge');
        if (trackBadge) trackBadge.textContent = `${data.active_tracks || 0} TRACKS`;

        const footerLeft = document.getElementById('footer-left');
        if (footerLeft && data.callsign) {
            const uptime = data.uptime_sec ? formatUptime(data.uptime_sec) : '--';
            const pos = window.HydraSimGps ? window.HydraSimGps.withSimSuffix(data.position || '--') : (data.position || '--');
            footerLeft.textContent = `${data.callsign} | TS: ${pos} | Up: ${uptime}`;
        }
    }

    const pollers = modules.createPollerManager({
        store,
        onStats: updateTopBarStats,
        onConnection: updateConnectionStatus,
    });

    function invokeViewLifecycle(prev, view) {
        if (typeof HydraOps !== 'undefined' && prev !== view) {
            if (view === 'ops') HydraOps.onEnter();
            if (prev === 'ops') HydraOps.onLeave();
        }
        if (typeof HydraOperations !== 'undefined' && prev !== view) {
            if (view === 'config') HydraOperations.onEnter();
            if (prev === 'config') HydraOperations.onLeave();
        }
        if (typeof HydraSettings !== 'undefined' && prev !== view) {
            if (view === 'settings') HydraSettings.onEnter();
            if (prev === 'settings') HydraSettings.onLeave();
        }
        if (typeof HydraTak !== 'undefined' && prev !== view) {
            if (view === 'tak') HydraTak.onEnter();
            if (prev === 'tak') HydraTak.onLeave();
        }
    }

    const router = modules.createViewRouter({
        store,
        onViewLifecycle: invokeViewLifecycle,
        onViewChanged: (view) => {
            stream.syncForView(view);
            pollers.updatePollers(view);
        },
    });

    function toggleLowBandwidth() {
        lowBandwidthMode = !lowBandwidthMode;
        const btn = document.getElementById('bandwidth-toggle');
        if (btn) btn.classList.toggle('active', lowBandwidthMode);
        api.apiPost('/api/stream/quality', { quality: lowBandwidthMode ? 30 : 70 });
    }

    function initBandwidthToggle() {
        const btn = document.getElementById('bandwidth-toggle');
        if (!btn) return;
        btn.addEventListener('click', toggleLowBandwidth);
    }

    async function initLogoutButton() {
        const btn = document.getElementById('footer-logout');
        if (!btn) return;
        try {
            const resp = await fetch('/auth/status', { credentials: 'same-origin' });
            if (resp.ok) {
                const data = await resp.json();
                if (data && data.password_enabled && data.authenticated) btn.style.display = '';
            }
        } catch (e) {}

        btn.addEventListener('click', async () => {
            try {
                await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' });
            } catch (e) {}
            window.location.href = '/login';
        });
    }

    function init() {
        preflight.runPreflight();
        modal.initEscapeAndTrap();
        stream.initStreamWatcher();
        router.initRouter();
        // Defer initial view enter until all scripts are loaded
        setTimeout(function() {
            var v = store.getState().currentView;
            if (v === 'ops' && typeof HydraOps !== 'undefined') HydraOps.onEnter();
            else if (v === 'config' && typeof HydraOperations !== 'undefined') HydraOperations.onEnter();
            else if (v === 'settings' && typeof HydraSettings !== 'undefined') HydraSettings.onEnter();
            else if (v === 'tak' && typeof HydraTak !== 'undefined') HydraTak.onEnter();
            stream.resumeStream();
        }, 0);
        initBandwidthToggle();
        initLogoutButton();
        pollers.updatePollers(store.getState().currentView);
    }

    const hydraApp = {
        state: store.getState().data,
        currentView: () => store.getState().currentView,
        switchView: router.switchView,
        showToast: toast.showToast,
        apiPost: api.apiPost,
        apiGet: api.apiGet,
        authHeaders: api.authHeaders,
        setApiToken: store.setApiToken,
        toggleLowBandwidth,
        toggleFullscreen: stream.toggleFullscreen,
        runPreflight: preflight.runPreflight,
        dismissPreflight: preflight.dismissPreflight,
        openModal: modal.openModal,
        closeModal: modal.closeModal,
        closeActiveModal: modal.closeActiveModal,
        subscribe: store.subscribe,
    };

    window.HydraApp = hydraApp;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
