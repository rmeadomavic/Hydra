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

    // ── Breakpoint helper ──
    function getBreakpoint() {
        return window.innerWidth >= 1280 ? 'desktop' : 'compact';
    }

    function storageKey() {
        return STORAGE_PREFIX + getBreakpoint();
    }

    // ── Initialization ──
    function init() {
        const container = document.getElementById('control-panels');
        if (!container) return;

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
        const container = document.getElementById('control-panels');
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
        const container = document.getElementById('control-panels');
        if (!container) return;

        let layout;
        try {
            const raw = localStorage.getItem(storageKey());
            if (!raw) return;
            layout = JSON.parse(raw);
        } catch (e) {
            return;
        }

        if (!Array.isArray(layout) || layout.length === 0) return;

        // Validate: only keep entries with known IDs
        const valid = layout.filter(item => item && KNOWN_IDS.includes(item.id));
        if (valid.length === 0) return;

        // Reorder panels in the DOM
        const panelMap = {};
        container.querySelectorAll('.panel').forEach(p => {
            panelMap[p.dataset.panelId] = p;
        });

        // Append panels in saved order
        valid.forEach(item => {
            const panel = panelMap[item.id];
            if (!panel) return;

            // Apply collapsed state
            panel.classList.toggle('collapsed', !!item.collapsed);

            // Apply visibility state
            panel.classList.toggle('hidden', item.visible === false);

            // Move to correct order
            container.appendChild(panel);

            delete panelMap[item.id];
        });

        // Any panels not in saved layout get appended at end (new panels added later)
        Object.values(panelMap).forEach(panel => {
            container.appendChild(panel);
        });

        // Sync visibility menu checkboxes
        syncVisibilityCheckboxes();
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
