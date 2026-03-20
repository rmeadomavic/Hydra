'use strict';

/**
 * Hydra Detect v2.0 — Panel System (SortableJS + localStorage persistence)
 *
 * Manages drag-reorder, collapse/expand, and panel visibility for the
 * Control view's right-side panel area.
 */
const HydraPanels = (() => {
    const KNOWN_IDS = ['vehicle', 'target', 'pipeline', 'detection', 'rf', 'log'];
    const STORAGE_PREFIX = 'hydra-panels-';
    let sortableInstance = null;
    let initialized = false;

    // ── Breakpoint helper ──
    function getBreakpoint() {
        return window.innerWidth >= 1280 ? 'desktop' : 'compact';
    }

    function storageKey() {
        return STORAGE_PREFIX + getBreakpoint();
    }

    // ── Initialization ──
    function init() {
        const container = document.getElementById('operations-panels');
        if (!container) return;
        if (initialized) return;
        initialized = true;

        // Init SortableJS
        if (typeof Sortable !== 'undefined') {
            sortableInstance = Sortable.create(container, {
                handle: '.panel-drag-handle',
                animation: 150,
                ghostClass: 'sortable-ghost',
                dragClass: 'sortable-drag',
                onEnd: function () {
                    saveLayout();
                },
            });
        }

        // Wire collapse buttons
        container.querySelectorAll('.panel-collapse-btn').forEach(btn => {
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                const panel = btn.closest('.panel');
                if (panel) {
                    panel.classList.toggle('collapsed');
                    saveLayout();
                }
            });
        });

        // Make entire header clickable to expand collapsed panels
        container.querySelectorAll('.panel-header').forEach(header => {
            header.style.cursor = 'pointer';
            header.addEventListener('click', function (e) {
                // Don't interfere with the collapse button or drag handle
                if (e.target.closest('.panel-collapse-btn') || e.target.closest('.panel-drag-handle')) return;
                const panel = header.closest('.panel');
                if (panel) {
                    panel.classList.toggle('collapsed');
                    saveLayout();
                }
            });
        });

        // Wire visibility menu
        initVisibilityMenu();

        // Load saved layout
        loadLayout();
    }

    // ── Visibility Menu ──
    function initVisibilityMenu() {
        const btn = document.getElementById('panel-visibility-btn');
        const menu = document.getElementById('panel-visibility-menu');
        if (!btn || !menu) return;

        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            menu.classList.toggle('open');
        });

        // Close menu on outside click
        document.addEventListener('click', function (e) {
            if (!menu.contains(e.target) && e.target !== btn) {
                menu.classList.remove('open');
            }
        });

        // Wire checkbox changes
        menu.querySelectorAll('.panel-vis-row').forEach(row => {
            const cb = row.querySelector('input[type="checkbox"]');
            const targetId = row.dataset.target;
            if (!cb || !targetId) return;

            cb.addEventListener('change', function () {
                const panel = document.getElementById('panel-' + targetId);
                if (panel) {
                    panel.classList.toggle('hidden', !cb.checked);
                    saveLayout();
                }
            });
        });
    }

    // ── Save Layout ──
    function saveLayout() {
        const container = document.getElementById('operations-panels');
        if (!container) return;

        const panels = container.querySelectorAll('.panel');
        const layout = [];

        panels.forEach(panel => {
            const id = panel.dataset.panelId;
            if (!id || !KNOWN_IDS.includes(id)) return;
            layout.push({
                id: id,
                collapsed: panel.classList.contains('collapsed'),
                visible: !panel.classList.contains('hidden'),
            });
        });

        try {
            localStorage.setItem(storageKey(), JSON.stringify(layout));
        } catch (e) {
            // localStorage full or unavailable — ignore silently
        }
    }

    // ── Load Layout ──
    function loadLayout() {
        const container = document.getElementById('operations-panels');
        if (!container) return;

        migrateOldKeys();

        let layout;
        try {
            const raw = localStorage.getItem(storageKey());
            if (!raw) {
                applyTierDefaults(container);
                return;
            }
            layout = JSON.parse(raw);
        } catch (e) {
            applyTierDefaults(container);
            return;
        }

        if (!Array.isArray(layout) || layout.length === 0) {
            applyTierDefaults(container);
            return;
        }

        const valid = layout.filter(item => item && KNOWN_IDS.includes(item.id));
        if (valid.length === 0) {
            applyTierDefaults(container);
            return;
        }

        const panelMap = {};
        container.querySelectorAll('.panel').forEach(p => {
            panelMap[p.dataset.panelId] = p;
        });

        valid.forEach(item => {
            const panel = panelMap[item.id];
            if (!panel) return;
            panel.classList.toggle('collapsed', !!item.collapsed);
            panel.classList.toggle('hidden', item.visible === false);
            container.appendChild(panel);
            delete panelMap[item.id];
        });

        Object.values(panelMap).forEach(panel => {
            container.appendChild(panel);
        });

        syncVisibilityCheckboxes();
    }

    function migrateOldKeys() {
        const newKey = storageKey();
        try {
            if (localStorage.getItem(newKey)) return;
            localStorage.removeItem('hydra-panels-desktop');
            localStorage.removeItem('hydra-panels-compact');
        } catch (e) {}
    }

    function applyTierDefaults(container) {
        const tier3 = ['detection', 'log'];
        container.querySelectorAll('.panel').forEach(panel => {
            const id = panel.dataset.panelId;
            if (tier3.includes(id)) {
                panel.classList.add('collapsed');
            }
        });
    }

    // ── Sync visibility checkboxes to current panel state ──
    function syncVisibilityCheckboxes() {
        const menu = document.getElementById('panel-visibility-menu');
        if (!menu) return;

        menu.querySelectorAll('.panel-vis-row').forEach(row => {
            const cb = row.querySelector('input[type="checkbox"]');
            const targetId = row.dataset.target;
            if (!cb || !targetId) return;

            const panel = document.getElementById('panel-' + targetId);
            if (panel) {
                cb.checked = !panel.classList.contains('hidden');
            }
        });
    }

    // ── Public API ──
    return {
        init,
        saveLayout,
        loadLayout,
    };
})();
