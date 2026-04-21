'use strict';
(function() {
    var lockedTrackId = null;
    var apiToken = sessionStorage.getItem('hydra_token') || '';

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
    function authHeaders() {
        var headers = { 'Content-Type': 'application/json' };
        if (apiToken) headers.Authorization = 'Bearer ' + apiToken;
        return headers;
    }

    function setApiToken(token) {
        apiToken = token || '';
        sessionStorage.setItem('hydra_token', apiToken);
    }

    function promptForToken() {
        var token = prompt('API token required.\nEnter the api_token from config.ini:');
        if (!token) return false;
        setApiToken(token.trim());
        return !!apiToken;
    }

    // In-page toast replaces native alert() so feedback matches the ops
    // theme and doesn't block the operator with a modal on a tablet.
    var toastEl = document.getElementById('action-toast');
    var toastTimer = null;
    function showToast(msg, type) {
        if (!toastEl) return;
        toastEl.textContent = msg;
        toastEl.classList.toggle('error', type === 'error');
        toastEl.classList.add('show');
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(function() {
            toastEl.classList.remove('show');
        }, 3500);
    }

    function handleActionAuthFailure(resp) {
        if (resp && resp.status === 401 && resp.headers.get('x-login-required')) {
            window.location.href = '/login';
            return 'redirect';
        }
        if (resp && (resp.status === 401 || resp.status === 403)) {
            if (!promptForToken()) {
                showToast('Authentication required for control actions.', 'error');
                return 'stop';
            }
            return 'retry';
        }
        return 'stop';
    }

    function apiGet(url) {
        return fetch(url, { headers: authHeaders() })
            .then(function(r) { return r.ok ? r.json() : null; })
            .catch(function() { return null; });
    }

    function apiPost(url, body) {
        function send() {
            return fetch(url, {
                method: 'POST',
                headers: authHeaders(),
                body: JSON.stringify(body),
            });
        }

        return send().then(function(resp) {
            if (resp.ok) return resp.json();

            var action = handleActionAuthFailure(resp);
            if (action !== 'retry') return null;

            return send().then(function(retryResp) {
                if (retryResp.ok) return retryResp.json();
                return null;
            }).catch(function() { return null; });
        }).catch(function() { return null; });
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
                document.getElementById('s-gps').textContent = window.HydraSimGps ? window.HydraSimGps.withSimSuffix(stats.position || 'No fix') : (stats.position || 'No fix');
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
        apiPost('/api/target/unlock', {}).then(function(result) {
            if (result === null) {
                showToast('Unlock failed — check connection or token.', 'error');
            }
            poll();
        });
    }

    // Always-visible ABORT — cancels every approach mode and restores the
    // pre-approach vehicle mode. POST /api/abort is unauthenticated by
    // design so the operator can always reach it in an emergency.
    var abortBtn = document.getElementById('control-abort');
    if (abortBtn) {
        abortBtn.addEventListener('click', function() {
            // Visual feedback immediately — the fetch is fire-and-forget
            // safety-wise; the dashboard + platform will reflect state.
            abortBtn.classList.remove('header-abort-flash');
            void abortBtn.offsetWidth;
            abortBtn.classList.add('header-abort-flash');
            fetch('/api/abort', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            })
                .then(function(r) {
                    if (r.ok) {
                        showToast('ABORT sent — platform returning to safe state.');
                    } else {
                        showToast('ABORT request failed (HTTP ' + r.status + ').', 'error');
                    }
                })
                .catch(function() {
                    showToast('ABORT request failed — network error.', 'error');
                })
                .finally(function() { poll(); });
        });
    }

    function getConfirmFocusable() {
        var modal = document.querySelector('#confirm-overlay [role=\"dialog\"]');
        if (!modal) return [];
        return Array.prototype.slice.call(modal.querySelectorAll('button:not([disabled]), [tabindex]:not([tabindex=\"-1\"])'));
    }

    function openConfirmModal(triggerEl) {
        var overlay = document.getElementById('confirm-overlay');
        if (!overlay) return;
        overlay.__triggerElement = triggerEl || document.activeElement;
        overlay.classList.add('active');
        var modal = overlay.querySelector('[role=\"dialog\"]');
        var focusables = getConfirmFocusable();
        if (focusables.length) focusables[0].focus();
        else if (modal) modal.focus();
    }

    function closeConfirmModal() {
        var overlay = document.getElementById('confirm-overlay');
        if (!overlay) return;
        overlay.classList.remove('active');
        var trigger = overlay.__triggerElement;
        if (trigger && document.contains(trigger) && typeof trigger.focus === 'function') {
            trigger.focus();
        }
        overlay.__triggerElement = null;
    }

    document.addEventListener('keydown', function (e) {
        var overlay = document.getElementById('confirm-overlay');
        if (!overlay || !overlay.classList.contains('active')) return;

        if (e.key === 'Escape') {
            e.preventDefault();
            closeConfirmModal();
            pendingStrikeId = null;
            return;
        }

        if (e.key !== 'Tab') return;
        var focusables = getConfirmFocusable();
        if (!focusables.length) return;
        var first = focusables[0];
        var last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
        }
    });

    // Strike confirmation
    var pendingStrikeId = null;
    function confirmStrike(trackId) {
        pendingStrikeId = trackId;
        document.getElementById('confirm-tid').textContent = '#' + trackId;
        openConfirmModal(document.activeElement);
    }
    document.getElementById('confirm-yes').addEventListener('click', function() {
        closeConfirmModal();
        if (pendingStrikeId != null) {
            apiPost('/api/target/strike', { track_id: pendingStrikeId, confirm: true }).then(poll);
            pendingStrikeId = null;
        }
    });
    document.getElementById('confirm-no').addEventListener('click', function() {
        closeConfirmModal();
        pendingStrikeId = null;
    });

    // Start polling
    poll();
    setInterval(poll, 1500);
})();
