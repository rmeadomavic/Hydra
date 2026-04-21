'use strict';

window.HydraModules = window.HydraModules || {};

window.HydraModules.createApiClient = function createApiClient({ store, toast, promptFn, fetchImpl }) {
    const fetcher = fetchImpl || fetch;
    const promptToken = promptFn || (() => prompt('API token required.\nEnter the api_token from config.ini:'));

    function authHeaders() {
        const h = { 'Content-Type': 'application/json' };
        const token = store.getState().apiToken;
        if (token) h.Authorization = `Bearer ${token}`;
        return h;
    }

    function promptForToken() {
        const token = promptToken();
        if (!token) return false;
        store.setApiToken(token.trim());
        return true;
    }

    async function apiPost(url, body) {
        try {
            let resp = await fetcher(url, {
                method: 'POST',
                headers: authHeaders(),
                body: JSON.stringify(body),
            });
            if (resp.status === 401 && resp.headers.get('x-login-required')) {
                window.location.href = '/login';
                return null;
            }
            if (resp.status === 401 && promptForToken()) {
                resp = await fetcher(url, {
                    method: 'POST',
                    headers: authHeaders(),
                    body: JSON.stringify(body),
                });
            }
            const data = await resp.json();
            if (!resp.ok) {
                // Surface validation field_errors inline so the user knows
                // which field is wrong instead of a generic "Validation
                // failed" toast that sends them hunting through 70+ fields.
                let msg = data.error || `Request failed (${resp.status})`;
                if (data.field_errors && typeof data.field_errors === 'object') {
                    const parts = Object.entries(data.field_errors)
                        .map(([k, v]) => `${k}: ${v}`);
                    if (parts.length > 0) {
                        msg = (data.error ? data.error + ' — ' : '') +
                              parts.slice(0, 4).join('; ') +
                              (parts.length > 4 ? ` (+${parts.length - 4} more)` : '');
                    }
                }
                toast.showToast(msg, 'error');
                return null;
            }
            return data;
        } catch (e) {
            toast.showToast('Network error — check connection', 'error');
            return null;
        }
    }

    async function apiGet(url) {
        try {
            const resp = await fetcher(url, { headers: authHeaders() });
            if (!resp.ok) return null;
            return await resp.json();
        } catch (e) {
            return null;
        }
    }

    return {
        authHeaders,
        apiGet,
        apiPost,
        promptForToken,
    };
};
