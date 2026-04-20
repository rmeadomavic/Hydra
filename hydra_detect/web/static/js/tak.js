'use strict';

/**
 * Hydra Detect v2.0 — TAK View Logic (M1 scaffold)
 *
 * Center column: polls /api/tak/commands at 1 Hz (per port plan rate table)
 * and renders the most recent inbound CoT/GeoChat commands as a chat-log
 * feed. Left/right columns and audit footer are B2/B3/B9 placeholders for
 * M2 — no polling for those until the endpoints land.
 */
const HydraTak = (() => {
    const POLL_INTERVAL_MS = 1000;
    const COMMANDS_LIMIT = 100;
    const SCROLL_PIN_PX = 32;       // within this of bottom → auto-pin to newest

    let pollTimer = null;
    let inFlight = false;
    let backoffMs = POLL_INTERVAL_MS;
    let renderedKeys = [];           // ordered DOM-diff key list
    let rowNodes = Object.create(null); // key → <div> row element

    // ── Lifecycle ──
    function onEnter() {
        renderedKeys = [];
        rowNodes = Object.create(null);
        clearFeedList();
        showEmpty(true);
        startPolling();
    }

    function onLeave() {
        stopPolling();
    }

    // ── Polling ──
    function startPolling() {
        if (pollTimer !== null) return;
        pollOnce();
    }

    function stopPolling() {
        if (pollTimer !== null) {
            clearTimeout(pollTimer);
            pollTimer = null;
        }
        inFlight = false;
    }

    function schedule(nextMs) {
        if (pollTimer !== null) clearTimeout(pollTimer);
        pollTimer = setTimeout(pollOnce, nextMs);
    }

    async function pollOnce() {
        pollTimer = null;
        if (inFlight) {
            schedule(POLL_INTERVAL_MS);
            return;
        }
        if (document.visibilityState === 'hidden') {
            schedule(POLL_INTERVAL_MS);
            return;
        }
        inFlight = true;
        try {
            const resp = await fetch('/api/tak/commands?limit=' + COMMANDS_LIMIT, {
                credentials: 'same-origin',
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            renderFeed(Array.isArray(data.commands) ? data.commands : []);
            backoffMs = POLL_INTERVAL_MS;
        } catch (err) {
            backoffMs = Math.min(backoffMs * 2, 10000);
        } finally {
            inFlight = false;
        }
        schedule(backoffMs);
    }

    // ── Rendering ──
    function rowKey(cmd) {
        // Stable-ish key for DOM diffing — same pattern as ops.js track list.
        const ts = typeof cmd.ts === 'number' ? cmd.ts.toFixed(3) : String(cmd.ts || '');
        const tid = cmd.track_id == null ? '' : String(cmd.track_id);
        const sender = cmd.sender || '';
        const action = cmd.action || '';
        return ts + '|' + sender + '|' + action + '|' + tid;
    }

    function formatTs(ts) {
        if (typeof ts !== 'number' || !isFinite(ts)) return '--:--:--';
        const d = new Date(ts * 1000);
        const pad = (n) => (n < 10 ? '0' + n : '' + n);
        return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }

    function truncateRaw(raw) {
        if (!raw) return '';
        if (raw.length > 120) return raw.slice(0, 117) + '...';
        return raw;
    }

    function clearFeedList() {
        const list = document.getElementById('tak-feed-list');
        if (list) list.textContent = '';
    }

    function showEmpty(visible) {
        const empty = document.getElementById('tak-feed-empty');
        const list = document.getElementById('tak-feed-list');
        if (empty) empty.style.display = visible ? '' : 'none';
        if (list) list.style.display = visible ? 'none' : '';
    }

    function buildRow(cmd) {
        const row = document.createElement('div');
        row.className = 'tak-row';

        const tsEl = document.createElement('div');
        tsEl.className = 'tak-row-ts';
        tsEl.textContent = formatTs(cmd.ts);
        row.appendChild(tsEl);

        const head = document.createElement('div');
        head.className = 'tak-row-head';
        const sender = document.createElement('span');
        sender.className = 'tak-row-sender';
        sender.textContent = cmd.sender || '(unknown)';
        const action = document.createElement('span');
        action.className = 'tak-row-action';
        action.textContent = cmd.action || '--';
        head.appendChild(sender);
        head.appendChild(action);
        row.appendChild(head);

        const pills = document.createElement('div');
        pills.className = 'tak-row-pills';
        const statusPill = document.createElement('span');
        statusPill.className = 'tak-pill ' + (cmd.accepted ? 'tak-pill-accepted' : 'tak-pill-rejected');
        statusPill.textContent = cmd.accepted ? 'Accepted' : 'Rejected';
        pills.appendChild(statusPill);
        if (cmd.hmac_state === 'verified') {
            const hmac = document.createElement('span');
            hmac.className = 'tak-chip-hmac';
            hmac.textContent = 'HMAC';
            hmac.title = 'HMAC signature verified';
            pills.appendChild(hmac);
        }
        row.appendChild(pills);

        const body = document.createElement('div');
        body.className = 'tak-row-body';
        if (!cmd.accepted && cmd.reject_reason) {
            const reason = document.createElement('div');
            reason.className = 'tak-row-reason';
            reason.textContent = 'Reject: ' + cmd.reject_reason;
            body.appendChild(reason);
        }
        const raw = document.createElement('div');
        raw.className = 'tak-row-raw';
        raw.textContent = truncateRaw(cmd.raw_text || '');
        raw.title = cmd.raw_text || '';
        body.appendChild(raw);
        row.appendChild(body);

        return row;
    }

    function renderFeed(commands) {
        const list = document.getElementById('tak-feed-list');
        const feed = document.getElementById('tak-feed');
        const meta = document.getElementById('tak-commands-meta');
        if (!list || !feed) return;

        if (meta) {
            const n = commands.length;
            meta.textContent = n + (n === 1 ? ' event' : ' events');
        }

        if (commands.length === 0) {
            renderedKeys = [];
            rowNodes = Object.create(null);
            list.textContent = '';
            showEmpty(true);
            return;
        }
        showEmpty(false);

        // DOM-diff: remove rows no longer present, then ensure incoming order
        // matches the server list (newest last per get_recent_commands).
        const newKeys = commands.map(rowKey);
        const newKeySet = new Set(newKeys);
        for (const oldKey of renderedKeys) {
            if (!newKeySet.has(oldKey)) {
                const node = rowNodes[oldKey];
                if (node && node.parentNode) node.parentNode.removeChild(node);
                delete rowNodes[oldKey];
            }
        }

        // Pin-to-bottom detection BEFORE we mutate.
        const pinned = (feed.scrollHeight - feed.scrollTop - feed.clientHeight) <= SCROLL_PIN_PX;

        for (let i = 0; i < commands.length; i++) {
            const key = newKeys[i];
            const cmd = commands[i];
            let node = rowNodes[key];
            if (!node) {
                node = buildRow(cmd);
                rowNodes[key] = node;
            }
            // Ensure correct position in list (append in order; browsers
            // no-op when appending an existing child in the same position).
            const currentChild = list.children[i];
            if (currentChild !== node) {
                list.insertBefore(node, currentChild || null);
            }
        }

        renderedKeys = newKeys;

        if (pinned) {
            feed.scrollTop = feed.scrollHeight;
        }
    }

    return { onEnter, onLeave };
})();
