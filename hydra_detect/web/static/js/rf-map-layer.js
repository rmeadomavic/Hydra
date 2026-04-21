'use strict';

/**
 * Hydra Detect — RF map overlay for the Cockpit TAK Leaflet instance.
 *
 * Attaches four layer groups (samples, breadcrumb, search pattern, best) to
 * the existing map handle returned by HydraTakMap.init(). Samples render as
 * canvas-backed circleMarkers (cheap to redraw 300 of them); the best-position
 * marker uses an L.divIcon star so it pops visually.
 *
 * Data flow: rf-hunt.js polls /api/rf/rssi_history and /api/rf/status at 1 Hz,
 * then calls setSamples(...) / setStatus(...) here. The RF layer does not do
 * its own polling — single source of truth in rf-hunt.js.
 *
 * CSP-safe: relies on Leaflet from unpkg which is already in the policy.
 */
const HydraRfMap = (() => {
    let map = null;
    let layerSamples = null;
    let layerTrail = null;
    let layerBest = null;
    let layerTarget = null;
    // bssid/freq -> marker for best; id (timestamp) -> marker for samples
    const sampleMarkers = new Map();
    let trailPoly = null;
    let bestMarker = null;
    let targetMarker = null;

    // RSSI color ramp: -100 gray → -80 red → -60 amber → -40 olive → -0 bright
    function rssiColor(rssi) {
        if (typeof rssi !== 'number') return 'rgba(110,110,110,0.6)';
        if (rssi <= -90) return 'rgba(100,100,100,0.5)';
        if (rssi <= -75) return 'rgba(197, 48, 48, 0.8)';
        if (rssi <= -60) return 'rgba(234, 179, 8, 0.85)';
        if (rssi <= -45) return 'rgba(74, 124, 46, 0.9)';
        return 'rgba(56, 87, 35, 1.0)';
    }

    function rssiRadius(rssi) {
        if (typeof rssi !== 'number') return 3;
        // -100 → 2px, 0 → 8px
        const p = Math.max(0, Math.min(100, rssi + 100));
        return 2 + (p / 100) * 6;
    }

    function attach(mapHandle) {
        if (!mapHandle || !window.L) return false;
        if (map && map === mapHandle) return true;
        if (map && map !== mapHandle) detach();

        map = mapHandle;
        // Dedicated pane so RF samples stay below TAK tracks.
        if (!map.getPane('hydra-rf-samples')) {
            const pane = map.createPane('hydra-rf-samples');
            pane.style.zIndex = 410;
        }
        layerSamples = L.layerGroup().addTo(map);
        layerTrail = L.layerGroup().addTo(map);
        layerBest = L.layerGroup().addTo(map);
        layerTarget = L.layerGroup().addTo(map);
        return true;
    }

    function detach() {
        if (!map) return;
        clearAll();
        try {
            if (layerSamples) map.removeLayer(layerSamples);
            if (layerTrail) map.removeLayer(layerTrail);
            if (layerBest) map.removeLayer(layerBest);
            if (layerTarget) map.removeLayer(layerTarget);
        } catch (_err) { /* noop */ }
        layerSamples = layerTrail = layerBest = layerTarget = null;
        map = null;
    }

    function clearAll() {
        sampleMarkers.clear();
        if (layerSamples) layerSamples.clearLayers();
        if (layerTrail) layerTrail.clearLayers();
        if (layerBest) layerBest.clearLayers();
        if (layerTarget) layerTarget.clearLayers();
        trailPoly = null;
        bestMarker = null;
        targetMarker = null;
    }

    /**
     * Update sample dots + breadcrumb trail from an /api/rf/rssi_history
     * response (list of {t, rssi, lat, lon}).
     *
     * Diff-by-timestamp so we only add new markers per poll — never full
     * rebuild.
     */
    function setSamples(history) {
        if (!map || !layerSamples) return;
        if (!Array.isArray(history) || history.length === 0) {
            if (sampleMarkers.size > 0) {
                sampleMarkers.clear();
                layerSamples.clearLayers();
                layerTrail.clearLayers();
                trailPoly = null;
            }
            return;
        }

        // Incremental add: keep markers for seen timestamps, add new ones,
        // drop any no-longer-present (history is a bounded ring so old
        // samples fall off).
        const keep = new Set();
        const trailPoints = [];
        for (const s of history) {
            if (typeof s.lat !== 'number' || typeof s.lon !== 'number') continue;
            const id = 't' + s.t;
            keep.add(id);
            trailPoints.push([s.lat, s.lon]);
            if (sampleMarkers.has(id)) continue;
            const marker = L.circleMarker([s.lat, s.lon], {
                pane: 'hydra-rf-samples',
                radius: rssiRadius(s.rssi),
                color: rssiColor(s.rssi),
                fillColor: rssiColor(s.rssi),
                fillOpacity: 0.85,
                stroke: false,
                interactive: false,
            });
            marker.addTo(layerSamples);
            sampleMarkers.set(id, marker);
        }
        for (const [id, marker] of sampleMarkers) {
            if (!keep.has(id)) {
                layerSamples.removeLayer(marker);
                sampleMarkers.delete(id);
            }
        }

        // Breadcrumb trail (single polyline).
        if (trailPoints.length >= 2) {
            if (!trailPoly) {
                trailPoly = L.polyline(trailPoints, {
                    color: 'rgba(56, 87, 35, 0.9)',
                    weight: 2,
                    opacity: 0.85,
                    interactive: false,
                });
                trailPoly.addTo(layerTrail);
            } else {
                trailPoly.setLatLngs(trailPoints);
            }
        } else if (trailPoly) {
            layerTrail.removeLayer(trailPoly);
            trailPoly = null;
        }
    }

    /** Update the best-position star from /api/rf/status. */
    function setStatus(status) {
        if (!map || !layerBest) return;
        if (!status || typeof status !== 'object') return;
        const lat = (typeof status.best_lat === 'number') ? status.best_lat : null;
        const lon = (typeof status.best_lon === 'number') ? status.best_lon : null;
        if (lat == null || lon == null) {
            if (bestMarker) {
                layerBest.removeLayer(bestMarker);
                bestMarker = null;
            }
            return;
        }
        const icon = L.divIcon({
            className: 'hydra-rf-best',
            html:
                '<div class="hydra-rf-best-star" title="Best RSSI: '
                + (status.best_rssi != null
                    ? Math.round(status.best_rssi) + ' dBm'
                    : '?')
                + '">&#9733;</div>',
            iconSize: [22, 22],
            iconAnchor: [11, 11],
        });
        if (!bestMarker) {
            bestMarker = L.marker([lat, lon], {
                icon: icon,
                interactive: false,
                keyboard: false,
            });
            bestMarker.addTo(layerBest);
        } else {
            bestMarker.setLatLng([lat, lon]);
            bestMarker.setIcon(icon);
        }
    }

    return {
        attach: attach,
        detach: detach,
        clear: clearAll,
        setSamples: setSamples,
        setStatus: setStatus,
    };
})();

if (typeof window !== 'undefined') {
    window.HydraRfMap = HydraRfMap;
}
