'use strict';

window.HydraModules = window.HydraModules || {};

window.HydraModules.createStore = function createStore(initialState) {
    const listeners = new Set();
    const state = {
        currentView: 'ops',
        apiToken: sessionStorage.getItem('hydra_token') || '',
        data: {
            stats: {},
            tracks: [],
            target: { locked: false },
            detections: [],
            rfStatus: { state: 'unavailable' },
        },
        ...initialState,
    };

    function getState() {
        return state;
    }

    function updateData(partial) {
        Object.assign(state.data, partial);
        listeners.forEach(fn => fn(state));
    }

    function setCurrentView(view) {
        if (state.currentView === view) return;
        state.currentView = view;
        listeners.forEach(fn => fn(state));
    }

    function setApiToken(token) {
        state.apiToken = token || '';
        sessionStorage.setItem('hydra_token', state.apiToken);
    }

    function subscribe(listener) {
        listeners.add(listener);
        return () => listeners.delete(listener);
    }

    return {
        getState,
        updateData,
        setCurrentView,
        setApiToken,
        subscribe,
    };
};
