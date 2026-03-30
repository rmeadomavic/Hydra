'use strict';

// Hydra Detect — Mission Review Map
// Extracted from review.html for CSP hardening (no unsafe-inline).

// --- Map init ---
const map = L.map('map').setView([0, 0], 2);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap',
    maxZoom: 19,
}).addTo(map);

// --- State ---
let allDetections = [];
let activeClasses = new Set();
let markerLayer = L.layerGroup().addTo(map);
let trailLayer = L.layerGroup().addTo(map);

const CLASS_COLORS = [
    '#ff4444', '#44ff44', '#4488ff', '#ffaa00', '#ff44ff',
    '#44ffff', '#ff8844', '#88ff44', '#4444ff', '#ffff44',
];
let classColorMap = {};

function esc(s) {
    const el = document.createElement('span');
    el.textContent = String(s);
    return el.innerHTML;
}

function getClassColor(label) {
    if (!(label in classColorMap)) {
        const idx = Object.keys(classColorMap).length % CLASS_COLORS.length;
        classColorMap[label] = CLASS_COLORS[idx];
    }
    return classColorMap[label];
}

// --- Load log list ---
async function loadLogList() {
    const res = await fetch('/api/review/logs');
    const data = await res.json();
    const sel = document.getElementById('logSelect');
    sel.innerHTML = '<option value="">-- Select a log file --</option>';
    for (const log of data.logs) {
        const opt = document.createElement('option');
        opt.value = log.filename;
        opt.textContent = `${log.filename} (${log.size_kb} KB)`;
        sel.appendChild(opt);
    }
}

// --- Load detections from log ---
async function loadLog(filename) {
    if (!filename) return;
    const res = await fetch(`/api/review/log/${filename}`);
    const data = await res.json();
    allDetections = data.detections || [];
    classColorMap = {};

    // Discover classes
    const classes = new Set();
    allDetections.forEach(d => { if (d.label) classes.add(d.label); });
    activeClasses = new Set(classes);

    // Update stats
    document.getElementById('detCount').textContent = allDetections.length;
    const trackIds = new Set(allDetections.map(d => d.track_id));
    document.getElementById('trackCount').textContent = trackIds.size;

    if (allDetections.length > 0) {
        const first = allDetections[0].timestamp || '';
        const last = allDetections[allDetections.length - 1].timestamp || '';
        document.getElementById('timeRange').textContent =
            `${first.slice(11, 19)} → ${last.slice(11, 19)}`;
    }

    renderClassFilters(classes);
    renderMap();
}

function renderClassFilters(classes) {
    const container = document.getElementById('classFilters');
    container.innerHTML = '';
    for (const cls of classes) {
        const tag = document.createElement('span');
        tag.className = 'class-tag active';
        tag.textContent = cls;
        tag.style.borderColor = getClassColor(cls);
        tag.onclick = () => {
            if (activeClasses.has(cls)) {
                activeClasses.delete(cls);
                tag.classList.remove('active');
            } else {
                activeClasses.add(cls);
                tag.classList.add('active');
            }
            renderMap();
        };
        container.appendChild(tag);
    }
}

