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

    function setDotClass(el, tone) {
        if (!el) return;
        el.className = 'tb-dot ' + tone;
    }

    function gpsBlipMeta(mavConnected, fix) {
        if (!mavConnected) return { label: 'GPS --', tone: 'dim' };
        if (fix === 0) return { label: 'GPS No Fix', tone: 'red' };
        if (fix === 2) return { label: 'GPS 2D', tone: 'amber' };
        if (fix === 3) return { label: 'GPS 3D', tone: 'olive' };
        return { label: 'GPS --', tone: 'dim' };
    }

    function updateTopBarStats(data) {
        const fpsEl = document.getElementById('fps-display');
        if (fpsEl) fpsEl.textContent = `${(data.fps || 0).toFixed(1)}`;

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

        // ── Topbar health blips — mock mapping:
        //    green/olive → good, amber → degraded, red → fault, dim → unknown.
        const dotCam = document.getElementById('dot-camera');
        const dotMav = document.getElementById('dot-mavlink');
        const dotGps = document.getElementById('dot-gps');
        const dotSim = document.getElementById('dot-sim');
        const dotKis = document.getElementById('dot-kismet');
        const dotTak = document.getElementById('dot-tak');
        const gpsLabel = document.getElementById('tb-gps-label');

        setDotClass(dotCam, data.camera_ok ? 'olive' : 'red');
        setDotClass(dotMav, data.mavlink ? 'olive' : 'red');
        const gpsMeta = gpsBlipMeta(!!data.mavlink, data.gps_fix);
        setDotClass(dotGps, gpsMeta.tone);
        if (gpsLabel) gpsLabel.textContent = gpsMeta.label;
        setDotClass(dotSim, data.is_sim_gps ? 'yellow' : 'dim');
        // Kismet / TAK blips: stats fields not always present — fall back to dim.
        const kisOk = data.kismet_running || data.kismet_connected;
        const takOk = data.tak_running || data.tak_enabled;
        setDotClass(dotKis, kisOk ? 'olive' : (data.kismet_running === false ? 'red' : 'dim'));
        setDotClass(dotTak, takOk ? 'olive' : (data.tak_running === false ? 'red' : 'dim'));

        // SIM pill (amber) — visible only when simulated GPS is active.
        const simPill = document.getElementById('sim-gps-pill');
        if (simPill) {
            if (data.is_sim_gps) simPill.removeAttribute('hidden');
            else simPill.setAttribute('hidden', '');
        }

        // Latency readout — prefer mavlink latency, fall back to inference_ms.
        const latEl = document.getElementById('tb-latency-value');
        if (latEl) {
            const ms = data.mavlink_latency_ms != null
                ? data.mavlink_latency_ms
                : (data.inference_ms != null ? data.inference_ms : null);
            latEl.textContent = ms == null ? '--' : `${Math.round(ms)}`;
        }

        // CS chip mirrors callsign; PLT chip mirrors platform if provided.
        const csEl = document.getElementById('tb-cs-value');
        if (csEl && data.callsign) csEl.textContent = data.callsign;
        const pltEl = document.getElementById('tb-plt-value');
        if (pltEl && data.platform) pltEl.textContent = String(data.platform).toUpperCase();

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
        if (typeof HydraSystems !== 'undefined' && prev !== view) {
            if (view === 'systems') HydraSystems.onEnter();
            if (prev === 'systems') HydraSystems.onLeave();
        }
        if (typeof HydraAutonomy !== 'undefined' && prev !== view) {
            if (view === 'autonomy') HydraAutonomy.onEnter();
            if (prev === 'autonomy') HydraAutonomy.onLeave();
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

    // Emergency abort wiring. POST /api/abort and raise body[data-emerg="1"]
    // so the topbar ABORT button + fullscreen border both pulse red.
    function initAbortButton() {
        const btn = document.getElementById('tb-abort');
        if (!btn) return;
        btn.addEventListener('click', async () => {
            document.body.setAttribute('data-emerg', '1');
            try {
                await api.apiPost('/api/abort', {});
                toast.showToast('EMERGENCY ABORT sent', 'error');
            } catch (e) {
                toast.showToast('Abort POST failed', 'error');
            }
        });
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
            else if (v === 'systems' && typeof HydraSystems !== 'undefined') HydraSystems.onEnter();
            else if (v === 'autonomy' && typeof HydraAutonomy !== 'undefined') HydraAutonomy.onEnter();
            stream.resumeStream();
        }, 0);
        initBandwidthToggle();
        initAbortButton();
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
