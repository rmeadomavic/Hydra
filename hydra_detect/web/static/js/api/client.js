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
                toast.showToast(data.error || `Request failed (${resp.status})`);
                return null;
            }
            return data;
        } catch (e) {
            toast.showToast('Network error — check connection');
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
