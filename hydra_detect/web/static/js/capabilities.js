'use strict';

(function () {
    var listEl = document.getElementById('cap-list');
    var loadingEl = document.getElementById('cap-loading');
    var tsEl = document.getElementById('cap-ts');

    var POLL_INTERVAL_MS = 3000;
    var pollTimer = null;

    // Status pill CSS class suffix
    var PILL_CLASS = {
        READY: 'cap-pill-READY',
        WARN: 'cap-pill-WARN',
        BLOCKED: 'cap-pill-BLOCKED',
        ARMED: 'cap-pill-ARMED',
    };

    function formatTs(isoStr) {
        try {
            var d = new Date(isoStr);
            return d.toLocaleTimeString([], {hour12: false});
        } catch (e) {
            return isoStr;
        }
    }

    function buildRow(cap) {
        var row = document.createElement('div');
        row.className = 'cap-row';
        row.setAttribute('role', 'listitem');

        var nameEl = document.createElement('span');
        nameEl.className = 'cap-name';
        nameEl.textContent = cap.name;
        row.appendChild(nameEl);

        var pillEl = document.createElement('span');
        pillEl.className = 'cap-pill ' + (PILL_CLASS[cap.status] || 'cap-pill-dim');
        pillEl.textContent = cap.status;
        row.appendChild(pillEl);

        if (cap.reasons && cap.reasons.length > 0) {
            var reasonsEl = document.createElement('div');
            reasonsEl.className = 'cap-reasons';
            // Each reason as a separate text node with line breaks
            cap.reasons.forEach(function (r, i) {
                if (i > 0) {
                    reasonsEl.appendChild(document.createElement('br'));
                }
                reasonsEl.appendChild(document.createTextNode(r));
            });
            if (cap.fix_target) {
                reasonsEl.appendChild(document.createTextNode(' '));
                var fixEl = document.createElement('a');
                fixEl.className = 'cap-fix';
                fixEl.textContent = cap.fix_target;
                // Link to GitHub issue if it looks like #NNN
                if (/^#\d+$/.test(cap.fix_target)) {
                    fixEl.href =
                        'https://github.com/rmeadomavic/Hydra/issues/' +
                        cap.fix_target.slice(1);
                    fixEl.target = '_blank';
                    fixEl.rel = 'noopener';
                } else {
                    fixEl.href = '#';
                }
                reasonsEl.appendChild(fixEl);
            }
            row.appendChild(reasonsEl);
        }

        return row;
    }

    function render(data) {
        // Remove all children except the loading element
        while (listEl.firstChild) {
            listEl.removeChild(listEl.firstChild);
        }

        if (!data || !data.capabilities) {
            var errEl = document.createElement('div');
            errEl.className = 'cap-error';
            errEl.textContent = 'No capability data received.';
            listEl.appendChild(errEl);
            return;
        }

        tsEl.textContent = formatTs(data.generated_at);

        data.capabilities.forEach(function (cap) {
            listEl.appendChild(buildRow(cap));
        });
    }

    function showError(msg) {
        while (listEl.firstChild) {
            listEl.removeChild(listEl.firstChild);
        }
        var errEl = document.createElement('div');
        errEl.className = 'cap-error';
        errEl.textContent = msg;
        listEl.appendChild(errEl);
    }

    async function poll() {
        try {
            var resp = await fetch('/api/capabilities');
            if (!resp.ok) {
                throw new Error('HTTP ' + resp.status);
            }
            var data = await resp.json();
            render(data);
        } catch (e) {
            showError('Failed to load capability status: ' + e.message);
        } finally {
            pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
        }
    }

    // Pause polling when tab is hidden
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
            if (pollTimer) {
                clearTimeout(pollTimer);
                pollTimer = null;
            }
        } else {
            poll();
        }
    });

    // Kick off
    poll();
})();