function renderMap() {
    markerLayer.clearLayers();
    trailLayer.clearLayers();

    const minConf = parseFloat(document.getElementById('confSlider').value);
    const showTrails = document.getElementById('showTrails').checked;
    const showMarkers = document.getElementById('showMarkers').checked;

    const filtered = allDetections.filter(d =>
        d.lat != null && d.lon != null &&
        activeClasses.has(d.label) &&
        (d.confidence || 0) >= minConf
    );

    if (filtered.length === 0) {
        updateSummary({});
        return;
    }

    // Markers
    const bounds = [];
    const classCounts = {};
    const trackPoints = {};

    for (const d of filtered) {
        const lat = parseFloat(d.lat);
        const lon = parseFloat(d.lon);
        if (isNaN(lat) || isNaN(lon)) continue;

        bounds.push([lat, lon]);
        classCounts[d.label] = (classCounts[d.label] || 0) + 1;

        // Group by track_id for trails
        const tid = d.track_id;
        if (!trackPoints[tid]) trackPoints[tid] = { label: d.label, points: [] };
        trackPoints[tid].points.push([lat, lon]);

        if (showMarkers) {
            const color = getClassColor(d.label);
            const marker = L.circleMarker([lat, lon], {
                radius: 6,
                fillColor: color,
                color: '#000',
                weight: 1,
                fillOpacity: 0.8,
            });

            let popup = `<b>${esc(d.label)}</b> #${esc(d.track_id)}<br>` +
                `Conf: ${((d.confidence || 0) * 100).toFixed(0)}%<br>` +
                `Time: ${esc((d.timestamp || '').slice(11, 19))}<br>` +
                `Pos: ${lat.toFixed(6)}, ${lon.toFixed(6)}`;

            if (d.image) {
                popup += `<br><img class="popup-img" src="/api/review/images/${encodeURIComponent(d.image)}" loading="lazy">`;
            }

            marker.bindPopup(popup, { maxWidth: 300 });
            markerLayer.addLayer(marker);
        }
    }

    // Track trails
    if (showTrails) {
        for (const tid of Object.keys(trackPoints)) {
            const tp = trackPoints[tid];
            if (tp.points.length < 2) continue;
            const color = getClassColor(tp.label);
            L.polyline(tp.points, {
                color: color,
                weight: 2,
                opacity: 0.5,
                dashArray: '4 4',
            }).addTo(trailLayer);
        }
    }

    if (bounds.length > 0) {
        map.fitBounds(bounds, { padding: [30, 30] });
    }

    updateSummary(classCounts);
}

function updateSummary(classCounts) {
    const container = document.getElementById('classSummary');
    const legend = document.getElementById('legend');
    container.innerHTML = '';
    legend.innerHTML = '';

    for (const [cls, count] of Object.entries(classCounts).sort((a, b) => b[1] - a[1])) {
        const color = getClassColor(cls);
        container.innerHTML += `<div class="stat">${esc(cls)}: <span>${count}</span></div>`;
        legend.innerHTML += `<div class="legend-item"><div class="legend-dot" style="background:${color}"></div>${esc(cls)}</div>`;
    }
}

