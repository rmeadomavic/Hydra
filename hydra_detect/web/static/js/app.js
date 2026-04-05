'use strict';

// Legacy shim: app lifecycle now lives in /static/js/main.js and modules/*.
// This file intentionally keeps no implementation logic.
if (!window.HydraApp) {
    console.warn('HydraApp bootstrap moved to /static/js/main.js.');
}
