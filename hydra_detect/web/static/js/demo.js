'use strict';

/**
 * Hydra Detect v2.0 — Tech-Day Demo View Logic.
 *
 * Ports the tech-day-demo.jsx mock to a compact single-screen "show-off"
 * view for 2-minute open-house demos. The left rail is static tech
 * highlights, the centre shows the live feed + mini TAK map, the right
 * rail polls live peers / stats.
 *
 *   - /api/stats      @ 1s  — feed FPS, tracks, detector, mavlink link state
 *   - /api/tak/peers  @ 3s  — peer roster, mini-map markers, "peers" tile
 *
 * Each poll has its own timer + exponential backoff and honours
 * document.visibilityState. Peer rows + mini-map markers are DOM-diffed
 * (reuse node per peer UID) so the browser never rebuilds the subtree.
 */
const HydraDemo = (() => {
    const POLL_MS_STATS = 1000;
    const POLL_MS_PEERS = 3000;
    const MAX_PEERS_UI  = 6;        // top-hits rail is intentionally compact

    // Stats polling state
    let statsTimer = null;
    let statsInflight = false;
    let statsBackoff = POLL_MS_STATS;

    // Peers polling state
    let peersTimer = null;
    let peersInflight = false;
    let peersBackoff = POLL_MS_PEERS;

    // DOM-diff caches (keyed by peer uid)
    let peerRowNodes = Object.create(null);
    let peerRowOrder = [];
    let peerMarkerNodes = Object.create(null);
    let peerMarkerOrder = [];

    // ── Lifecycle ──
    function onEnter() {
        peerRowNodes = Object.create(null);
        peerRowOrder = [];
        peerMarkerNodes = Object.create(null);
        peerMarkerOrder = [];
        resetFeedImg();
        startStatsPoll();
        startPeersPoll();
    }

    function onLeave() {
        stopAllPolls();
    }

    function stopAllPolls() {
        if (statsTimer !== null) { clearTimeout(statsTimer); statsTimer = null; }
        if (peersTimer !== null) { clearTimeout(peersTimer); peersTimer = null; }
        statsInflight = false;
        peersInflight = false;
    }

    // Re-kick the <img> src so /stream.jpg refreshes when the view activates.
    function resetFeedImg() {
        const img = document.getElementById('demo-feed-img');
        if (!img) return;
        const stamp = Date.now();
        img.src = '/stream.jpg?t=' + stamp;
    }

    // ── /api/stats poller ──
    function startStatsPoll() {
        if (statsTimer !== null) return;
        pollStats();
    }
    function scheduleStats(ms) {
        if (statsTimer !== null) clearTimeout(statsTimer);
        statsTimer = setTimeout(pollStats, ms);
    }
    async function pollStats() {
        statsTimer = null;
        if (statsInflight) { scheduleStats(POLL_MS_STATS); return; }
        if (document.visibilityState === 'hidden') {
            scheduleStats(POLL_MS_STATS);
            return;
        }
        statsInflight = true;
        try {
            const resp = await fetch('/api/stats', { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            updateStats(data || {});
            statsBackoff = POLL_MS_STATS;
        } catch (err) {
            statsBackoff = Math.min(statsBackoff * 2, 10000);
        } finally {
            statsInflight = false;
        }
        scheduleStats(statsBackoff);
    }

    function updateStats(data) {
        const fps = (typeof data.fps === 'number' && isFinite(data.fps)) ? data.fps : null;
        const tracks = (typeof data.active_tracks === 'number') ? data.active_tracks : null;
        const detector = (typeof data.detector === 'string') ? data.detector : null;
        const mavlinkUp = !!data.mavlink;

        setText('demo-stat-fps', fps == null ? '-- FPS' : fps.toFixed(1) + ' FPS');
        setText('demo-stat-tracks', tracks == null ? '-- tracks' : tracks + (tracks === 1 ? ' track' : ' tracks'));
        setText('demo-stat-model', detector ? 'detector: ' + detector : 'detector: --');
        setText('demo-stat-mavlink', 'link: ' + (mavlinkUp ? 'up' : 'down'));

        setText('demo-tile-fps', fps == null ? '--' : fps.toFixed(0));
        setText('demo-tile-tracks', tracks == null ? '--' : String(tracks));

        // Center header meta: detector + active tracks summary
        const meta = detector ? detector + ' · HDMI 0' : '-- · HDMI 0';
        setText('demo-center-meta', meta);
    }

    // ── /api/tak/peers poller ──
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
        if (document.visibilityState === 'hidden') {
            schedulePeers(POLL_MS_PEERS);
            return;
        }
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

    function updatePeers(data) {
        const peers = Array.isArray(data.peers) ? data.peers : [];

        // Stat tile + left-rail meta + top-hits card header
        setText('demo-tile-peers', String(peers.length));
        setText('demo-stat-peers', peers.length + (peers.length === 1 ? ' peer' : ' peers'));
        setText('demo-peers-count', String(peers.length));

        renderPeerList(peers.slice(0, MAX_PEERS_UI));
        renderPeerMarkers(peers.slice(0, MAX_PEERS_UI));
    }

    function peerKey(peer) {
        if (peer && peer.uid) return String(peer.uid);
        if (peer && peer.callsign) return 'cs:' + String(peer.callsign);
        return '';
    }

    function peerTone(cotType) {
        if (typeof cotType !== 'string') return '';
        if (cotType.startsWith('a-f-')) return 'demo-peer-dot-friendly';
        if (cotType.startsWith('a-h-')) return 'demo-peer-dot-hostile';
        return '';
    }

    function ageString(lastSeen) {
        if (typeof lastSeen !== 'number' || !isFinite(lastSeen)) return '--';
        const delta = Math.max(0, Date.now() / 1000 - lastSeen);
        if (delta < 60)   return Math.floor(delta) + 's';
        if (delta < 3600) return Math.floor(delta / 60) + 'm';
        return Math.floor(delta / 3600) + 'h';
    }

    function renderPeerList(peers) {
        const list = document.getElementById('demo-peers-list');
        const empty = document.getElementById('demo-peers-empty');
        if (!list) return;

        if (peers.length === 0) {
            for (const key of peerRowOrder) {
                const node = peerRowNodes[key];
                if (node && node.parentNode) node.parentNode.removeChild(node);
            }
            peerRowNodes = Object.create(null);
            peerRowOrder = [];
            if (empty) empty.style.display = '';
            return;
        }
        if (empty) empty.style.display = 'none';

        const newKeys = peers.map(peerKey);
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

    function buildPeerRow(peer) {
        const row = document.createElement('div');
        row.className = 'demo-peer-row';

        const dot = document.createElement('span');
        dot.className = 'demo-peer-dot';
        row.appendChild(dot);

        const name = document.createElement('span');
        name.className = 'demo-peer-name';
        row.appendChild(name);

        const age = document.createElement('span');
        age.className = 'demo-peer-age';
        row.appendChild(age);

        refreshPeerRow(row, peer);
        return row;
    }

    function refreshPeerRow(row, peer) {
        const dot = row.querySelector('.demo-peer-dot');
        const name = row.querySelector('.demo-peer-name');
        const age = row.querySelector('.demo-peer-age');
        if (dot) {
            dot.className = 'demo-peer-dot ' + peerTone(peer && peer.cot_type);
        }
        if (name) {
            name.textContent = (peer && peer.callsign) ? peer.callsign : '(unknown)';
        }
        if (age) {
            age.textContent = ageString(peer && peer.last_seen);
        }
    }

    // Mini-map SVG markers. Deterministic positions around the self marker
    // (centre at 50%,55% in the 400×300 viewBox) so peers without lat/lon
    // still render in a stable ring.
    function renderPeerMarkers(peers) {
        const svgNs = 'http://www.w3.org/2000/svg';
        const group = document.getElementById('demo-takmini-peers');
        const empty = document.getElementById('demo-takmini-empty');
        if (!group) return;

        if (peers.length === 0) {
            for (const key of peerMarkerOrder) {
                const node = peerMarkerNodes[key];
                if (node && node.parentNode) node.parentNode.removeChild(node);
            }
            peerMarkerNodes = Object.create(null);
            peerMarkerOrder = [];
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;

        const newKeys = peers.map(peerKey);
        const newSet = new Set(newKeys);
        for (const oldKey of peerMarkerOrder) {
            if (!newSet.has(oldKey)) {
                const node = peerMarkerNodes[oldKey];
                if (node && node.parentNode) node.parentNode.removeChild(node);
                delete peerMarkerNodes[oldKey];
            }
        }

        for (let i = 0; i < peers.length; i++) {
            const peer = peers[i] || {};
            const key = newKeys[i];
            // Deterministic ring: spread peers around the self marker.
            const angle = (i / Math.max(peers.length, 1)) * Math.PI * 2;
            const cx = 200 + Math.cos(angle) * 80;
            const cy = 165 + Math.sin(angle) * 60;

            let node = peerMarkerNodes[key];
            if (!node) {
                node = document.createElementNS(svgNs, 'g');
                const circle = document.createElementNS(svgNs, 'circle');
                circle.setAttribute('r', '4');
                circle.setAttribute('fill', '#93c5fd');
                circle.setAttribute('stroke', '#000');
                circle.setAttribute('stroke-width', '0.5');
                node.appendChild(circle);

                const text = document.createElementNS(svgNs, 'text');
                text.setAttribute('x', '7');
                text.setAttribute('y', '3');
                text.setAttribute('class', 'demo-takmini-peer-label');
                node.appendChild(text);

                peerMarkerNodes[key] = node;
            }
            node.setAttribute('transform', 'translate(' + cx.toFixed(1) + ' ' + cy.toFixed(1) + ')');
            const label = node.querySelector('text');
            if (label) label.textContent = (peer && peer.callsign) ? peer.callsign : '--';

            const current = group.children[i];
            if (current !== node) {
                group.insertBefore(node, current || null);
            }
        }
        peerMarkerOrder = newKeys;
    }

    // ── helpers ──
    function setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    return {
        onEnter,
        onLeave,
        _updateStats: updateStats,
        _updatePeers: updatePeers,
    };
})();
