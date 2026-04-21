'use strict';

(function() {
    let endpoints = JSON.parse(localStorage.getItem('hydra-fleet-endpoints') || localStorage.getItem('hydra-instructor-endpoints') || '[]');
    let vehicleState = {};
    let alertFeed = [];
    const MAX_ALERTS = 50;

    // Sanitize text to prevent XSS when building DOM nodes
    function esc(str) {
        const d = document.createElement('div');
        d.textContent = String(str);
        return d.textContent;
    }

    // Create a text element helper
    function txt(tag, text, className) {
        const el = document.createElement(tag);
        el.textContent = text;
        if (className) el.className = className;
        return el;
    }

    // Init
    document.getElementById('endpoints-input').value = endpoints.join(', ');
    document.getElementById('save-btn').addEventListener('click', saveEndpoints);

    function saveEndpoints() {
        const raw = document.getElementById('endpoints-input').value;
        endpoints = raw.split(',').map(s => s.trim()).filter(Boolean);
        localStorage.setItem('hydra-fleet-endpoints', JSON.stringify(endpoints));
        pollAll();
    }

    async function pollVehicle(host) {
        const port = 8080;
        const url = 'http://' + host + ':' + port;
        try {
            const resp = await fetch(url + '/api/stats', {signal: AbortSignal.timeout(3000)});
            if (!resp.ok) throw new Error(resp.status);
            const data = await resp.json();
            const prevDetections = vehicleState[host] ? (vehicleState[host]._prevDetections || 0) : 0;
            data._host = host;
            data._url = url;
            data._lastSeen = Date.now();
            data._online = true;
            data._prevDetections = prevDetections;
            vehicleState[host] = data;

            // Check for new detections
            if (data.total_detections && data.total_detections > prevDetections) {
                addAlert(data.callsign || host, 'Detection count: ' + data.total_detections);
            }
            vehicleState[host]._prevDetections = data.total_detections;
        } catch (e) {
            if (!vehicleState[host]) vehicleState[host] = {};
            vehicleState[host]._online = false;
            vehicleState[host]._host = host;
        }
    }

    function addAlert(callsign, message) {
        const now = new Date().toLocaleTimeString('en-US', {hour12: false});
        alertFeed.unshift({time: now, callsign: callsign, message: message});
        if (alertFeed.length > MAX_ALERTS) alertFeed.pop();
        renderAlertFeed();
    }

    function renderAlertFeed() {
        const el = document.getElementById('alert-feed');
        while (el.firstChild) el.removeChild(el.firstChild);

        if (alertFeed.length === 0) {
            const placeholder = document.createElement('div');
            placeholder.className = 'alert-feed-placeholder';
            placeholder.textContent = 'No alerts yet';
            el.appendChild(placeholder);
            return;
        }

        for (const a of alertFeed) {
            const row = document.createElement('div');
            row.className = 'alert-item';

            const timeSpan = txt('span', a.time, 'alert-time');
            row.appendChild(timeSpan);
            row.appendChild(document.createTextNode(' '));

            const csSpan = txt('span', a.callsign, 'alert-callsign');
            row.appendChild(csSpan);
            row.appendChild(document.createTextNode(' ' + a.message));

            el.appendChild(row);
        }
    }

    function renderCards() {
        const grid = document.getElementById('vehicle-grid');
        while (grid.firstChild) grid.removeChild(grid.firstChild);

        for (const host of endpoints) {
            const v = vehicleState[host] || {_online: false, _host: host};
            const online = v._online;
            const callsign = v.callsign || host;
            const fps = v.fps ? v.fps.toFixed(1) : '-';
            const detections = v.total_detections || 0;
            const cameraOk = v.camera_ok !== false;
            const staleSec = online ? Math.round((Date.now() - v._lastSeen) / 1000) : '?';

            let statusClass = 'offline';
            let statusText = 'OFFLINE';
            if (online) {
                if (!cameraOk) { statusClass = 'degraded'; statusText = 'DEGRADED'; }
                else if (detections > 0) { statusClass = 'detecting'; statusText = 'DETECTING'; }
                else { statusClass = 'ready'; statusText = 'READY'; }
            }

            // Battery display
            let batteryStr = '--';
            if (online && v.battery_pct != null) {
                batteryStr = v.battery_pct + '%';
                if (v.battery_v != null) batteryStr += ' (' + v.battery_v.toFixed(1) + 'V)';
            }

            // Mission display
            let missionStr = '--';
            if (online && v.mission_name) {
                missionStr = v.mission_name;
            }

            const card = document.createElement('div');
            card.className = 'vehicle-card ' + statusClass;

            // Header
            const header = document.createElement('div');
            header.className = 'card-header';
            header.appendChild(txt('span', callsign, 'callsign'));
            header.appendChild(txt('span', statusText, 'status-pill ' + statusClass));
            card.appendChild(header);

            // Stats grid
            const stats = document.createElement('div');
            stats.className = 'card-stats';
            const statPairs = [
                ['FPS', fps], ['Detections', detections],
                ['Camera', cameraOk ? 'OK' : 'LOST'], ['Battery', batteryStr],
                ['Sortie', missionStr], ['Updated', staleSec + 's ago']
            ];
            for (const [label, value] of statPairs) {
                stats.appendChild(txt('span', label, 'label'));
                stats.appendChild(txt('span', value, ''));
            }
            card.appendChild(stats);

            // Open Dashboard button
            const openBtn = document.createElement('button');
            openBtn.className = 'open-btn';
            openBtn.textContent = 'Open Dashboard';
            openBtn.addEventListener('click', (function(h) {
                return function() { window.open('http://' + h + ':8080', '_blank'); };
            })(host));
            card.appendChild(openBtn);

            // Abort button
            const abortBtn = document.createElement('button');
            abortBtn.className = 'abort-btn';
            abortBtn.textContent = 'ABORT';
            abortBtn.addEventListener('click', (function(h) {
                return function() { abortVehicle(h); };
            })(host));
            card.appendChild(abortBtn);

            grid.appendChild(card);
        }
    }

    async function abortVehicle(host) {
        try {
            // 5 s timeout — an unreachable platform must fail fast so the
            // instructor can move on during a fleet-wide emergency.
            const resp = await fetch('http://' + host + ':8080/api/abort', {
                method: 'POST',
                signal: AbortSignal.timeout(5000),
            });
            if (resp.ok) {
                addAlert(vehicleState[host] ? (vehicleState[host].callsign || host) : host, 'ABORT sent by range control');
            } else {
                addAlert(host, 'ABORT failed — HTTP ' + resp.status);
            }
        } catch (e) {
            addAlert(host, 'ABORT FAILED — platform unreachable');
        }
    }

    async function pollAll() {
        await Promise.allSettled(endpoints.map(pollVehicle));
        renderCards();
    }

    // Poll every 2 seconds
    pollAll();
    setInterval(pollAll, 2000);
})();
