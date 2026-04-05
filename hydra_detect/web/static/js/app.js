'use strict';

// Compatibility bridge:
// Keep app.js present during module migration for tests/integrations that
// still reference this path directly.
(function bindLegacyBandwidthToggle() {
    function toggleLowBandwidth() {
        if (window.HydraApp && typeof window.HydraApp.toggleLowBandwidth === 'function') {
            window.HydraApp.toggleLowBandwidth();
        }
    }

    function attachListener() {
        const btn = document.getElementById('bandwidth-toggle');
        if (!btn) return;
        btn.addEventListener('click', toggleLowBandwidth);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', attachListener);
    } else {
        attachListener();
    }
})();
