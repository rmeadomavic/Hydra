'use strict';

window.HydraSimGps = (function () {
    let lastFlag = false;
    let store = null;

    function isSim() {
        if (!store) return false;
        const s = store.getState();
        return Boolean(s && s.data && s.data.stats && s.data.stats.is_sim_gps);
    }

    function withSimSuffix(str) {
        if (str == null || str === '' || str === '--') return str;
        return isSim() ? (str + ' (SIM)') : str;
    }

    function renderPill(isOn) {
        const el = document.getElementById('sim-gps-pill');
        if (!el) return;
        el.hidden = !isOn;
    }

    function init(storeRef) {
        store = storeRef;
        store.subscribe(() => {
            const flag = isSim();
            if (flag === lastFlag) return;
            lastFlag = flag;
            renderPill(flag);
        });
        lastFlag = isSim();
        renderPill(lastFlag);
    }

    return { init, isSim, withSimSuffix };
})();
