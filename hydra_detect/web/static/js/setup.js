'use strict';

(function() {
    var cameraSelect = document.getElementById('setup-camera');
    var serialSelect = document.getElementById('setup-serial');
    var saveBtn = document.getElementById('setup-save');
    var skipBtn = document.getElementById('setup-skip');
    var statusEl = document.getElementById('setup-status');
    var callsignInput = document.getElementById('setup-callsign');
    var callsignPreview = document.getElementById('callsign-preview');
    var teamInput = document.getElementById('setup-team');
    var vehicleSelect = document.getElementById('setup-vehicle');
    var takEnabledInput = document.getElementById('setup-tak-enabled');
    var takHostInput = document.getElementById('setup-tak-advertise-host');
    var takHostHint = document.getElementById('advertise-host-hint');
    var takAllowedInput = document.getElementById('setup-tak-allowed-callsigns');

    function updateCallsignPreview() {
        var cs = callsignInput.value.trim();
        if (cs) {
            callsignPreview.textContent = '';
            return;
        }
        var team = teamInput.value.trim();
        var veh = vehicleSelect.value;
        if (team && veh) {
            callsignPreview.textContent = 'Will use: HYDRA-' + team + '-' + veh.toUpperCase();
        } else {
            callsignPreview.textContent = 'Enter team + platform for auto-callsign';
        }
    }
    teamInput.addEventListener('input', updateCallsignPreview);
    vehicleSelect.addEventListener('change', updateCallsignPreview);
    callsignInput.addEventListener('input', updateCallsignPreview);
    updateCallsignPreview();

    // CSP-safe skip handler (replaces inline onclick).
    if (skipBtn) {
        skipBtn.addEventListener('click', function() { window.location = '/'; });
    }

    function clearSelect(sel) {
        while (sel.firstChild) {
            sel.removeChild(sel.firstChild);
        }
    }

    // Populate device dropdowns + LAN IP hint on load
    async function loadDevices() {
        statusEl.textContent = 'Detecting devices...';
        statusEl.className = 'setup-status setup-loading';
        try {
            var resp = await fetch('/api/setup/devices');
            if (!resp.ok) throw new Error('Failed to fetch devices');
            var data = await resp.json();

            // Populate cameras
            if (data.cameras && data.cameras.length > 0) {
                data.cameras.forEach(function(cam) {
                    var opt = document.createElement('option');
                    opt.value = cam.path;
                    opt.textContent = cam.name;
                    cameraSelect.appendChild(opt);
                });
            }

            // Populate serial ports
            if (data.serial_ports && data.serial_ports.length > 0) {
                clearSelect(serialSelect);
                data.serial_ports.forEach(function(port) {
                    var opt = document.createElement('option');
                    opt.value = port.path;
                    opt.textContent = port.name;
                    serialSelect.appendChild(opt);
                });
                var tths1 = Array.from(serialSelect.options).find(
                    function(o) { return o.value === '/dev/ttyTHS1'; }
                );
                if (tths1) tths1.selected = true;
            }

            // Pre-fill advertise host with detected LAN IP
            if (takHostInput && data.lan_ip) {
                takHostInput.placeholder = data.lan_ip;
                if (!takHostInput.value) {
                    takHostInput.value = data.lan_ip;
                }
                if (takHostHint) {
                    takHostHint.textContent =
                        'Auto-detected: ' + data.lan_ip +
                        '. Used for RTSP video links shown in ATAK markers. Override if wrong.';
                }
            }

            statusEl.textContent = '';
            statusEl.className = 'setup-status';
        } catch (err) {
            statusEl.textContent = 'Could not detect devices. Enter values manually.';
            statusEl.className = 'setup-status error';
        }
    }

    // Save configuration
    saveBtn.addEventListener('click', async function() {
        saveBtn.disabled = true;
        statusEl.textContent = 'Saving configuration...';
        statusEl.className = 'setup-status setup-loading';

        var payload = {
            camera_source: cameraSelect.value,
            serial_port: serialSelect.value,
            vehicle_type: vehicleSelect.value,
            team_number: teamInput.value.trim(),
            callsign: callsignInput.value.trim(),
            tak_enabled: takEnabledInput ? takEnabledInput.checked : undefined,
            tak_advertise_host: takHostInput ? takHostInput.value.trim() : '',
            tak_allowed_callsigns: takAllowedInput ? takAllowedInput.value.trim() : '',
        };

        try {
            var resp = await fetch('/api/setup/save', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            var data = await resp.json();
            if (!resp.ok) {
                throw new Error(data.error || 'Save failed');
            }
            var msg = 'Configuration saved';
            if (data.callsign) {
                msg += ' (' + data.callsign + ')';
            }
            msg += '. Redirecting...';
            statusEl.textContent = msg;
            statusEl.className = 'setup-status success';
            setTimeout(function() { window.location = '/'; }, 2000);
        } catch (err) {
            statusEl.textContent = err.message;
            statusEl.className = 'setup-status error';
            saveBtn.disabled = false;
        }
    });

    loadDevices();
})();
