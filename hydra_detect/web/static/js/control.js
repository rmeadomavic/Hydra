'use strict';
(function() {
    var lockedTrackId = null;

    // Stream — use snapshot polling (same as main dashboard)
    var img = document.getElementById('stream');
    var streamMsg = document.getElementById('stream-msg');
    var streamPolling = true;

    function pollFrame() {
        if (!streamPolling) return;
        img.src = '/stream.jpg?t=' + Date.now();
    }
    img.onload = function() {
        img.style.display = 'block';
        streamMsg.style.display = 'none';
        if (streamPolling) setTimeout(pollFrame, 33);
    };
    img.onerror = function() {
        img.style.display = 'none';
        streamMsg.style.display = 'block';
        streamMsg.textContent = 'Stream lost';
        if (streamPolling) setTimeout(pollFrame, 2000);
    };
    pollFrame();

    // API helpers
    function apiGet(url) {
        return fetch(url).then(function(r) { return r.ok ? r.json() : null; }).catch(function() { return null; });
    }
    function apiPost(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        }).then(function(r) { return r.ok ? r.json() : null; }).catch(function() { return null; });
    }

    // Build a track card using safe DOM methods (no innerHTML)
    function buildTrackCard(t, isLocked) {
        var card = document.createElement('div');
        card.className = 'track-card' + (isLocked ? ' locked' : '');

        var info = document.createElement('div');
        info.className = 'track-info';

        var label = document.createElement('div');
        label.className = 'track-label';
        label.textContent = t.label + ' #' + t.track_id;
        info.appendChild(label);

        var meta = document.createElement('div');
        meta.className = 'track-meta';
        var conf = document.createElement('span');
        conf.className = 'track-conf';
        conf.textContent = (t.confidence * 100).toFixed(0) + '%';
        meta.appendChild(conf);
        info.appendChild(meta);
        card.appendChild(info);

        var actions = document.createElement('div');
        actions.className = 'track-actions';

        if (isLocked) {
            var strikeBtn = document.createElement('button');
            strikeBtn.className = 'btn btn-strike';
            strikeBtn.textContent = 'STRIKE';
            strikeBtn.addEventListener('click', function() { confirmStrike(t.track_id); });
            actions.appendChild(strikeBtn);

            var unlockBtn = document.createElement('button');
            unlockBtn.className = 'btn btn-unlock';
            unlockBtn.textContent = 'UNLOCK';
            unlockBtn.addEventListener('click', doUnlock);
            actions.appendChild(unlockBtn);
        } else {
            var lockBtn = document.createElement('button');
            lockBtn.className = 'btn btn-lock';
            lockBtn.textContent = 'LOCK';
            lockBtn.addEventListener('click', function() { doLock(t.track_id); });
            actions.appendChild(lockBtn);
        }

        card.appendChild(actions);
        return card;
    }

    // Poll and render
    function poll() {
        Promise.all([
            apiGet('/api/tracks'),
            apiGet('/api/target'),
            apiGet('/api/stats'),
        ]).then(function(results) {
            var tracks = results[0];
            var target = results[1];
            var stats = results[2];

            // Status bar
            if (stats) {
                document.getElementById('s-fps').textContent = (stats.fps || 0).toFixed(1);
                document.getElementById('s-gps').textContent = stats.position || 'No fix';
                document.getElementById('s-tracks').textContent = stats.active_tracks || 0;
                var batt = stats.battery_pct;
                document.getElementById('s-batt').textContent = batt != null ? batt + '%' : '--';
                var armedWrap = document.getElementById('s-armed-wrap');
                armedWrap.style.display = stats.armed ? '' : 'none';
            }

            // Target lock state
            lockedTrackId = (target && target.locked) ? target.track_id : null;

            // Tracks list
            var container = document.getElementById('tracks');
            while (container.firstChild) { container.removeChild(container.firstChild); }

            if (!tracks || tracks.length === 0) {
                var empty = document.createElement('div');
                empty.className = 'tracks-empty';
                empty.textContent = 'No active tracks';
                container.appendChild(empty);
                return;
            }

            for (var i = 0; i < tracks.length; i++) {
                var t = tracks[i];
                container.appendChild(buildTrackCard(t, t.track_id === lockedTrackId));
            }
        });
    }

    function doLock(trackId) {
        apiPost('/api/target/lock', { track_id: trackId }).then(poll);
    }

    function doUnlock() {
        apiPost('/api/target/unlock', {}).then(poll);
    }

    // Strike confirmation
    var pendingStrikeId = null;
    function confirmStrike(trackId) {
        pendingStrikeId = trackId;
        document.getElementById('confirm-tid').textContent = '#' + trackId;
        document.getElementById('confirm-overlay').classList.add('active');
    }
    document.getElementById('confirm-yes').addEventListener('click', function() {
        document.getElementById('confirm-overlay').classList.remove('active');
        if (pendingStrikeId != null) {
            apiPost('/api/target/strike', { track_id: pendingStrikeId, confirm: true }).then(poll);
            pendingStrikeId = null;
        }
    });
    document.getElementById('confirm-no').addEventListener('click', function() {
        document.getElementById('confirm-overlay').classList.remove('active');
        pendingStrikeId = null;
    });

    // Start polling
    poll();
    setInterval(poll, 1500);
})();
