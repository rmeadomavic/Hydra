'use strict';

window.HydraModules = window.HydraModules || {};

window.HydraModules.createPollerManager = function createPollerManager({ store, fetchImpl, onStats, onConnection }) {
    const fetcher = fetchImpl || fetch;
    const pollers = {};
    const MAX_BACKOFF = 10000;

    function startPoller(name, url, intervalMs, update) {
        if (pollers[name]) clearTimeout(pollers[name].timer);
        // failCount is per-poller: one failing endpoint must not slow the
        // healthy ones, and a success on a healthy endpoint must not reset
        // the backoff of one that is still failing.
        const entry = { baseInterval: intervalMs, timer: null, failCount: 0 };
        pollers[name] = entry;

        const schedule = () => {
            const delay = entry.failCount === 0
                ? intervalMs
                : Math.min(intervalMs * Math.pow(2, entry.failCount), MAX_BACKOFF);
            entry.timer = setTimeout(poll, delay);
        };

        const poll = async () => {
            try {
                const resp = await fetcher(url);
                if (resp.ok) {
                    const data = await resp.json();
                    update(data);
                    entry.failCount = 0;
                    if (onConnection) onConnection(true);
                } else {
                    entry.failCount++;
                    if (onConnection) onConnection(false);
                }
            } catch (e) {
                entry.failCount++;
                if (onConnection) onConnection(false);
            }
            if (pollers[name]) schedule();
        };

        poll();
    }

    function stopPoller(name) {
        if (!pollers[name]) return;
        clearTimeout(pollers[name].timer);
        delete pollers[name];
    }

    function updatePollers(currentView) {
        if (!pollers.stats) {
            startPoller('stats', '/api/stats', 2000, data => {
                store.updateData({ stats: data });
                if (onStats) onStats(data);
            });
        }

        const needsDetail = currentView === 'ops' || currentView === 'config';
        if (needsDetail && !pollers.tracks) {
            startPoller('tracks', '/api/tracks', 1000, data => store.updateData({ tracks: data }));
            startPoller('target', '/api/target', 1000, data => store.updateData({ target: data }));
            startPoller('rf', '/api/rf/status', 2000, data => store.updateData({ rfStatus: data }));
            startPoller('detections', '/api/detections', 3000, data => store.updateData({ detections: data }));
        } else if (!needsDetail) {
            stopPoller('tracks');
            stopPoller('target');
            stopPoller('rf');
            stopPoller('detections');
        }
    }

    function getActivePollers() {
        return Object.keys(pollers);
    }

    return {
        startPoller,
        stopPoller,
        updatePollers,
        getActivePollers,
    };
};
