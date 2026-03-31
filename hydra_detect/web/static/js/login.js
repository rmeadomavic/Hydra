/**
 * Hydra Detect v2.0 — Login Page
 */

'use strict';

(() => {
    const form = document.getElementById('login-form');
    const input = document.getElementById('password');
    const btn = document.getElementById('login-btn');
    const errorEl = document.getElementById('login-error');

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const password = input.value;
        if (!password) return;

        btn.disabled = true;
        errorEl.textContent = '';

        try {
            const resp = await fetch('/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password }),
            });

            if (resp.ok) {
                window.location.href = '/';
                return;
            }

            const data = await resp.json().catch(() => ({}));
            if (resp.status === 429) {
                errorEl.textContent = 'Too many attempts — try again later.';
            } else {
                errorEl.textContent = data.error || 'Wrong password.';
            }
        } catch {
            errorEl.textContent = 'Connection error — check network.';
        }

        btn.disabled = false;
        input.select();
    });

    input.focus();
})();
