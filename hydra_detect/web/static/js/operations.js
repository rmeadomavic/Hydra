'use strict';

/**
 * Hydra Detect v2.0 — Operations View Logic
 *
 * Reads from HydraApp.state (populated by centralized pollers) and
 * updates the 6 panels. Handles all user interactions (mode buttons,
 * sliders, target lock/strike, RF hunt, etc).
 */
const HydraOperations = (() => {
    let updateTimer = null;
    let isPaused = false;
    let selectedTrackId = null;
    let selectedTrackLabel = '';
    let pendingMode = null;
    let pendingModeTime = 0;
    let alertClassData = { all: [], categories: {}, selected: new Set() };
    let dropdownsLoaded = false;

    // ── Lifecycle ──
    function onEnter() {
        HydraPanels.init();
        initStreamWatcher();
        if (!dropdownsLoaded) {
            loadDropdowns();
            dropdownsLoaded = true;
        }
        wireEventHandlers();
        updateTimer = setInterval(updatePanels, 500);
        updatePanels();
    }

    function onLeave() {
        if (updateTimer) {
            clearInterval(updateTimer);
            updateTimer = null;
        }
    }

    // ── Load dropdowns (models, power modes, config, alert classes) ──
    async function loadDropdowns() {
        loadModels();
        loadPowerModes();
        loadConfig();
        loadAlertClasses();
        rfModeChanged();
        loadRTSPStatus();
        loadMAVLinkVideoStatus();
    }

    async function loadModels() {
        const data = await HydraApp.apiGet('/api/models');
        const sel = document.getElementById('ctrl-model-select');
        if (!sel || !data) return;
        clearChildren(sel);
        if (!data.length) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'No models in /models';
            sel.appendChild(opt);
            return;
        }
        for (const m of data) {
            const opt = document.createElement('option');
            opt.value = m.name;
            opt.textContent = m.name + ' (' + m.size_mb + ' MB)';
            if (m.active) opt.selected = true;
            sel.appendChild(opt);
        }
    }

    async function loadPowerModes() {
        const data = await HydraApp.apiGet('/api/system/power-modes');
        const sel = document.getElementById('ctrl-power-mode');
        if (!sel || !data) return;
        clearChildren(sel);
        if (!data.length) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'Not available';
            sel.appendChild(opt);
            return;
        }
        for (const m of data) {
            const opt = document.createElement('option');
            opt.value = m.id;
            opt.textContent = m.name;
            if (m.active) opt.selected = true;
            sel.appendChild(opt);
        }
    }

    async function loadConfig() {
        const data = await HydraApp.apiGet('/api/config');
        if (!data) return;
        if (data.threshold !== undefined) {
            const slider = document.getElementById('ctrl-thresh-slider');
            const val = document.getElementById('ctrl-thresh-val');
            if (slider) slider.value = data.threshold;
            if (val) val.textContent = data.threshold;
        }
    }

    async function loadAlertClasses() {
        const data = await HydraApp.apiGet('/api/config/alert-classes');
        if (!data) return;
        alertClassData.all = data.all_classes || [];
        alertClassData.categories = data.categories || {};
        const active = data.alert_classes || [];
        alertClassData.selected = new Set(active.length > 0 ? active : alertClassData.all);
        renderAlertClassList();
    }

    function renderAlertClassList() {
        const el = document.getElementById('ctrl-alert-class-list');
        if (!el) return;
        clearChildren(el);
        const cats = alertClassData.categories;
        for (const catName of Object.keys(cats)) {
            const classes = cats[catName];
            const isOther = catName === 'Other';
            const hdr = document.createElement('div');
            hdr.className = 'panel-alert-cat-hdr';
            hdr.textContent = (isOther ? '\u25B6 ' : '\u25BC ') + catName;
            const wrap = document.createElement('div');
            wrap.style.display = isOther ? 'none' : '';
            hdr.addEventListener('click', function () {
                const hidden = wrap.style.display === 'none';
                wrap.style.display = hidden ? '' : 'none';
                hdr.textContent = (wrap.style.display === 'none' ? '\u25B6 ' : '\u25BC ') + catName;
            });
            for (const cls of classes) {
                const row = document.createElement('label');
                row.className = 'panel-alert-class-row';
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.checked = alertClassData.selected.has(cls);
                cb.addEventListener('change', function () {
                    if (cb.checked) alertClassData.selected.add(cls);
                    else alertClassData.selected.delete(cls);
                });
                const span = document.createElement('span');
                span.textContent = cls;
                row.appendChild(cb);
                row.appendChild(span);
                wrap.appendChild(row);
            }
            el.appendChild(hdr);
            el.appendChild(wrap);
        }
    }

    // ── Event Handlers ──
    let handlersWired = false;
    function wireEventHandlers() {
        if (handlersWired) return;
        handlersWired = true;

        // Mode buttons
        document.querySelectorAll('#panel-vehicle .btn-mode').forEach(btn => {
            btn.addEventListener('click', () => commandMode(btn.dataset.mode));
        });

        // Target buttons
        addClick('ctrl-btn-lock', () => lockTarget());
        addClick('ctrl-btn-strike', () => showStrikeConfirm());
        addClick('ctrl-btn-release', () => unlockTarget());

        // Pipeline buttons
        addClick('ctrl-btn-pause', () => togglePause());
        addClick('ctrl-btn-stop', () => stopPipeline());

        // Power mode
        addChange('ctrl-power-mode', (e) => setPowerMode(e.target.value));

        // Model select
        addChange('ctrl-model-select', (e) => switchModel(e.target.value));

        // Confidence slider
        const slider = document.getElementById('ctrl-thresh-slider');
        if (slider) {
            slider.addEventListener('input', function () {
                const val = document.getElementById('ctrl-thresh-val');
                if (val) val.textContent = this.value;
            });
            slider.addEventListener('change', function () {
                updateThreshold();
            });
        }

        // Alert class buttons
        addClick('ctrl-alert-all', () => {
            alertClassData.selected = new Set(alertClassData.all);
            renderAlertClassList();
        });
        addClick('ctrl-alert-clear', () => {
            alertClassData.selected.clear();
            renderAlertClassList();
        });
        addClick('ctrl-alert-apply', () => applyAlertClasses());

        // RF Hunt
        addChange('ctrl-rf-mode', () => rfModeChanged());
        addClick('ctrl-btn-rf-start', () => rfStart());
        addClick('ctrl-btn-rf-stop', () => rfStop());

        // RTSP toggle
        addClick('ctrl-rtsp-toggle', () => toggleRTSP());
        const rtspUrl = document.getElementById('ctrl-rtsp-url');
        if (rtspUrl) {
            rtspUrl.addEventListener('click', () => {
                navigator.clipboard.writeText(rtspUrl.textContent);
                rtspUrl.title = 'Copied!';
                setTimeout(() => { rtspUrl.title = 'Click to copy'; }, 1500);
            });
        }

        // MAVLink Video
        addClick('ctrl-mvid-toggle', () => toggleMAVLinkVideo());
        const mvidRes = document.getElementById('ctrl-mvid-res');
        const mvidResVal = document.getElementById('ctrl-mvid-res-val');
        if (mvidRes) {
            mvidRes.addEventListener('input', function() {
                if (mvidResVal) mvidResVal.textContent = this.value;
            });
            mvidRes.addEventListener('change', function() {
                const v = parseInt(this.value);
                tuneMAVLinkVideo({ width: v, height: Math.round(v * 0.75) });
            });
        }
        const mvidQ = document.getElementById('ctrl-mvid-quality');
        const mvidQVal = document.getElementById('ctrl-mvid-quality-val');
        if (mvidQ) {
            mvidQ.addEventListener('input', function() {
                if (mvidQVal) mvidQVal.textContent = this.value;
            });
            mvidQ.addEventListener('change', function() {
                tuneMAVLinkVideo({ quality: parseInt(this.value) });
            });
        }
    }

    function addClick(id, handler) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', handler);
    }

    function addChange(id, handler) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', handler);
    }

    // ── Panel Updates (reads HydraApp.state) ──
    function updatePanels() {
        const s = HydraApp.state.stats;
        if (s && Object.keys(s).length > 0) {
            updateVehiclePanel(s);
            updatePipelinePanel(s);
        }
        updateTargetPanel();
        updateDetectionLog();
        updateRFPanel();
        updateLockOverlay();
    }

    // ── Lock Overlay (on video) ──
    function updateLockOverlay() {
        const t = HydraApp.state.target;
        const el = document.getElementById('ops-lock-indicator');
        if (!el) return;
        if (t.locked) {
            el.style.display = '';
            const labelEl = document.getElementById('lock-label');
            const modeEl = document.getElementById('lock-mode');
            if (labelEl) labelEl.textContent = '#' + t.track_id + ' ' + (t.label || '');
            if (modeEl) modeEl.textContent = (t.mode || 'track').toUpperCase();
            el.classList.toggle('strike', t.mode === 'strike');
        } else {
            el.style.display = 'none';
        }
    }

    // ── Vehicle Panel ──
    function updateVehiclePanel(s) {
        // Mode badge
        const modeBadge = document.getElementById('ctrl-mode-badge');
        if (modeBadge && !modeBadge.classList.contains('sending')) {
            const mode = s.vehicle_mode || '--';
            modeBadge.textContent = mode;
            modeBadge.className = 'badge mode-badge ' + (mode !== '--' ? mode.toLowerCase() : '');
        }

        // Check pending mode confirmation
        if (pendingMode && s.vehicle_mode) {
            if (s.vehicle_mode === pendingMode) {
                pendingMode = null;
            } else if (Date.now() - pendingModeTime > 3000) {
                if (modeBadge) modeBadge.className = 'badge mode-badge failed';
                setTimeout(function () { pendingMode = null; }, 1000);
            }
        }

        // Armed badge
        const armedBadge = document.getElementById('ctrl-armed-badge');
        if (armedBadge && s.armed !== undefined) {
            armedBadge.textContent = s.armed ? 'ARMED' : 'DISARMED';
            armedBadge.className = 'badge armed-badge ' + (s.armed ? 'armed' : 'disarmed');
        }

        // Battery
        const battEl = document.getElementById('ctrl-battery');
        if (battEl && s.battery_v != null) {
            const pct = (s.battery_pct != null && s.battery_pct >= 0) ? ' ' + s.battery_pct + '%' : '';
            battEl.textContent = s.battery_v.toFixed(1) + 'V' + pct;
            const bpct = s.battery_pct != null ? s.battery_pct : 100;
            battEl.style.color = bpct > 40 ? '#4a7c2e' : bpct > 20 ? '#eab308' : '#c53030';
        }

        // Speed / Alt / Heading
        setText('ctrl-speed', s.groundspeed != null ? s.groundspeed.toFixed(1) + ' m/s' : null);
        setText('ctrl-alt', s.altitude_m != null ? s.altitude_m.toFixed(1) + ' m' : null);
        setText('ctrl-heading', s.heading_deg != null ? Math.round(s.heading_deg) + '\u00B0' : null);

        // GPS
        setText('ctrl-gps-fix', (!s.mavlink || s.gps_fix === undefined) ? '--' : (s.gps_fix === 0 ? 'No Fix' : s.gps_fix + 'D'));
        setText('ctrl-gps-pos', s.position || '--');
    }

    // ── Pipeline Panel ──
    function updatePipelinePanel(s) {
        setText('ctrl-fps', s.fps != null ? s.fps.toFixed(1) : '--');
        setText('ctrl-inference', s.inference_ms != null ? s.inference_ms.toFixed(1) + ' ms' : '--');
        setText('ctrl-detector', (s.detector || 'yolo').toUpperCase());

        // System stats color bars
        if (s.gpu_temp_c != null) {
            const load = s.gpu_load_pct != null ? s.gpu_load_pct : 0;
            const el = document.getElementById('ctrl-gpu-temp');
            if (el) {
                el.textContent = s.gpu_temp_c + '\u00B0C  ' + load.toFixed(0) + '%';
                const c = levelColor(s.gpu_temp_c);
                el.style.color = c;
                setBar('ctrl-gpu-bar', load, c);
            }
        }

        if (s.cpu_temp_c != null) {
            const el = document.getElementById('ctrl-cpu-temp');
            if (el) {
                el.textContent = s.cpu_temp_c + '\u00B0C';
                const c = levelColor(s.cpu_temp_c);
                el.style.color = c;
                setBar('ctrl-cpu-bar', s.cpu_temp_c, c);
            }
        }

        if (s.ram_used_mb != null && s.ram_total_mb != null) {
            const el = document.getElementById('ctrl-ram-usage');
            if (el) {
                const pct = Math.round(s.ram_used_mb / s.ram_total_mb * 100);
                el.textContent = (s.ram_used_mb / 1024).toFixed(1) + ' / ' + (s.ram_total_mb / 1024).toFixed(1) + ' GB';
                const c = levelColor(pct);
                el.style.color = c;
                setBar('ctrl-ram-bar', pct, c);
            }
        }

        // Refresh RTSP client count from stats
        if (s.rtsp_clients !== undefined) {
            const status = document.getElementById('ctrl-rtsp-status');
            if (status && document.getElementById('ctrl-rtsp-toggle')?.classList.contains('active')) {
                status.textContent = s.rtsp_clients > 0
                    ? s.rtsp_clients + ' client' + (s.rtsp_clients !== 1 ? 's' : '')
                    : 'ON';
            }
        }

        if (s.mavlink_video_fps !== undefined) {
            const status = document.getElementById('ctrl-mvid-status');
            if (status && document.getElementById('ctrl-mvid-toggle')?.classList.contains('active')) {
                const kbps = (s.mavlink_video_kbps || 0).toFixed(1);
                status.textContent = s.mavlink_video_fps.toFixed(1) + ' FPS / ' + kbps + ' KB/s';
            }
        }
    }

    // ── Target Panel ──
    function updateTargetPanel() {
        const tracks = HydraApp.state.tracks;
        const target = HydraApp.state.target;
        const list = document.getElementById('ctrl-track-list');
        if (!list) return;

        // Track list with per-track action buttons
        if (!tracks || tracks.length === 0) {
            clearChildren(list);
            const empty = document.createElement('div');
            empty.className = 'panel-track-empty';
            empty.textContent = 'No tracks';
            list.appendChild(empty);
        } else {
            clearChildren(list);
            for (const t of tracks) {
                const isLocked = target && target.locked && target.track_id === t.track_id;
                const div = document.createElement('div');
                div.className = 'panel-track-item' + (isLocked ? ' locked' : '');

                // Info column: ID + label + confidence
                const info = document.createElement('div');
                info.className = 'track-info';
                const tid = document.createElement('span');
                tid.className = 'track-id';
                tid.textContent = '#' + t.track_id;
                const tl = document.createElement('span');
                tl.className = 'track-label';
                tl.textContent = t.label;
                const tc = document.createElement('span');
                tc.className = 'track-conf';
                tc.textContent = (t.confidence * 100).toFixed(0) + '%';
                info.appendChild(tid);
                info.appendChild(tl);
                info.appendChild(tc);

                // Action buttons column
                const actions = document.createElement('div');
                actions.className = 'track-actions';

                if (isLocked) {
                    const modeLabel = document.createElement('span');
                    modeLabel.className = 'track-lock-badge' + (target.mode === 'strike' ? ' strike' : '');
                    modeLabel.textContent = (target.mode || 'track').toUpperCase();
                    actions.appendChild(modeLabel);
                } else {
                    const lockBtn = document.createElement('button');
                    lockBtn.className = 'btn btn-sm btn-green track-btn';
                    lockBtn.textContent = 'Lock';
                    lockBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        HydraApp.apiPost('/api/target/lock', { track_id: t.track_id });
                    });
                    actions.appendChild(lockBtn);

                    const strikeBtn = document.createElement('button');
                    strikeBtn.className = 'btn btn-sm btn-danger track-btn';
                    strikeBtn.textContent = 'Strike';
                    strikeBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        selectedTrackId = t.track_id;
                        selectedTrackLabel = t.label;
                        showStrikeConfirm();
                    });
                    actions.appendChild(strikeBtn);
                }

                div.appendChild(info);
                div.appendChild(actions);
                list.appendChild(div);
            }
        }

        // Lock indicator
        const lockEl = document.getElementById('ctrl-lock-indicator');
        if (lockEl && target) {
            if (target.locked) {
                lockEl.style.display = 'block';
                if (target.mode === 'strike') {
                    lockEl.className = 'panel-lock-indicator strike-active';
                    lockEl.textContent = 'STRIKE: #' + target.track_id + ' ' + (target.label || '');
                } else {
                    lockEl.className = 'panel-lock-indicator tracking';
                    lockEl.textContent = 'TRACKING: #' + target.track_id + ' ' + (target.label || '');
                }
                const releaseBtn = document.getElementById('ctrl-btn-release');
                if (releaseBtn) releaseBtn.disabled = false;
            } else {
                lockEl.style.display = 'none';
                const releaseBtn = document.getElementById('ctrl-btn-release');
                if (releaseBtn) releaseBtn.disabled = true;
            }
        }
    }

    // ── Detection Log ──
    function updateDetectionLog() {
        const dets = HydraApp.state.detections;
        const log = document.getElementById('ctrl-det-log');
        if (!log || !dets || dets.length === 0) return;

        clearChildren(log);
        for (let i = dets.length - 1; i >= 0; i--) {
            const d = dets[i];
            const time = d.timestamp ? d.timestamp.split('T')[1].split('.')[0] : '';
            const div = document.createElement('div');
            div.className = 'panel-det-entry';

            const ts = document.createElement('span');
            ts.className = 'panel-det-time';
            ts.textContent = time;

            const lb = document.createElement('span');
            lb.className = 'panel-det-label';
            lb.textContent = d.label;

            const cf = document.createElement('span');
            cf.className = 'panel-det-conf';
            cf.textContent = (d.confidence * 100).toFixed(0) + '%';

            div.appendChild(ts);
            div.appendChild(lb);
            div.appendChild(cf);

            if (d.lat != null && d.lon != null) {
                const ps = document.createElement('span');
                ps.className = 'panel-det-pos';
                ps.textContent = d.lat.toFixed(5) + ',' + d.lon.toFixed(5);
                div.appendChild(ps);
            }

            log.appendChild(div);
        }
    }

    // ── RF Panel ──
    const RF_STATE_COLORS = {
        idle: 'off', searching: 'on', homing: 'on',
        converged: 'on', lost: 'off', aborted: 'off', unavailable: 'off'
    };
    const RF_STATE_LABELS = {
        idle: 'IDLE', searching: 'SEARCHING', homing: 'HOMING',
        converged: 'CONVERGED', lost: 'LOST', aborted: 'ABORTED', unavailable: 'N/A'
    };

    // ── RF Visualization ──
    function renderRssiSparkline(data, thresholds) {
        var container = document.getElementById('ctrl-rf-rssi-chart');
        if (!container || !data || data.length < 2) {
            if (container) {
                while (container.firstChild) container.removeChild(container.firstChild);
            }
            return;
        }

        var W = container.clientWidth || 300;
        var H = container.clientHeight || 120;
        var PAD = { top: 10, right: 10, bottom: 20, left: 40 };
        var plotW = W - PAD.left - PAD.right;
        var plotH = H - PAD.top - PAD.bottom;

        var yMin = -100, yMax = -20;
        var yScale = function(v) { return PAD.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH; };
        var tMin = data[0].t, tMax = data[data.length - 1].t;
        var tSpan = Math.max(tMax - tMin, 1);
        var xScale = function(t) { return PAD.left + ((t - tMin) / tSpan) * plotW; };

        var points = data.map(function(d) {
            return xScale(d.t).toFixed(1) + ',' + yScale(d.rssi).toFixed(1);
        }).join(' ');

        // Trend color
        var recent = data.slice(-10);
        var trend = 'var(--color-warn)';
        if (recent.length >= 2) {
            var diff = recent[recent.length - 1].rssi - recent[0].rssi;
            if (diff > 3) trend = 'var(--color-ok)';
            else if (diff < -3) trend = 'var(--color-danger)';
        }

        var detectTh = thresholds.detect || -80;
        var convergeTh = thresholds.converge || -40;

        var ns = 'http://www.w3.org/2000/svg';
        var svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('width', W);
        svg.setAttribute('height', H);
        svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);

        function svgEl(tag, attrs) {
            var el = document.createElementNS(ns, tag);
            for (var k in attrs) {
                if (attrs.hasOwnProperty(k)) el.setAttribute(k, attrs[k]);
            }
            return el;
        }

        // Background
        svg.appendChild(svgEl('rect', {
            x: PAD.left, y: PAD.top, width: plotW, height: plotH,
            fill: 'rgba(0,0,0,0.2)', rx: '2'
        }));

        // Threshold dashed lines
        [
            { val: detectTh, label: 'det ' + detectTh },
            { val: convergeTh, label: 'conv ' + convergeTh }
        ].forEach(function(th) {
            var y = yScale(th.val);
            if (y >= PAD.top && y <= PAD.top + plotH) {
                svg.appendChild(svgEl('line', {
                    x1: PAD.left, y1: y, x2: PAD.left + plotW, y2: y,
                    stroke: 'rgba(255,255,255,0.3)', 'stroke-dasharray': '4,3', 'stroke-width': '1'
                }));
                var text = document.createElementNS(ns, 'text');
                text.setAttribute('x', PAD.left + 3);
                text.setAttribute('y', y - 3);
                text.setAttribute('fill', 'rgba(255,255,255,0.5)');
                text.setAttribute('font-size', '9');
                text.textContent = th.label;
                svg.appendChild(text);
            }
        });

        // Data polyline
        svg.appendChild(svgEl('polyline', {
            points: points, fill: 'none', stroke: trend, 'stroke-width': '1.5'
        }));

        // Y-axis labels
        [-100, -80, -60, -40, -20].forEach(function(v) {
            var text = document.createElementNS(ns, 'text');
            text.setAttribute('x', PAD.left - 3);
            text.setAttribute('y', yScale(v) + 3);
            text.setAttribute('fill', 'rgba(255,255,255,0.4)');
            text.setAttribute('font-size', '9');
            text.setAttribute('text-anchor', 'end');
            text.textContent = v;
            svg.appendChild(text);
        });

        // X-axis "now" label
        var xLabel = document.createElementNS(ns, 'text');
        xLabel.setAttribute('x', PAD.left + plotW);
        xLabel.setAttribute('y', H - 3);
        xLabel.setAttribute('fill', 'rgba(255,255,255,0.4)');
        xLabel.setAttribute('font-size', '9');
        xLabel.setAttribute('text-anchor', 'end');
        xLabel.textContent = 'now';
        svg.appendChild(xLabel);

        // Replace container content using DOM methods
        while (container.firstChild) container.removeChild(container.firstChild);
        container.appendChild(svg);
    }

    function renderSignalMap(data) {
        var canvas = document.getElementById('ctrl-rf-signal-map');
        if (!canvas || !data || data.length < 1) return;
        var ctx = canvas.getContext('2d');

        var rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * (window.devicePixelRatio || 1);
        canvas.height = rect.height * (window.devicePixelRatio || 1);
        ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
        var W = rect.width, H = rect.height;

        ctx.clearRect(0, 0, W, H);

        var gpsData = data.filter(function(d) { return d.lat != null && d.lon != null; });
        if (gpsData.length === 0) return;

        var minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
        gpsData.forEach(function(d) {
            if (d.lat < minLat) minLat = d.lat;
            if (d.lat > maxLat) maxLat = d.lat;
            if (d.lon < minLon) minLon = d.lon;
            if (d.lon > maxLon) maxLon = d.lon;
        });

        var latSpan = Math.max(maxLat - minLat, 0.00005);
        var lonSpan = Math.max(maxLon - minLon, 0.00005);
        var padFrac = 0.1;
        minLat -= latSpan * padFrac; maxLat += latSpan * padFrac;
        minLon -= lonSpan * padFrac; maxLon += lonSpan * padFrac;

        var PAD = 15;
        var plotW = W - 2 * PAD, plotH = H - 2 * PAD;
        function toX(lon) { return PAD + ((lon - minLon) / (maxLon - minLon)) * plotW; }
        function toY(lat) { return PAD + plotH - ((lat - minLat) / (maxLat - minLat)) * plotH; }

        ctx.fillStyle = 'rgba(0,0,0,0.2)';
        ctx.fillRect(PAD, PAD, plotW, plotH);

        var rf = HydraApp.state.rfStatus || {};
        var detectTh = rf.rssi_threshold || -80;
        var convergeTh = rf.rssi_converge || -40;

        var bestIdx = 0;
        gpsData.forEach(function(d, i) {
            if (d.rssi > gpsData[bestIdx].rssi) bestIdx = i;
        });

        // Draw dots
        gpsData.forEach(function(d, i) {
            var alpha = 0.3 + 0.7 * (i / (gpsData.length - 1 || 1));
            var color;
            if (d.rssi >= convergeTh) color = 'rgba(74,124,46,' + alpha + ')';
            else if (d.rssi >= detectTh) color = 'rgba(234,179,8,' + alpha + ')';
            else color = 'rgba(197,48,48,' + alpha + ')';

            ctx.beginPath();
            ctx.arc(toX(d.lon), toY(d.lat), 4, 0, Math.PI * 2);
            ctx.fillStyle = color;
            ctx.fill();
        });

        // Current position triangle
        var last = gpsData[gpsData.length - 1];
        var cx = toX(last.lon), cy = toY(last.lat);
        ctx.beginPath();
        ctx.moveTo(cx, cy - 7);
        ctx.lineTo(cx - 5, cy + 4);
        ctx.lineTo(cx + 5, cy + 4);
        ctx.closePath();
        ctx.fillStyle = '#fff';
        ctx.fill();

        // Best position star
        if (bestIdx !== gpsData.length - 1) {
            var best = gpsData[bestIdx];
            var bx = toX(best.lon), by = toY(best.lat);
            ctx.beginPath();
            for (var j = 0; j < 10; j++) {
                var angle = -Math.PI / 2 + j * (Math.PI / 5);
                var r = j % 2 === 0 ? 6 : 3;
                if (j === 0) ctx.moveTo(bx + r * Math.cos(angle), by + r * Math.sin(angle));
                else ctx.lineTo(bx + r * Math.cos(angle), by + r * Math.sin(angle));
            }
            ctx.closePath();
            ctx.fillStyle = '#ffd700';
            ctx.fill();
        }
    }

    function updateRFPanel() {
        const rf = HydraApp.state.rfStatus;
        if (!rf) return;

        const state = rf.state || 'unavailable';
        const badge = document.getElementById('ctrl-rf-state-badge');
        if (badge) {
            badge.className = 'badge ' + (RF_STATE_COLORS[state] || 'off');
            badge.textContent = RF_STATE_LABELS[state] || state.toUpperCase();
            if (state === 'homing') {
                badge.style.animation = 'pulse-glow 1.5s ease-in-out infinite';
            } else {
                badge.style.animation = '';
            }
            if (state === 'converged') {
                badge.style.background = '#4a7c2e';
                badge.style.color = '#fff';
            }
        }

        const isActive = ['searching', 'homing', 'lost'].includes(state);
        const isDone = ['converged', 'aborted'].includes(state);

        const statusPanel = document.getElementById('ctrl-rf-status-panel');
        const configPanel = document.getElementById('ctrl-rf-config-panel');
        if (statusPanel) statusPanel.style.display = (isActive || isDone) ? '' : 'none';
        if (configPanel) configPanel.style.display = isActive ? 'none' : '';

        const startBtn = document.getElementById('ctrl-btn-rf-start');
        const stopBtn = document.getElementById('ctrl-btn-rf-stop');
        if (startBtn) startBtn.disabled = isActive;
        if (stopBtn) stopBtn.disabled = !isActive;

        if (isActive || isDone) {
            const rssi = rf.best_rssi != null ? rf.best_rssi : -100;
            setText('ctrl-rf-best-rssi', rssi.toFixed(0) + ' dBm');
            setText('ctrl-rf-samples', rf.samples || 0);
            setText('ctrl-rf-wp', rf.wp_progress || '--');

            if (rf.best_lat && rf.best_lon) {
                setText('ctrl-rf-best-pos', 'Best: ' + rf.best_lat.toFixed(6) + ', ' + rf.best_lon.toFixed(6));
            }

            // Signal bar
            const pct = Math.max(0, Math.min(100, (rssi + 100)));
            const barColor = pct > 60 ? '#4a7c2e' : pct > 30 ? '#eab308' : '#c53030';
            setBar('ctrl-rf-rssi-bar', pct, barColor);

            const rssiLabel = document.getElementById('ctrl-rf-rssi-label');
            if (rssiLabel) {
                rssiLabel.textContent = rssi.toFixed(0) + ' dBm';
                rssiLabel.style.color = barColor;
            }

            // Fetch and render RSSI history charts
            if (isActive) {
                fetch('/api/rf/rssi_history')
                    .then(function(r) { return r.json(); })
                    .then(function(historyData) {
                        var rf = HydraApp.state.rfStatus || {};
                        renderRssiSparkline(historyData, {
                            detect: rf.rssi_threshold || -80,
                            converge: rf.rssi_converge || -40
                        });
                        renderSignalMap(historyData);
                    })
                    .catch(function() {});
            }
        }
    }

    // ── Actions ──
    async function commandMode(mode) {
        const labels = {
            LOITER: 'Command vehicle to LOITER?',
            AUTO: 'Resume AUTO mission?',
            RTL: 'Return to Launch?'
        };
        if (!confirm(labels[mode] || ('Set mode to ' + mode + '?'))) return;

        const result = await HydraApp.apiPost('/api/vehicle/mode', { mode: mode });
        if (result && result.status === 'ok') {
            const badge = document.getElementById('ctrl-mode-badge');
            if (badge) {
                badge.textContent = mode + '...';
                badge.className = 'badge mode-badge sending';
            }
            pendingMode = mode;
            pendingModeTime = Date.now();
        }
    }

    async function lockTarget() {
        if (selectedTrackId === null) return;
        const result = await HydraApp.apiPost('/api/target/lock', { track_id: selectedTrackId });
        if (result && result.status === 'ok') {
            const releaseBtn = document.getElementById('ctrl-btn-release');
            if (releaseBtn) releaseBtn.disabled = false;
        }
    }

    async function unlockTarget() {
        await HydraApp.apiPost('/api/target/unlock', {});
        selectedTrackId = null;
        selectedTrackLabel = '';
        const lockBtn = document.getElementById('ctrl-btn-lock');
        const strikeBtn = document.getElementById('ctrl-btn-strike');
        const releaseBtn = document.getElementById('ctrl-btn-release');
        const lockIndicator = document.getElementById('ctrl-lock-indicator');
        if (lockBtn) lockBtn.disabled = true;
        if (strikeBtn) strikeBtn.disabled = true;
        if (releaseBtn) releaseBtn.disabled = true;
        if (lockIndicator) lockIndicator.style.display = 'none';
    }

    function showStrikeConfirm() {
        if (selectedTrackId === null) return;
        const label = document.getElementById('strike-target-label');
        if (label) label.textContent = '#' + selectedTrackId + ' (' + selectedTrackLabel + ')';
        const modal = document.getElementById('strike-modal');
        if (modal) modal.classList.add('active');

        // Wire confirm/cancel (idempotent via replaceWith clone)
        wireStrikeModal();
    }

    function wireStrikeModal() {
        const confirmBtn = document.getElementById('strike-confirm');
        const cancelBtn = document.getElementById('strike-cancel');

        if (confirmBtn) {
            const clone = confirmBtn.cloneNode(true);
            confirmBtn.parentNode.replaceChild(clone, confirmBtn);
            clone.addEventListener('click', async () => {
                const modal = document.getElementById('strike-modal');
                if (modal) modal.classList.remove('active');
                if (selectedTrackId === null) return;
                const result = await HydraApp.apiPost('/api/target/strike', {
                    track_id: selectedTrackId,
                    confirm: true
                });
                if (result && result.status === 'ok') {
                    const releaseBtn = document.getElementById('ctrl-btn-release');
                    if (releaseBtn) releaseBtn.disabled = false;
                }
            });
        }

        if (cancelBtn) {
            const clone = cancelBtn.cloneNode(true);
            cancelBtn.parentNode.replaceChild(clone, cancelBtn);
            clone.addEventListener('click', () => {
                const modal = document.getElementById('strike-modal');
                if (modal) modal.classList.remove('active');
            });
        }
    }

    async function togglePause() {
        isPaused = !isPaused;
        const result = await HydraApp.apiPost('/api/pipeline/pause', { paused: isPaused });
        if (!result) {
            isPaused = !isPaused;
            return;
        }
        const btn = document.getElementById('ctrl-btn-pause');
        if (btn) {
            btn.textContent = isPaused ? 'Resume' : 'Pause';
            if (isPaused) {
                btn.classList.add('btn-green');
            } else {
                btn.classList.remove('btn-green');
            }
        }
    }

    async function stopPipeline() {
        if (!confirm('Stop Hydra Detect?')) return;
        await HydraApp.apiPost('/api/pipeline/stop', {});
    }

    async function setPowerMode(modeId) {
        if (!modeId) return;
        const sel = document.getElementById('ctrl-power-mode');
        if (sel) sel.disabled = true;
        const result = await HydraApp.apiPost('/api/system/power-mode', { mode_id: parseInt(modeId) });
        if (!result || result.status !== 'ok') {
            HydraApp.showToast('Failed to set power mode');
        }
        setTimeout(() => {
            loadPowerModes();
            if (sel) sel.disabled = false;
        }, 2000);
    }

    async function switchModel(modelName) {
        if (!modelName) return;
        const sel = document.getElementById('ctrl-model-select');
        if (sel) sel.disabled = true;
        const result = await HydraApp.apiPost('/api/models/switch', { model: modelName });
        if (result && result.status === 'ok') {
            loadAlertClasses();
        } else {
            loadModels();
        }
        if (sel) sel.disabled = false;
    }

    async function updateThreshold() {
        const slider = document.getElementById('ctrl-thresh-slider');
        if (!slider) return;
        const threshold = parseFloat(slider.value);
        await HydraApp.apiPost('/api/config/threshold', { threshold: threshold });
    }

    async function applyAlertClasses() {
        const classes = alertClassData.selected.size === alertClassData.all.length
            ? []
            : Array.from(alertClassData.selected);
        await HydraApp.apiPost('/api/config/alert-classes', { classes: classes });
    }

    // ── RF Hunt ──
    function rfModeChanged() {
        const modeEl = document.getElementById('ctrl-rf-mode');
        if (!modeEl) return;
        const mode = modeEl.value;
        const bssidGroup = document.getElementById('ctrl-rf-bssid-group');
        const freqGroup = document.getElementById('ctrl-rf-freq-group');
        if (bssidGroup) bssidGroup.style.display = mode === 'wifi' ? '' : 'none';
        if (freqGroup) freqGroup.style.display = mode === 'sdr' ? '' : 'none';
    }

    async function rfStart() {
        const modeEl = document.getElementById('ctrl-rf-mode');
        if (!modeEl) return;
        const mode = modeEl.value;
        const bssid = (document.getElementById('ctrl-rf-bssid') || {}).value || '';
        const freq = parseFloat((document.getElementById('ctrl-rf-freq') || {}).value || '0');

        if (mode === 'wifi' && !bssid.trim()) {
            HydraApp.showToast('Enter target BSSID (MAC address)');
            return;
        }
        if (mode === 'sdr' && (isNaN(freq) || freq < 1)) {
            HydraApp.showToast('Enter valid frequency in MHz');
            return;
        }

        const target = mode === 'wifi' ? bssid.trim() : freq + ' MHz';
        if (!confirm('Start RF hunt?\n\nMode: ' + mode.toUpperCase() + '\nTarget: ' + target + '\n\nVehicle will switch to GUIDED mode.')) return;

        const body = {
            mode: mode,
            target_bssid: mode === 'wifi' ? bssid.trim() : '',
            target_freq_mhz: freq,
            search_pattern: (document.getElementById('ctrl-rf-pattern') || {}).value || 'lawnmower',
            search_area_m: parseFloat((document.getElementById('ctrl-rf-area') || {}).value || '200'),
            search_spacing_m: parseFloat((document.getElementById('ctrl-rf-spacing') || {}).value || '30'),
            search_alt_m: parseFloat((document.getElementById('ctrl-rf-alt') || {}).value || '30'),
            gradient_step_m: parseFloat((document.getElementById('ctrl-rf-step') || {}).value || '10'),
            rssi_threshold_dbm: parseFloat((document.getElementById('ctrl-rf-thresh') || {}).value || '-80'),
            rssi_converge_dbm: parseFloat((document.getElementById('ctrl-rf-converge') || {}).value || '-40'),
        };

        const result = await HydraApp.apiPost('/api/rf/start', body);
        if (result && result.status === 'ok') {
            const startBtn = document.getElementById('ctrl-btn-rf-start');
            const stopBtn = document.getElementById('ctrl-btn-rf-stop');
            if (startBtn) startBtn.disabled = true;
            if (stopBtn) stopBtn.disabled = false;
        }
    }

    async function rfStop() {
        if (!confirm('Abort RF hunt?')) return;
        await HydraApp.apiPost('/api/rf/stop', {});
        const startBtn = document.getElementById('ctrl-btn-rf-start');
        const stopBtn = document.getElementById('ctrl-btn-rf-stop');
        if (startBtn) startBtn.disabled = false;
        if (stopBtn) stopBtn.disabled = true;
    }

    // -- RTSP ----------------------------------------------------------

    async function loadRTSPStatus() {
        const data = await HydraApp.apiGet('/api/rtsp/status');
        if (!data) return;
        const toggle = document.getElementById('ctrl-rtsp-toggle');
        const status = document.getElementById('ctrl-rtsp-status');
        const urlEl = document.getElementById('ctrl-rtsp-url');
        if (!toggle || !status) return;

        if (data.running) {
            toggle.classList.add('active');
            status.textContent = data.clients > 0
                ? data.clients + ' client' + (data.clients !== 1 ? 's' : '')
                : 'ON';
            if (urlEl) {
                urlEl.textContent = data.url;
                urlEl.style.display = 'block';
            }
        } else {
            toggle.classList.remove('active');
            status.textContent = 'OFF';
            if (urlEl) urlEl.style.display = 'none';
        }
    }

    async function toggleRTSP() {
        const toggle = document.getElementById('ctrl-rtsp-toggle');
        if (!toggle) return;
        const nowActive = toggle.classList.contains('active');
        const resp = await HydraApp.apiPost('/api/rtsp/toggle', { enabled: !nowActive });
        if (resp) loadRTSPStatus();
    }

    // -- MAVLink Video --------------------------------------------------

    async function loadMAVLinkVideoStatus() {
        const data = await HydraApp.apiGet('/api/mavlink-video/status');
        if (!data) return;
        const toggle = document.getElementById('ctrl-mvid-toggle');
        const status = document.getElementById('ctrl-mvid-status');
        const details = document.getElementById('ctrl-mvid-details');
        if (!toggle || !status) return;

        if (data.running) {
            toggle.classList.add('active');
            const kbps = (data.bytes_per_sec / 1024).toFixed(1);
            status.textContent = data.current_fps.toFixed(1) + ' FPS / ' + kbps + ' KB/s';
            if (details) details.style.display = 'block';
        } else {
            toggle.classList.remove('active');
            status.textContent = 'OFF';
            if (details) details.style.display = 'none';
        }
    }

    async function toggleMAVLinkVideo() {
        const toggle = document.getElementById('ctrl-mvid-toggle');
        if (!toggle) return;
        const nowActive = toggle.classList.contains('active');
        await HydraApp.apiPost('/api/mavlink-video/toggle', { enabled: !nowActive });
        loadMAVLinkVideoStatus();
    }

    async function tuneMAVLinkVideo(params) {
        await HydraApp.apiPost('/api/mavlink-video/tune', params);
    }

    // ── Stream Watcher ──
    function initStreamWatcher() {
        const img = document.getElementById('mjpeg-stream');
        const loading = document.getElementById('ops-loading');
        const lost = document.getElementById('ops-stream-lost');
        if (!img) return;

        if (img.complete && img.naturalWidth > 0) {
            if (loading) loading.style.display = 'none';
        }

        img.addEventListener('load', () => {
            if (loading) loading.style.display = 'none';
            if (lost) lost.style.display = 'none';
        }, { once: true });
    }

    // ── Helpers ──
    function setText(id, value) {
        if (value === null || value === undefined) return;
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    function clearChildren(el) {
        while (el.firstChild) el.removeChild(el.firstChild);
    }

    function levelColor(pct) {
        if (pct > 90) return '#ef4444';
        if (pct > 75) return '#dc2626';
        if (pct > 60) return '#b45309';
        if (pct > 45) return '#eab308';
        return '#4a7c2e';
    }

    function setBar(id, pct, color) {
        const bar = document.getElementById(id);
        if (bar) {
            bar.style.width = Math.min(pct, 100) + '%';
            bar.style.backgroundColor = color;
        }
    }

    // ── Public API ──
    return { onEnter, onLeave };
})();
