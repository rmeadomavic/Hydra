/**
 * Hydra Detect v2.0 — Command palette (Ctrl+K / Cmd+K).
 *
 * Self-attaching IIFE. Opens a centered overlay with a text input + live
 * substring-filtered list. Sources:
 *   Actions — tab switches, toggle help, toggle Konami sentience nod.
 *   Tracks  — HydraApp.state.tracks (Track #N class (conf%)).
 *   Peers   — HydraApp.state.peers  (Peer callsign (uid)).
 *
 * Always stateless across opens (empty input, full list rebuild). Arrow keys
 * navigate, Enter selects, Escape closes. Does not intercept arrow keys when
 * closed — Konami keeps arrow-key exclusivity.
 */

'use strict';

(function () {
    const PALETTE_ID = 'hydra-command-palette';
    const INPUT_ID = 'hydra-command-palette-input';
    const LIST_ID = 'hydra-command-palette-list';

    let palette = null;
    let input = null;
    let list = null;
    let items = [];
    let selectedIdx = 0;
    let isOpen = false;

    function isMac() {
        return /Mac|iPhone|iPad/.test(navigator.platform);
    }

    function currentState() {
        if (window.HydraApp && window.HydraApp.state) return window.HydraApp.state;
        return { tracks: [], peers: [] };
    }

    function buildActionItems() {
        const switchView = (v) => {
            if (window.HydraApp && typeof window.HydraApp.switchView === 'function') {
                window.HydraApp.switchView(v);
            }
            window.location.hash = v;
        };
        return [
            { label: 'Switch to Ops',      hint: 'view · 1', kind: 'action', run: () => switchView('ops') },
            { label: 'Switch to TAK',      hint: 'view · 2', kind: 'action', run: () => switchView('tak') },
            { label: 'Switch to Systems',  hint: 'view · 3', kind: 'action', run: () => switchView('systems') },
            { label: 'Switch to Autonomy', hint: 'view · 4', kind: 'action', run: () => switchView('autonomy') },
            { label: 'Switch to Config',   hint: 'view · 5', kind: 'action', run: () => switchView('config') },
            { label: 'Switch to Settings', hint: 'view · 6', kind: 'action', run: () => switchView('settings') },
            { label: 'Show help (?)',      hint: 'action',   kind: 'action', run: () => {
                if (window.HydraKeybinds && typeof window.HydraKeybinds.toggleHelp === 'function') {
                    window.HydraKeybinds.toggleHelp();
                }
            }},
            { label: 'Toggle Konami sentience', hint: 'easter', kind: 'action', run: () => {
                if (window.HydraApp && typeof window.HydraApp.showToast === 'function') {
                    window.HydraApp.showToast('Try: ↑ ↑ ↓ ↓ ← → ← → B A', 'info');
                }
            }},
        ];
    }

    function buildTrackItems() {
        const state = currentState();
        const tracks = Array.isArray(state.tracks) ? state.tracks : [];
        return tracks.map(t => {
            const tid = t.track_id != null ? t.track_id : (t.id != null ? t.id : '?');
            const cls = String(t.label || t.class || t.cls || 'unknown');
            const conf = typeof t.confidence === 'number' ? t.confidence
                       : (typeof t.conf === 'number' ? t.conf : null);
            const pct = conf == null ? '--' : `${Math.round(conf * 100)}%`;
            return {
                label: `Track #${tid} ${cls} (${pct})`,
                hint: 'track',
                kind: 'track',
                run: () => {
                    if (window.HydraApp && typeof window.HydraApp.switchView === 'function') {
                        window.HydraApp.switchView('ops');
                    }
                    window.location.hash = 'ops';
                },
            };
        });
    }

    function buildPeerItems() {
        const state = currentState();
        const peers = Array.isArray(state.peers) ? state.peers : [];
        return peers.map(p => {
            const callsign = String(p.callsign || p.name || p.uid || 'peer');
            const uid = String(p.uid || p.id || '--');
            return {
                label: `Peer ${callsign} (${uid})`,
                hint: 'peer',
                kind: 'peer',
                run: () => {
                    if (window.HydraApp && typeof window.HydraApp.switchView === 'function') {
                        window.HydraApp.switchView('tak');
                    }
                    window.location.hash = 'tak';
                },
            };
        });
    }

    function collectItems() {
        return buildActionItems()
            .concat(buildTrackItems())
            .concat(buildPeerItems());
    }

    function filterItems(all, query) {
        const q = (query || '').trim().toLowerCase();
        if (!q) return all.slice();
        return all.filter(it => it.label.toLowerCase().includes(q)
                             || it.hint.toLowerCase().includes(q));
    }

    function renderList(visible) {
        if (!list) return;
        list.textContent = '';
        if (visible.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'cmd-palette-empty';
            empty.textContent = 'No matches';
            list.appendChild(empty);
            return;
        }
        visible.forEach((it, idx) => {
            const row = document.createElement('div');
            row.className = 'cmd-palette-row';
            if (idx === selectedIdx) row.classList.add('selected');
            row.dataset.idx = String(idx);

            const label = document.createElement('span');
            label.className = 'cmd-palette-label';
            label.textContent = it.label;

            const hint = document.createElement('span');
            hint.className = 'cmd-palette-hint';
            hint.textContent = it.hint;

            row.appendChild(label);
            row.appendChild(hint);
            row.addEventListener('mouseenter', () => {
                selectedIdx = idx;
                refreshHighlight();
            });
            row.addEventListener('click', () => {
                selectedIdx = idx;
                commitSelection();
            });
            list.appendChild(row);
        });
    }

    function refreshHighlight() {
        if (!list) return;
        list.querySelectorAll('.cmd-palette-row').forEach((row, idx) => {
            row.classList.toggle('selected', idx === selectedIdx);
        });
    }

    function updateVisible() {
        const q = input ? input.value : '';
        const visible = filterItems(items, q);
        if (selectedIdx >= visible.length) selectedIdx = Math.max(0, visible.length - 1);
        renderList(visible);
        return visible;
    }

    function commitSelection() {
        const visible = filterItems(items, input ? input.value : '');
        const chosen = visible[selectedIdx];
        close();
        if (chosen && typeof chosen.run === 'function') {
            try { chosen.run(); }
            catch (e) { /* swallow — don't nuke keyboard flow */ }
        }
    }

    function open() {
        if (!palette || !input || !list) return;
        isOpen = true;
        items = collectItems();
        selectedIdx = 0;
        input.value = '';
        palette.style.display = 'flex';
        void palette.offsetWidth;
        palette.classList.add('active');
        updateVisible();
        setTimeout(() => { try { input.focus(); } catch (e) {} }, 0);
    }

    function close() {
        if (!palette) return;
        isOpen = false;
        palette.classList.remove('active');
        palette.style.display = 'none';
        if (list) list.textContent = '';
        if (input) input.value = '';
        selectedIdx = 0;
    }

    function onInputKeydown(e) {
        if (e.key === 'ArrowDown') {
            const visible = filterItems(items, input.value);
            if (visible.length > 0) selectedIdx = (selectedIdx + 1) % visible.length;
            refreshHighlight();
            e.preventDefault();
            e.stopPropagation();
            return;
        }
        if (e.key === 'ArrowUp') {
            const visible = filterItems(items, input.value);
            if (visible.length > 0) selectedIdx = (selectedIdx - 1 + visible.length) % visible.length;
            refreshHighlight();
            e.preventDefault();
            e.stopPropagation();
            return;
        }
        if (e.key === 'Enter') {
            commitSelection();
            e.preventDefault();
            e.stopPropagation();
            return;
        }
        if (e.key === 'Escape') {
            close();
            e.preventDefault();
            e.stopPropagation();
            return;
        }
    }

    function onInput() {
        selectedIdx = 0;
        updateVisible();
    }

    function onGlobalKeydown(e) {
        const mod = isMac() ? e.metaKey : e.ctrlKey;
        if (mod && (e.key === 'k' || e.key === 'K')) {
            if (isOpen) close();
            else open();
            e.preventDefault();
            e.stopPropagation();
        }
    }

    function attach() {
        palette = document.getElementById(PALETTE_ID);
        input = document.getElementById(INPUT_ID);
        list = document.getElementById(LIST_ID);
        if (!palette || !input || !list) return;

        palette.addEventListener('click', function (e) {
            if (e.target === palette) close();
        });
        input.addEventListener('keydown', onInputKeydown);
        input.addEventListener('input', onInput);

        document.addEventListener('keydown', onGlobalKeydown, true);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', attach);
    } else {
        attach();
    }

    window.HydraCommandPalette = {
        version: '1.0.0-power-ux',
        attached: true,
        open: open,
        close: close,
        isOpen: () => isOpen,
    };
})();
