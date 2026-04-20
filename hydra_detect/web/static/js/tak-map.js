'use strict';

/**
 * Shared Leaflet map module for Hydra.
 *
 * Exposes HydraTakMap.init(options) which attaches a Leaflet map to a
 * container div and polls the Hydra APIs to keep it live:
 *   - /api/stats           → ownship lat/lon + heading (self marker)
 *   - /api/tak/peers       → peer markers
 *   - /api/active_tracks   → geo-referenced detections (optional)
 *
 * Used by:
 *   - tak.js (large pane on the TAK tab)
 *   - ops.js (small Cockpit TAK cell)
 *
 * Intentionally framework-free — the rest of the Hydra frontend is
 * vanilla JS and we do not pull in a bundler.
 */
const HydraTakMap = (() => {
    const DEFAULT_CENTER = [35.0383, -79.5250]; // SORCC / Southern Pines fallback
    const DEFAULT_ZOOM = 15;

    // Self marker: olive circle + heading triangle
    const SELF_ICON = L.divIcon({
        className: 'hydra-map-self',
        html: '<div class="hydra-self-dot"></div><div class="hydra-self-heading"></div>',
        iconSize: [28, 28],
        iconAnchor: [14, 14],
    });

    function peerIcon(callsign) {
        return L.divIcon({
            className: 'hydra-map-peer',
            html: '<div class="hydra-peer-dot"></div>' +
                  '<div class="hydra-peer-label">' + escapeHtml(callsign || 'PEER') + '</div>',
            iconSize: [24, 24],
            iconAnchor: [12, 12],
        });
    }

    function trackIcon(label) {
        return L.divIcon({
            className: 'hydra-map-track',
            html: '<div class="hydra-track-dot"></div>' +
                  '<div class="hydra-track-label">' + escapeHtml(label || 'TRK') + '</div>',
            iconSize: [20, 20],
            iconAnchor: [10, 10],
        });
    }

    function escapeHtml(s) {
        const div = document.createElement('div');
        div.textContent = String(s);
        return div.innerHTML;
    }

    /**
     * Init a map instance on the container with id `containerId`.
     * Returns a control object with stop() / refresh() / etc.
     *
     * options: {
     *   containerId: string (required)
     *   pollMs: number (default 2000)
     *   showZoom: bool (default true)
     *   showAttribution: bool (default true)
     *   showTracks: bool (default true)
     *   onTitleUpdate: (callsign, fixInfo) => void (optional)
     * }
     */
    function init(options) {
        options = options || {};
        const container = document.getElementById(options.containerId);
        if (!container) {
            console.warn('[tak-map] container not found:', options.containerId);
            return null;
        }

        const pollMs = options.pollMs || 2000;
        const showTracks = options.showTracks !== false;

        // Guard against double-init (router may re-enter views)
        if (container._hydraMap) {
            container._hydraMap.invalidateSize();
            return container._hydraMap._hydraCtl;
        }

        const map = L.map(container, {
            center: DEFAULT_CENTER,
            zoom: DEFAULT_ZOOM,
            zoomControl: options.showZoom !== false,
            attributionControl: options.showAttribution !== false,
            preferCanvas: true,
        });

        // OSM tiles. For offline/field deployment we bundle a local tile
        // server later; the CDN works in the classroom and is harmless
        // everywhere else (falls back to grey tiles on no-network).
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: options.showAttribution !== false ? '© OpenStreetMap' : '',
        }).addTo(map);

        let selfMarker = null;
        let selfHeading = 0;
        const peerMarkers = new Map();   // callsign → marker
        const trackMarkers = new Map();  // track_id → marker
        let hasCentered = false;
        let timer = null;
        let stopped = false;

        function setSelfHeading(heading) {
            selfHeading = (typeof heading === 'number') ? heading : 0;
            const el = selfMarker && selfMarker.getElement();
            if (el) {
                const arrow = el.querySelector('.hydra-self-heading');
                if (arrow) arrow.style.transform = 'rotate(' + selfHeading + 'deg)';
            }
        }

        async function refreshSelf() {
            try {
                const r = await fetch('/api/stats', { credentials: 'same-origin' });
                if (!r.ok) return;
                const s = await r.json();
                const lat = (typeof s.lat === 'number') ? s.lat : null;
                const lon = (typeof s.lon === 'number') ? s.lon : null;
                const callsign = s.callsign || 'HYDRA-1';
                const fix = s.gps_fix;

                if (options.onTitleUpdate) {
                    options.onTitleUpdate(callsign, {
                        fix: fix,
                        lat: lat,
                        lon: lon,
                        alt: s.alt_msl_m,
                    });
                }

                if (lat != null && lon != null) {
                    if (!selfMarker) {
                        selfMarker = L.marker([lat, lon], {
                            icon: SELF_ICON,
                            title: callsign,
                            interactive: false,
                            keyboard: false,
                        }).addTo(map);
                    } else {
                        selfMarker.setLatLng([lat, lon]);
                    }
                    setSelfHeading(s.heading);
                    if (!hasCentered) {
                        map.setView([lat, lon], DEFAULT_ZOOM);
                        hasCentered = true;
                    }
                }
            } catch (e) { /* transient */ }
        }

        async function refreshPeers() {
            try {
                const r = await fetch('/api/tak/peers', { credentials: 'same-origin' });
                if (!r.ok) return;
                const data = await r.json();
                const peers = Array.isArray(data.peers) ? data.peers : [];
                const seen = new Set();
                peers.forEach(p => {
                    const cs = p.callsign || p.cs || p.uid || 'PEER';
                    const lat = (typeof p.lat === 'number') ? p.lat
                              : (typeof p.latitude === 'number') ? p.latitude : null;
                    const lon = (typeof p.lon === 'number') ? p.lon
                              : (typeof p.longitude === 'number') ? p.longitude : null;
                    if (lat == null || lon == null) return; // need position
                    seen.add(cs);
                    let m = peerMarkers.get(cs);
                    if (!m) {
                        m = L.marker([lat, lon], {
                            icon: peerIcon(cs),
                            title: cs,
                        }).addTo(map);
                        peerMarkers.set(cs, m);
                    } else {
                        m.setLatLng([lat, lon]);
                    }
                });
                // Remove peers that dropped off
                for (const [cs, m] of peerMarkers) {
                    if (!seen.has(cs)) {
                        map.removeLayer(m);
                        peerMarkers.delete(cs);
                    }
                }
            } catch (e) { /* transient */ }
        }

        async function refreshTracks() {
            if (!showTracks) return;
            try {
                const r = await fetch('/api/active_tracks', { credentials: 'same-origin' });
                if (!r.ok) return;
                const tracks = await r.json();
                const list = Array.isArray(tracks) ? tracks : [];
                const seen = new Set();
                list.forEach(t => {
                    const lat = (typeof t.lat === 'number') ? t.lat : null;
                    const lon = (typeof t.lon === 'number') ? t.lon : null;
                    if (lat == null || lon == null) return;
                    const id = t.track_id != null ? t.track_id : t.id;
                    if (id == null) return;
                    seen.add(id);
                    const labelStr = '#' + id + ' ' + (t.label || '?');
                    let m = trackMarkers.get(id);
                    if (!m) {
                        m = L.marker([lat, lon], {
                            icon: trackIcon(labelStr),
                            title: labelStr,
                        }).addTo(map);
                        trackMarkers.set(id, m);
                    } else {
                        m.setLatLng([lat, lon]);
                    }
                });
                for (const [id, m] of trackMarkers) {
                    if (!seen.has(id)) {
                        map.removeLayer(m);
                        trackMarkers.delete(id);
                    }
                }
            } catch (e) { /* transient */ }
        }

        async function tick() {
            if (stopped) return;
            await Promise.all([refreshSelf(), refreshPeers(), refreshTracks()]);
            if (!stopped) timer = setTimeout(tick, pollMs);
        }

        // Let Leaflet read the container's size after it has been laid out.
        requestAnimationFrame(() => {
            map.invalidateSize();
            tick();
        });

        const ctl = {
            map: map,
            stop() {
                stopped = true;
                if (timer) clearTimeout(timer);
            },
            refresh() {
                map.invalidateSize();
                return Promise.all([refreshSelf(), refreshPeers(), refreshTracks()]);
            },
        };
        map._hydraCtl = ctl;
        container._hydraMap = map;
        return ctl;
    }

    return { init };
})();
