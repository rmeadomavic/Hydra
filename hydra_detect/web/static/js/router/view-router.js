'use strict';

window.HydraModules = window.HydraModules || {};

window.HydraModules.createViewRouter = function createViewRouter({ store, onViewLifecycle, onViewChanged }) {
    const VALID_VIEWS = ['ops', 'config', 'settings', 'tak'];
    // Autonomy and Systems were folded into Config and Settings respectively.
    // Aliases keep old bookmarks (#autonomy, #systems) and external links working.
    const VIEW_ALIASES = {
        'operations': 'config',
        'autonomy': 'config',
        'systems': 'settings',
    };

    function normalizeView(raw) {
        const hash = (raw || '').replace('#', '') || 'ops';
        const aliased = VIEW_ALIASES[hash] || hash;
        return VALID_VIEWS.includes(aliased) ? aliased : 'ops';
    }

    function applyView(view) {
        const prev = store.getState().currentView;
        store.setCurrentView(view);

        VALID_VIEWS.forEach(v => document.body.classList.remove(`view-${v}`));
        document.body.classList.add(`view-${view}`);

        document.querySelectorAll('.topbar-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.view === view);
        });

        if (prev !== view && onViewLifecycle) onViewLifecycle(prev, view);
        if (onViewChanged) onViewChanged(view, prev);
    }

    function switchView(view) {
        applyView(normalizeView(view));
    }

    function onHashChange() {
        switchView(window.location.hash);
    }

    function initRouter() {
        window.addEventListener('hashchange', onHashChange);
        switchView(window.location.hash);

        document.querySelectorAll('.topbar-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                window.location.hash = tab.dataset.view;
            });
        });

        const thumb = document.getElementById('mini-thumbnail');
        if (thumb) {
            thumb.addEventListener('click', () => {
                window.location.hash = 'ops';
            });
        }
    }

    return {
        initRouter,
        switchView,
        normalizeView,
    };
};