function exportGeoJSON() {
    const minConf = parseFloat(document.getElementById('confSlider').value);
    const features = allDetections
        .filter(d => d.lat != null && d.lon != null && activeClasses.has(d.label) && (d.confidence || 0) >= minConf)
        .map(d => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [parseFloat(d.lon), parseFloat(d.lat)] },
            properties: {
                label: d.label,
                track_id: d.track_id,
                confidence: d.confidence,
                timestamp: d.timestamp,
                image: d.image,
            },
        }));

    const geojson = { type: 'FeatureCollection', features };
    const blob = new Blob([JSON.stringify(geojson, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'hydra_detections.geojson';
    a.click();
    URL.revokeObjectURL(url);
}

// --- Vehicle track / timeline ---
let allEvents = [];
let vehicleTrackLayer = L.layerGroup().addTo(map);
let eventMarkerLayer = L.layerGroup().addTo(map);
let vehicleMarker = null;

async function loadEventLogList() {
    const res = await fetch('/api/review/logs');
    const data = await res.json();
    const sel = document.getElementById('eventSelect');
    sel.textContent = '';
    const defOpt = document.createElement('option');
    defOpt.value = '';
    defOpt.textContent = '-- Select event log --';
    sel.appendChild(defOpt);
    for (const log of (data.event_logs || [])) {
        const opt = document.createElement('option');
        opt.value = log.filename;
        opt.textContent = log.filename;
        sel.appendChild(opt);
    }
}

async function loadEventLog(filename) {
    if (!filename) return;
    const res = await fetch(`/api/review/events/${encodeURIComponent(filename)}`);
    const data = await res.json();
    allEvents = data.events || [];

    document.getElementById('eventCount').textContent = allEvents.length;

    const slider = document.getElementById('timeSlider');
    slider.max = allEvents.length > 0 ? allEvents.length - 1 : 0;
    slider.value = slider.max;

    renderVehicleTrack();
    renderTimeline(parseInt(slider.max));
}

function renderVehicleTrack() {
    vehicleTrackLayer.clearLayers();
    eventMarkerLayer.clearLayers();

    const showTrack = document.getElementById('showVehicleTrack').checked;
    const showEvts = document.getElementById('showEvents').checked;

    // Extract track points
    const trackPoints = allEvents
        .filter(e => e.type === 'track' && e.lat != null && e.lon != null)
        .map(e => [e.lat, e.lon]);

    if (showTrack && trackPoints.length > 1) {
        L.polyline(trackPoints, {
            color: '#00aaff',
            weight: 3,
            opacity: 0.7,
        }).addTo(vehicleTrackLayer);
    }

    // Event markers (actions)
    if (showEvts) {
        const actionEvents = allEvents.filter(e =>
            e.type === 'action' || e.type === 'state' || e.type === 'mission_start' || e.type === 'mission_end'
        );
        const trackEvts = allEvents.filter(e => e.type === 'track');
        for (const evt of actionEvents) {
            let closest = null;
            let minDiff = Infinity;
            for (const t of trackEvts) {
                const diff = Math.abs((t.ts || 0) - (evt.ts || 0));
                if (diff < minDiff) { minDiff = diff; closest = t; }
            }
            if (closest && closest.lat && closest.lon) {
                const label = evt.action || evt.state || evt.type;
                const marker = L.circleMarker([closest.lat, closest.lon], {
                    radius: 8,
                    fillColor: '#ffaa00',
                    color: '#fff',
                    weight: 2,
                    fillOpacity: 0.9,
                });
                const time = new Date((evt.ts || 0) * 1000).toLocaleTimeString('en-US', {hour12: false});
                marker.bindPopup(`<b>${esc(label)}</b><br>Time: ${time}`);
                eventMarkerLayer.addLayer(marker);
            }
        }
    }

    if (trackPoints.length > 0) {
        map.fitBounds(trackPoints, { padding: [30, 30] });
    }
}

function renderTimeline(index) {
    if (allEvents.length === 0) return;

    const idx = Math.min(index, allEvents.length - 1);
    const evt = allEvents[idx];

    if (evt.ts) {
        const time = new Date(evt.ts * 1000).toLocaleTimeString('en-US', {hour12: false});
        document.getElementById('timeValue').textContent = time;
    }

    // Move vehicle marker to current position
    const trackUpTo = allEvents.slice(0, idx + 1).filter(e => e.type === 'track');
    if (trackUpTo.length > 0) {
        const last = trackUpTo[trackUpTo.length - 1];
        if (last.lat && last.lon) {
            if (vehicleMarker) {
                vehicleMarker.setLatLng([last.lat, last.lon]);
            } else {
                vehicleMarker = L.marker([last.lat, last.lon], {
                    icon: L.divIcon({
                        html: '<div style="width:12px;height:12px;background:#00ff88;border:2px solid #fff;border-radius:50%;"></div>',
                        iconSize: [12, 12],
                        iconAnchor: [6, 6],
                    }),
                }).addTo(map);
            }
        }
    }

    // Show recent events in list
    const recentEvents = allEvents.slice(Math.max(0, idx - 10), idx + 1)
        .filter(e => e.type !== 'track')
        .reverse();

    const list = document.getElementById('eventList');
    list.textContent = '';
    for (const e of recentEvents) {
        const div = document.createElement('div');
        div.className = 'timeline-event';
        const time = e.ts ? new Date(e.ts * 1000).toLocaleTimeString('en-US', {hour12: false}) : '';
        const label = e.action || e.state || e.name || e.type;
        div.textContent = `${time} ${label}`;
        list.appendChild(div);
    }
}

// --- Event listeners ---
document.getElementById('logSelect').onchange = e => loadLog(e.target.value);
document.getElementById('confSlider').oninput = e => {
    document.getElementById('confValue').textContent = parseFloat(e.target.value).toFixed(2);
    renderMap();
};
document.getElementById('showTrails').onchange = renderMap;
document.getElementById('showMarkers').onchange = renderMap;
document.getElementById('eventSelect').onchange = e => loadEventLog(e.target.value);
document.getElementById('timeSlider').oninput = e => renderTimeline(parseInt(e.target.value));
document.getElementById('showVehicleTrack').onchange = renderVehicleTrack;
document.getElementById('showEvents').onchange = renderVehicleTrack;

// --- Init ---
loadLogList();
loadEventLogList();
