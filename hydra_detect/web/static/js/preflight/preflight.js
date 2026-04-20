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

    return {
        runPreflight,
        dismissPreflight,
        showPreflightOverlay,
    };
};
