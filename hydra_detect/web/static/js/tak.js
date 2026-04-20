'use strict';

/**
 * Hydra Detect v2.0 — TAK View Logic (M2)
 *
 * Polls four endpoints independently, each with its own timer and
 * exponential-backoff on failure:
 *   - /api/tak/commands     @ 1s (center column — M1)
 *   - /api/tak/type_counts  @ 2s (left column, CoT type histogram)
 *   - /api/tak/peers        @ 3s (right column, peer list + security)
 *   - /api/audit/summary    @ 5s (audit footer: tiles + recent events)
 *
 * DOM-diff on update — reuse row nodes across polls so the browser doesn't
 * rebuild the tree.
 */
const HydraTak = (() => {
    // ── Shared polling primitives ──
    const POLL_MS_COMMANDS = 1000;
    const POLL_MS_TYPES    = 2000;
    const POLL_MS_PEERS    = 3000;
    const POLL_MS_AUDIT    = 5000;
    const COMMANDS_LIMIT   = 100;
    const AUDIT_RECENT     = 5;
    const SCROLL_PIN_PX    = 32;

    // Commands feed state (M1)
    let cmdTimer = null;
    let cmdInflight = false;
    let cmdBackoff = POLL_MS_COMMANDS;
    let renderedKeys = [];
    let rowNodes = Object.create(null);

    // Type counts state (M2)
    let typeTimer = null;
    let typeInflight = false;
    let typeBackoff = POLL_MS_TYPES;
    let typeRowNodes = Object.create(null);
    let typeRowOrder = [];

    // Peers state (M2)
    let peersTimer = null;
    let peersInflight = false;
    let peersBackoff = POLL_MS_PEERS;
    let peerRowNodes = Object.create(null);
    let peerRowOrder = [];

    // Audit state (M2)
    let auditTimer = null;
    let auditInflight = false;
    let auditBackoff = POLL_MS_AUDIT;
    let auditEventRowNodes = Object.create(null);
    let auditEventRowOrder = [];

    // ── Lifecycle ──
    let mapCtl = null;

    function onEnter() {
        renderedKeys = [];
        rowNodes = Object.create(null);
        typeRowNodes = Object.create(null);
        typeRowOrder = [];
        peerRowNodes = Object.create(null);
        peerRowOrder = [];
        auditEventRowNodes = Object.create(null);
        auditEventRowOrder = [];

        clearFeedList();
        showEmpty(true);

        // Large Leaflet map — self + peers + tracks, 2s poll. Shared module.
        if (window.HydraTakMap && document.getElementById('tak-map-canvas')) {
            mapCtl = HydraTakMap.init({
                containerId: 'tak-map-canvas',
                pollMs: 2000,
                showZoom: true,
                showAttribution: true,
                showTracks: true,
                onTitleUpdate: (callsign, info) => {
                    const t = document.getElementById('tak-map-title');
                    const s = document.getElementById('tak-map-sub');
                    if (t) t.textContent = 'TAK · ' + callsign;
                    if (s) {
                        if (info.lat != null && info.lon != null) {
                            s.textContent = info.lat.toFixed(5) + ', ' +
                                info.lon.toFixed(5) + ' · fix ' + info.fix;
                        } else {
                            s.textContent = 'no fix';
                        }
                    }
                },
            });
        }

        startCommandsPoll();
        startTypeCountsPoll();
        startPeersPoll();
        startAuditPoll();
    }

    function onLeave() {
        stopAllPolls();
        if (mapCtl) { mapCtl.stop(); mapCtl = null; }
    }

    function stopAllPolls() {
        if (cmdTimer    !== null) { clearTimeout(cmdTimer);    cmdTimer = null; }
        if (typeTimer   !== null) { clearTimeout(typeTimer);   typeTimer = null; }
        if (peersTimer  !== null) { clearTimeout(peersTimer);  peersTimer = null; }
        if (auditTimer  !== null) { clearTimeout(auditTimer);  auditTimer = null; }
        cmdInflight = typeInflight = peersInflight = auditInflight = false;
    }

    // ── Commands feed (M1) ──
    function startCommandsPoll() {
        if (cmdTimer !== null) return;
        pollCommands();
    }
    function scheduleCommands(ms) {
        if (cmdTimer !== null) clearTimeout(cmdTimer);
        cmdTimer = setTimeout(pollCommands, ms);
    }
    async function pollCommands() {
        cmdTimer = null;
        if (cmdInflight) { scheduleCommands(POLL_MS_COMMANDS); return; }
        if (document.visibilityState === 'hidden') { scheduleCommands(POLL_MS_COMMANDS); return; }
        cmdInflight = true;
        try {
            const resp = await fetch('/api/tak/commands?limit=' + COMMANDS_LIMIT, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            renderFeed(Array.isArray(data.commands) ? data.commands : []);
            cmdBackoff = POLL_MS_COMMANDS;
        } catch (err) {
            cmdBackoff = Math.min(cmdBackoff * 2, 10000);
        } finally {
            cmdInflight = false;
        }
        scheduleCommands(cmdBackoff);
    }

    function rowKey(cmd) {
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

        const newKeys = commands.map(rowKey);
        const newKeySet = new Set(newKeys);
        for (const oldKey of renderedKeys) {
            if (!newKeySet.has(oldKey)) {
                const node = rowNodes[oldKey];
                if (node && node.parentNode) node.parentNode.removeChild(node);
                delete rowNodes[oldKey];
            }
        }

        const pinned = (feed.scrollHeight - feed.scrollTop - feed.clientHeight) <= SCROLL_PIN_PX;

        for (let i = 0; i < commands.length; i++) {
            const key = newKeys[i];
            const cmd = commands[i];
            let node = rowNodes[key];
            if (!node) {
                node = buildRow(cmd);
                rowNodes[key] = node;
            }
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

    // ── Type counts (M2) ──
    function startTypeCountsPoll() {
        if (typeTimer !== null) return;
        pollTypeCounts();
    }
    function scheduleTypes(ms) {
        if (typeTimer !== null) clearTimeout(typeTimer);
        typeTimer = setTimeout(pollTypeCounts, ms);
    }
    async function pollTypeCounts() {
        typeTimer = null;
        if (typeInflight) { scheduleTypes(POLL_MS_TYPES); return; }
        if (document.visibilityState === 'hidden') { scheduleTypes(POLL_MS_TYPES); return; }
        typeInflight = true;
        try {
            const resp = await fetch('/api/tak/type_counts', { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            updateTypeCounts(data || {});
            typeBackoff = POLL_MS_TYPES;
        } catch (err) {
            typeBackoff = Math.min(typeBackoff * 2, 20000);
        } finally {
            typeInflight = false;
        }
        scheduleTypes(typeBackoff);
    }

    function updateTypeCounts(data) {
        const counts = (data && typeof data.counts === 'object' && data.counts) ? data.counts : {};
        const total = typeof data.total === 'number' ? data.total : 0;
        const windowSec = typeof data.window_seconds === 'number' ? data.window_seconds : 0;

        const totalEl = document.getElementById('tak-type-total');
        if (totalEl) totalEl.textContent = total.toLocaleString();

        const winEl = document.getElementById('tak-type-window');
        if (winEl) winEl.textContent = windowSec > 0 ? windowSec + 's window' : '-- window';

        const subEl = document.getElementById('tak-type-subline');
        if (subEl) {
            if (total === 0) {
                subEl.textContent = 'no inbound CoT in window';
            } else {
                subEl.textContent = total + ' in last ' + windowSec + 's · multicast 239.2.3.1:6969';
            }
        }

        const list = document.getElementById('tak-type-list');
        const empty = document.getElementById('tak-type-empty');
        if (!list) return;

        const entries = Object.entries(counts)
            .filter(([, v]) => typeof v === 'number')
            .sort((a, b) => b[1] - a[1]);

        if (entries.length === 0) {
            for (const key of typeRowOrder) {
                const node = typeRowNodes[key];
                if (node && node.parentNode) node.parentNode.removeChild(node);
            }
            typeRowNodes = Object.create(null);
            typeRowOrder = [];
            if (empty) empty.style.display = '';
            list.style.display = 'none';
            return;
        }
        if (empty) empty.style.display = 'none';
        list.style.display = '';

        const newKeys = entries.map(([k]) => k);
        const newSet = new Set(newKeys);
        for (const oldKey of typeRowOrder) {
            if (!newSet.has(oldKey)) {
                const node = typeRowNodes[oldKey];
                if (node && node.parentNode) node.parentNode.removeChild(node);
                delete typeRowNodes[oldKey];
            }
        }

        for (let i = 0; i < entries.length; i++) {
            const [cotType, count] = entries[i];
            let node = typeRowNodes[cotType];
            if (!node) {
                node = buildTypeRow(cotType);
                typeRowNodes[cotType] = node;
            }
            const countEl = node.querySelector('.tak-breakdown-count');
            if (countEl) countEl.textContent = count.toLocaleString();
            const current = list.children[i];
            if (current !== node) {
                list.insertBefore(node, current || null);
            }
        }
        typeRowOrder = newKeys;
    }

    function buildTypeRow(cotType) {
        const row = document.createElement('div');
        row.className = 'tak-breakdown-row';
        row.dataset.cotType = cotType;

        const label = document.createElement('span');
        label.className = 'tak-breakdown-label';
        label.textContent = cotType;
        row.appendChild(label);

        const count = document.createElement('span');
        count.className = 'tak-breakdown-count';
        count.textContent = '0';
        row.appendChild(count);

        return row;
    }

    // ── Peers + Security (M2) ──
    function startPeersPoll() {
        if (peersTimer !== null) return;
        pollPeers();
    }
    function schedulePeers(ms) {
        if (peersTimer !== null) clearTimeout(peersTimer);
        peersTimer = setTimeout(pollPeers, ms);
    }
    async function pollPeers() {
        peersTimer = null;
        if (peersInflight) { schedulePeers(POLL_MS_PEERS); return; }
        if (document.visibilityState === 'hidden') { schedulePeers(POLL_MS_PEERS); return; }
        peersInflight = true;
        try {
            const resp = await fetch('/api/tak/peers', { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            updatePeers(data || {});
            peersBackoff = POLL_MS_PEERS;
        } catch (err) {
            peersBackoff = Math.min(peersBackoff * 2, 30000);
        } finally {
            peersInflight = false;
        }
        schedulePeers(peersBackoff);
    }

    function peerTone(cotType) {
        if (typeof cotType !== 'string') return 'tak-peer-pill-neutral';
        if (cotType.startsWith('a-f-')) return 'tak-peer-pill-friendly';
        if (cotType.startsWith('a-h-')) return 'tak-peer-pill-hostile';
        return 'tak-peer-pill-neutral';
    }

    function ageString(lastSeen) {
        if (typeof lastSeen !== 'number' || !isFinite(lastSeen)) return '--';
        const nowSec = Date.now() / 1000;
        const delta = Math.max(0, nowSec - lastSeen);
        if (delta < 60) return Math.floor(delta) + 's';
        if (delta < 3600) return Math.floor(delta / 60) + 'm';
        return Math.floor(delta / 3600) + 'h';
    }

    function updatePeers(data) {
        const peers = Array.isArray(data.peers) ? data.peers : [];
        const targets = Array.isArray(data.unicast_targets) ? data.unicast_targets : [];
        const allowed = Array.isArray(data.allowed_callsigns) ? data.allowed_callsigns : [];
        const hmacEnforced = !!data.hmac_enforced;
        const dupAlarm = !!data.duplicate_callsign_alarm;

        // Peer count in header
        const countEl = document.getElementById('tak-peers-count');
        if (countEl) {
            countEl.textContent = peers.length + (peers.length === 1 ? ' peer' : ' peers');
        }

        // Peer list DOM-diff
        const list = document.getElementById('tak-peers-list');
        const empty = document.getElementById('tak-peers-empty');
        if (list) {
            if (peers.length === 0) {
                for (const key of peerRowOrder) {
                    const node = peerRowNodes[key];
                    if (node && node.parentNode) node.parentNode.removeChild(node);
                }
                peerRowNodes = Object.create(null);
                peerRowOrder = [];
                if (empty) empty.style.display = '';
                list.style.display = 'none';
            } else {
                if (empty) empty.style.display = 'none';
                list.style.display = '';
                const newKeys = peers.map(p => p && p.uid ? String(p.uid) : '');
                const newSet = new Set(newKeys);
                for (const oldKey of peerRowOrder) {
                    if (!newSet.has(oldKey)) {
                        const node = peerRowNodes[oldKey];
                        if (node && node.parentNode) node.parentNode.removeChild(node);
                        delete peerRowNodes[oldKey];
                    }
                }
                for (let i = 0; i < peers.length; i++) {
                    const peer = peers[i] || {};
                    const key = newKeys[i];
                    let node = peerRowNodes[key];
                    if (!node) {
                        node = buildPeerRow(peer);
                        peerRowNodes[key] = node;
                    } else {
                        refreshPeerRow(node, peer);
                    }
                    const current = list.children[i];
                    if (current !== node) {
                        list.insertBefore(node, current || null);
                    }
                }
                peerRowOrder = newKeys;
            }
        }

        // Security rows
        const hmacEl = document.getElementById('tak-security-hmac');
        if (hmacEl) {
            hmacEl.textContent = hmacEnforced ? 'yes' : 'no';
            hmacEl.classList.toggle('tak-security-ok', hmacEnforced);
            hmacEl.classList.toggle('tak-security-dim', !hmacEnforced);
        }
        const allowedEl = document.getElementById('tak-security-allowed');
        if (allowedEl) {
            allowedEl.textContent = String(allowed.length);
            allowedEl.title = allowed.join(', ');
        }
        const dupEl = document.getElementById('tak-security-dup');
        if (dupEl) {
            dupEl.textContent = dupAlarm ? 'ALARM' : 'clear';
            dupEl.classList.toggle('tak-security-danger', dupAlarm);
            dupEl.classList.toggle('tak-security-ok', !dupAlarm);
        }
        const targetsEl = document.getElementById('tak-security-targets');
        if (targetsEl) {
            targetsEl.textContent = targets.length === 0 ? '— none —' : targets.join(', ');
        }
    }

    function buildPeerRow(peer) {
        const row = document.createElement('div');
        row.className = 'tak-peer-row';
        row.dataset.uid = peer && peer.uid ? String(peer.uid) : '';

        const head = document.createElement('div');
        head.className = 'tak-peer-head';
        const cs = document.createElement('span');
        cs.className = 'tak-peer-callsign';
        head.appendChild(cs);
        const pill = document.createElement('span');
        pill.className = 'tak-peer-pill';
        head.appendChild(pill);
        row.appendChild(head);

        const uid = document.createElement('div');
        uid.className = 'tak-peer-uid';
        row.appendChild(uid);

        const coords = document.createElement('div');
        coords.className = 'tak-peer-coords';
        row.appendChild(coords);

        const seen = document.createElement('div');
        seen.className = 'tak-peer-seen';
        row.appendChild(seen);

        refreshPeerRow(row, peer);
        return row;
    }

    function refreshPeerRow(row, peer) {
        const cs = row.querySelector('.tak-peer-callsign');
        const pill = row.querySelector('.tak-peer-pill');
        const uid = row.querySelector('.tak-peer-uid');
        const coords = row.querySelector('.tak-peer-coords');
        const seen = row.querySelector('.tak-peer-seen');
        if (cs) cs.textContent = (peer && peer.callsign) ? peer.callsign : '(unknown)';
        if (pill) {
            pill.textContent = (peer && peer.cot_type) ? peer.cot_type : '--';
            pill.className = 'tak-peer-pill ' + peerTone(peer && peer.cot_type);
        }
        if (uid) uid.textContent = (peer && peer.uid) ? peer.uid : '--';
        if (coords) {
            if (peer && typeof peer.lat === 'number' && typeof peer.lon === 'number') {
                coords.textContent = peer.lat.toFixed(4) + ', ' + peer.lon.toFixed(4);
            } else {
                coords.textContent = '—, —';
            }
        }
        if (seen) seen.textContent = ageString(peer && peer.last_seen);
    }

    // ── Audit summary (M2) ──
    function startAuditPoll() {
        if (auditTimer !== null) return;
        pollAudit();
    }
    function scheduleAudit(ms) {
        if (auditTimer !== null) clearTimeout(auditTimer);
        auditTimer = setTimeout(pollAudit, ms);
    }
    async function pollAudit() {
        auditTimer = null;
        if (auditInflight) { scheduleAudit(POLL_MS_AUDIT); return; }
        if (document.visibilityState === 'hidden') { scheduleAudit(POLL_MS_AUDIT); return; }
        auditInflight = true;
        try {
            const resp = await fetch('/api/audit/summary?recent=' + AUDIT_RECENT, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            updateAudit(data || {});
            auditBackoff = POLL_MS_AUDIT;
        } catch (err) {
            auditBackoff = Math.min(auditBackoff * 2, 60000);
        } finally {
            auditInflight = false;
        }
        scheduleAudit(auditBackoff);
    }

    function updateAudit(data) {
        const counts = (data && typeof data.counts === 'object' && data.counts) ? data.counts : {};
        const windowSec = typeof data.window_seconds === 'number' ? data.window_seconds : 0;

        const winEl = document.getElementById('tak-audit-window');
        if (winEl) winEl.textContent = windowSec > 0 ? windowSec + 's window' : '-- window';

        const tileMap = {
            'tak-audit-accepted':       counts.tak_accepted,
            'tak-audit-rejected':       counts.tak_rejected,
            'tak-audit-hmac-invalid':   counts.hmac_invalid_events,
            'tak-audit-approach-arm':   counts.approach_arm_events,
            'tak-audit-drop':           counts.drop_events,
            'tak-audit-strike':         counts.strike_events,
        };
        for (const id of Object.keys(tileMap)) {
            const el = document.getElementById(id);
            if (!el) continue;
            const v = typeof tileMap[id] === 'number' ? tileMap[id] : 0;
            el.textContent = v.toLocaleString();
            const tile = el.closest('.tak-audit-tile');
            if (tile) tile.classList.toggle('tak-audit-tile-zero', v === 0);
        }

        // Recent events DOM-diff
        const recent = Array.isArray(data.recent_events) ? data.recent_events : [];
        const list = document.getElementById('tak-audit-events-list');
        const empty = document.getElementById('tak-audit-events-empty');
        if (!list) return;
        if (recent.length === 0) {
            for (const key of auditEventRowOrder) {
                const node = auditEventRowNodes[key];
                if (node && node.parentNode) node.parentNode.removeChild(node);
            }
            auditEventRowNodes = Object.create(null);
            auditEventRowOrder = [];
            if (empty) empty.style.display = '';
            list.style.display = 'none';
            return;
        }
        if (empty) empty.style.display = 'none';
        list.style.display = '';

        const newKeys = recent.map((ev, i) => auditEventKey(ev, i));
        const newSet = new Set(newKeys);
        for (const oldKey of auditEventRowOrder) {
            if (!newSet.has(oldKey)) {
                const node = auditEventRowNodes[oldKey];
                if (node && node.parentNode) node.parentNode.removeChild(node);
                delete auditEventRowNodes[oldKey];
            }
        }
        for (let i = 0; i < recent.length; i++) {
            const ev = recent[i] || {};
            const key = newKeys[i];
            let node = auditEventRowNodes[key];
            if (!node) {
                node = buildAuditEventRow(ev);
                auditEventRowNodes[key] = node;
            }
            const current = list.children[i];
            if (current !== node) {
                list.insertBefore(node, current || null);
            }
        }
        auditEventRowOrder = newKeys;
    }

    function auditEventKey(ev, i) {
        const ts = (ev && typeof ev.ts === 'number') ? ev.ts.toFixed(3) : String(i);
        const kind = (ev && ev.kind) ? String(ev.kind) : '';
        const op = (ev && ev.operator) ? String(ev.operator) : '';
        const ref = (ev && ev.ref) ? String(ev.ref) : '';
        return ts + '|' + kind + '|' + op + '|' + ref;
    }

    function buildAuditEventRow(ev) {
        const row = document.createElement('div');
        row.className = 'tak-audit-event';

        const ts = document.createElement('span');
        ts.className = 'tak-audit-event-ts';
        ts.textContent = formatTs(ev && ev.ts);
        row.appendChild(ts);

        const kind = document.createElement('span');
        kind.className = 'tak-audit-event-kind';
        kind.textContent = (ev && ev.kind) ? ev.kind : '--';
        row.appendChild(kind);

        const op = document.createElement('span');
        op.className = 'tak-audit-event-op';
        op.textContent = (ev && ev.operator) ? ev.operator : '';
        row.appendChild(op);

        return row;
    }

    return {
        onEnter,
        onLeave,
        // Exported for testing / potential future reuse.
        _updateTypeCounts: updateTypeCounts,
        _updatePeers: updatePeers,
        _updateAudit: updateAudit,
    };
})();
