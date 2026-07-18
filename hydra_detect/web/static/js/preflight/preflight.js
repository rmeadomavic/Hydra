'use strict';

window.HydraModules = window.HydraModules || {};

window.HydraModules.createPreflight = function createPreflight({ fetchImpl }) {
    const fetcher = fetchImpl || fetch;

    async function runPreflight() {
        try {
            const resp = await fetcher('/api/preflight');
            if (!resp.ok) return;
            const data = await resp.json();
            if (data.overall === 'fail') {
                showPreflightOverlay(data.checks, true);
            } else if (data.overall === 'warn') {
                // Gate the WARNING modal to once per tab session (operator request).
                // Cleared by tab close or Ctrl+Shift+R.
                if (sessionStorage.getItem('hydra-preflight-dismissed') === '1') return;
                showPreflightOverlay(data.checks, false);
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
        while (overlay.firstChild) overlay.removeChild(overlay.firstChild);

        const card = document.createElement('div');
        card.className = 'preflight-card';
        const title = document.createElement('h2');
        title.className = 'preflight-title ' + (blocking ? 'fail' : 'warn');
        title.textContent = blocking ? 'PRE-FLIGHT FAILED' : 'PRE-FLIGHT WARNING';
        card.appendChild(title);

        const checksDiv = document.createElement('div');
        checksDiv.className = 'preflight-checks';
        checks.forEach(c => {
            const item = document.createElement('div');
            item.className = 'preflight-item preflight-' + c.status;
            item.innerHTML = `<span class="preflight-icon">${c.status === 'pass' ? '✓' : c.status === 'warn' ? '⚠' : '✗'}</span><span class="preflight-name"></span><span class="preflight-msg"></span>`;
            item.querySelector('.preflight-name').textContent = c.name;
            item.querySelector('.preflight-msg').textContent = c.message;
            checksDiv.appendChild(item);
        });
        card.appendChild(checksDiv);

        const btn = document.createElement('button');
        btn.className = 'preflight-btn';
        btn.textContent = blocking ? 'Re-check' : 'Continue';
        btn.addEventListener('click', blocking ? runPreflight : function() {
            sessionStorage.setItem('hydra-preflight-dismissed', '1');
            dismissPreflight();
        });
        card.appendChild(btn);

        overlay.appendChild(card);
        overlay.style.display = 'flex';
    }

    function dismissPreflight() {
        const overlay = document.getElementById('preflight-overlay');
        if (overlay) overlay.style.display = 'none';
    }

    // Issue #295: gate an action (Start Sortie) on preflight. Returns a
    // promise resolving true = proceed, false = operator aborted. On 'fail'
    // it forces an explicit confirm listing the failures; on 'warn' it
    // confirms with the warnings; on 'pass' (or an unreachable endpoint —
    // never block the mission on a preflight outage) it resolves true.
    async function gateAction(actionLabel) {
        let data;
        try {
            const resp = await fetcher('/api/preflight');
            if (!resp.ok) return true; // endpoint down → do not block the sortie
            data = await resp.json();
        } catch (e) {
            return true;
        }
        const overall = data && data.overall;
        if (overall !== 'fail' && overall !== 'warn') return true;

        const checks = Array.isArray(data.checks) ? data.checks : [];
        const bad = checks.filter(c => c && (c.status === 'fail' || c.status === 'warn'));
        const lines = bad.map(c =>
            (c.status === 'fail' ? '✗ ' : '⚠ ') + c.name + (c.message ? ': ' + c.message : '')
        ).join('\n');
        const header = overall === 'fail'
            ? 'PRE-FLIGHT FAILED — ' + actionLabel + ' anyway?\n\n'
            : 'Pre-flight warnings — ' + actionLabel + ' anyway?\n\n';
        // eslint-disable-next-line no-alert
        return window.confirm(header + lines);
    }

    return {
        runPreflight,
        dismissPreflight,
        showPreflightOverlay,
        gateAction,
    };
};
