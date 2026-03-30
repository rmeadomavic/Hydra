'use strict';

(function() {
    var cameraSelect = document.getElementById('setup-camera');
    var serialSelect = document.getElementById('setup-serial');
    var saveBtn = document.getElementById('setup-save');
    var statusEl = document.getElementById('setup-status');
    var callsignInput = document.getElementById('setup-callsign');
    var callsignPreview = document.getElementById('callsign-preview');
    var teamInput = document.getElementById('setup-team');
    var vehicleSelect = document.getElementById('setup-vehicle');

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
            callsignPreview.textContent = 'Enter team + vehicle for auto-callsign';
        }
    }
    teamInput.addEventListener('input', updateCallsignPreview);
    vehicleSelect.addEventListener('change', updateCallsignPreview);
    callsignInput.addEventListener('input', updateCallsignPreview);
    updateCallsignPreview();

    function clearSelect(sel) {
        while (sel.firstChild) {
            sel.removeChild(sel.firstChild);
        }
    }

    // Populate device dropdowns on load
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
                // Pre-select ttyTHS1 if present
                var tths1 = Array.from(serialSelect.options).find(
                    function(o) { return o.value === '/dev/ttyTHS1'; }
                );
                if (tths1) tths1.selected = true;
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
            vehicle_type: document.getElementById('setup-vehicle').value,
            team_number: document.getElementById('setup-team').value.trim(),
            callsign: document.getElementById('setup-callsign').value.trim(),
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
