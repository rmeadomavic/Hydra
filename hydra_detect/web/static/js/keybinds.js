/**
 * Hydra Detect v2.0 — Global power-user keyboard shortcuts.
 *
 * Self-attaching IIFE. Runs on capture phase so overlays can intercept
 * Escape before in-view handlers. Deliberately avoids arrow keys so the
 * Konami listener in easter.js keeps exclusive reign over those.
 *
 * Shortcuts:
 *   1..6       switch views (ops, tak, systems, autonomy, config, settings)
 *   ?          toggle the #keybinds-help overlay
 *   /          focus the current view's .view-search input (if any)
 *   Escape     close any open overlay (help, command palette, modals, preflight)
 *   Ctrl/Cmd+K handled by command-palette.js (not here)
 *
 * Skipped while focus is on INPUT / TEXTAREA / SELECT (except Escape).
 */

'use strict';

(function () {
    const VIEW_ORDER = ['ops', 'tak', 'systems', 'autonomy', 'config', 'settings'];

    function isEditingTarget() {
        const ae = document.activeElement;
        if (!ae) return false;
        if (['INPUT', 'TEXTAREA', 'SELECT'].includes(ae.tagName)) return true;
        if (ae.isContentEditable) return true;
        return false;
    }

    function switchView(view) {
        if (window.HydraApp && typeof window.HydraApp.switchView === 'function') {
            window.HydraApp.switchView(view);
            window.location.hash = view;
            return;
        }
        window.location.hash = view;
    }

    function toggleHelpOverlay() {
        const overlay = document.getElementById('keybinds-help');
        if (!overlay) return;
        const open = overlay.style.display === 'flex';
        if (open) closeOverlay(overlay);
        else openOverlay(overlay);
    }

    function openOverlay(el) {
        el.style.display = 'flex';
        void el.offsetWidth;
        el.classList.add('active');
    }

    function closeOverlay(el) {
        el.classList.remove('active');
        el.style.display = 'none';
    }

    function focusViewSearch() {
        const currentView = (window.HydraApp && typeof window.HydraApp.currentView === 'function')
            ? window.HydraApp.currentView()
            : 'ops';
        const selectors = [
            `#view-${currentView} .view-search`,
            `#view-${currentView} input[data-role="view-search"]`,
            `#view-${currentView} input[type="search"]`,
        ];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && typeof el.focus === 'function') {
                el.focus();
                if (typeof el.select === 'function') el.select();
                return true;
            }
        }
        return false;
    }

    function closeAnyOpenOverlay() {
        let closed = false;

        const cmd = document.getElementById('hydra-command-palette');
        if (cmd && cmd.style.display === 'flex') {
            if (window.HydraCommandPalette && typeof window.HydraCommandPalette.close === 'function') {
                window.HydraCommandPalette.close();
            } else {
                closeOverlay(cmd);
            }
            closed = true;
        }

        const help = document.getElementById('keybinds-help');
        if (help && help.style.display === 'flex') {
            closeOverlay(help);
            closed = true;
        }

        document.querySelectorAll('.modal-overlay.active').forEach(m => {
            m.classList.remove('active');
            closed = true;
        });

        const preflight = document.getElementById('preflight-overlay');
        if (preflight && preflight.style.display && preflight.style.display !== 'none') {
            preflight.style.display = 'none';
            closed = true;
        }

        return closed;
    }

    function onKeydownCapture(e) {
        if (e.key === 'Escape') {
            if (closeAnyOpenOverlay()) {
                e.preventDefault();
                e.stopPropagation();
            }
            return;
        }

        if (isEditingTarget()) return;
        if (e.altKey || e.metaKey || e.ctrlKey) return;

        if (e.key >= '1' && e.key <= '6') {
            const idx = parseInt(e.key, 10) - 1;
            const view = VIEW_ORDER[idx];
            if (view) {
                switchView(view);
                e.preventDefault();
            }
            return;
        }

        if (e.key === '?' || (e.key === '/' && e.shiftKey)) {
            toggleHelpOverlay();
            e.preventDefault();
            return;
        }

        if (e.key === '/') {
            if (focusViewSearch()) {
                e.preventDefault();
            }
            return;
        }
    }

    document.addEventListener('keydown', onKeydownCapture, true);

    const helpCard = document.getElementById('keybinds-help');
    if (helpCard) {
        helpCard.addEventListener('click', function (e) {
            if (e.target === helpCard) closeOverlay(helpCard);
        });
        const closeBtn = helpCard.querySelector('[data-role="keybinds-close"]');
        if (closeBtn) closeBtn.addEventListener('click', () => closeOverlay(helpCard));
    }

    window.HydraKeybinds = {
        version: '1.0.0-power-ux',
        attached: true,
        views: VIEW_ORDER.slice(),
        toggleHelp: toggleHelpOverlay,
        closeOverlays: closeAnyOpenOverlay,
    };
})();
