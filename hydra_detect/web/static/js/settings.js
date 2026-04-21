'use strict';

const HydraSettings = (() => {
    let configData = null;
    let schemaData = null;
    let currentSection = 'camera';
    let hasUnsavedChanges = false;
    let initialized = false;
    let logAutoRefreshTimer = null;

    // Fields that require restart
    const RESTART_FIELDS = {
        web: ['host', 'port'],
        mavlink: ['connection_string', 'baud', 'source_system'],
        camera: ['source', 'width', 'height'],
        detector: ['yolo_model'],
    };

    // Fields that should use textarea
    const TEXTAREA_FIELDS = ['geofence_polygon', 'alert_classes', 'allowed_classes', 'unicast_targets'];

    // Boolean fields
    const BOOLEAN_FIELDS = [
        'enabled', 'alert_statustext', 'auto_loiter_on_detect',
        'guided_roi_on_detect', 'geo_tracking', 'save_images', 'save_crops',
        'app_log_file', 'gps_required', 'kismet_auto_spawn',
    ];

    // Password fields
    const PASSWORD_FIELDS = ['api_token', 'kismet_pass'];

    // Dropdown fields — populated from API endpoints
    const DROPDOWN_FIELDS = {
        'yolo_model': { url: '/api/models', labelKey: 'name', valueKey: 'name', detailKey: 'size_mb', detailSuffix: ' MB' },
        'source': { url: '/api/camera/sources', labelKey: 'label', valueKey: 'index' },
    };
    // Cache for dropdown options (fetched once per session)
    const dropdownCache = {};

    // Static dropdown options (known fixed choices)
    const STATIC_DROPDOWNS = {
        'mode': {
            'osd': [
                { value: 'statustext', label: 'Basic (MAVLink status text)' },
                { value: 'named_value', label: 'Enhanced (requires Lua telemetry script)' },
                { value: 'msp_displayport', label: 'MSP DisplayPort (HDZero VTX direct)' },
            ],
            'rf_homing': [
                { value: 'wifi', label: 'WiFi (by BSSID)' },
                { value: 'sdr', label: 'SDR (by Frequency)' },
            ],
        },
        'search_pattern': {
            'rf_homing': [
                { value: 'lawnmower', label: 'Lawnmower (Grid)' },
                { value: 'spiral', label: 'Spiral (Expanding)' },
            ],
        },
        'log_format': {
            'logging': [
                { value: 'jsonl', label: 'JSONL' },
                { value: 'csv', label: 'CSV' },
            ],
        },
        'severity': {
            'mavlink': [
                { value: '0', label: '0 — Emergency' },
                { value: '1', label: '1 — Alert' },
                { value: '2', label: '2 — Critical' },
                { value: '3', label: '3 — Error' },
                { value: '4', label: '4 — Warning' },
                { value: '5', label: '5 — Notice' },
                { value: '6', label: '6 — Info' },
                { value: '7', label: '7 — Debug' },
            ],
        },
        'min_gps_fix': {
            'mavlink': [
                { value: '0', label: '0 — No fix' },
                { value: '1', label: '1 — No fix' },
                { value: '2', label: '2 — 2D fix' },
                { value: '3', label: '3 — 3D fix' },
                { value: '4', label: '4 — DGPS' },
                { value: '5', label: '5 — RTK Float' },
                { value: '6', label: '6 — RTK Fixed' },
            ],
        },
        'allowed_vehicle_modes': {
            'autonomous': [
                { value: 'AUTO', label: 'AUTO' },
                { value: 'GUIDED', label: 'GUIDED' },
                { value: 'AUTO,GUIDED', label: 'AUTO + GUIDED' },
            ],
        },
        'video_standard': {
            'camera': [
                { value: 'ntsc', label: 'NTSC (30 fps)' },
                { value: 'pal', label: 'PAL (25 fps)' },
            ],
        },
        'source_type': {
            'camera': [
                { value: 'auto', label: 'Auto Detect' },
                { value: 'usb', label: 'USB Camera' },
                { value: 'rtsp', label: 'RTSP Stream' },
                { value: 'gstreamer', label: 'GStreamer Pipeline' },
                { value: 'file', label: 'Video File' },
            ],
        },
        'app_log_level': {
            'logging': [
                { value: 'DEBUG', label: 'DEBUG' },
                { value: 'INFO', label: 'INFO' },
                { value: 'WARNING', label: 'WARNING' },
                { value: 'ERROR', label: 'ERROR' },
            ],
        },
    };

    // Theme is locked to Lattice. The picker UI and config plumbing
    // were removed — keep a no-op applyTheme export so downstream
    // callers don't blow up.
    const THEME_CHOICES = ['lattice'];

    function applyTheme(_theme) {
        // Always force lattice; ignore incoming value.
        document.documentElement.setAttribute('data-theme', 'lattice');
    }

    function initThemePicker() {
        // No-op: picker removed from settings.html.
    }

    function onEnter() {
        loadConfig();
        if (!initialized) {
            initNavHandlers();
            initActionHandlers();
            initLogViewer();
            initThemePicker();
            initialized = true;
        }
        // Resume auto-refresh if returning to logs section
        if (currentSection === 'system_logs') {
            startLogAutoRefresh();
        }
    }

    function onLeave() {
        if (hasUnsavedChanges) {
            // Could warn, but for now just reset
            hasUnsavedChanges = false;
        }
        stopLogAutoRefresh();
    }

    async function loadConfig() {
        const results = await Promise.all([
            HydraApp.apiGet('/api/config/full'),
            schemaData ? Promise.resolve(schemaData) : HydraApp.apiGet('/api/config/schema'),
        ]);
        configData = results[0];
        if (results[1]) schemaData = results[1];
        if (!configData) {
            showError('Failed to load configuration');
            return;
        }
        // Theme locked to lattice; ignore configData.web.theme.
        applyTheme('lattice');
        // Defer render to next frame so the view's display:flex has applied
        // (prevents empty Camera tab on initial load when CSS transition
        // from display:none hasn't completed yet).
        requestAnimationFrame(() => renderSection(currentSection));
    }

    function initNavHandlers() {
        document.querySelectorAll('.settings-section-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const section = btn.dataset.section;
                if (section === currentSection) return;
                currentSection = section;

                document.querySelectorAll('.settings-section-btn').forEach(b =>
                    b.classList.toggle('active', b.dataset.section === section));

                renderSection(section);
            });
        });
    }

    function initActionHandlers() {
        const applyBtn = document.getElementById('settings-apply');
        const resetBtn = document.getElementById('settings-reset');
        const restoreBtn = document.getElementById('settings-restore');
        const restartBtn = document.getElementById('settings-restart-btn');
        const factoryBtn = document.getElementById('settings-factory-reset');
        const exportBtn = document.getElementById('settings-export');
        const importBtn = document.getElementById('settings-import');
        const importFile = document.getElementById('settings-import-file');
        const sessionExportBtn = document.getElementById('settings-session-export');

        if (applyBtn) applyBtn.addEventListener('click', handleApply);
        if (resetBtn) resetBtn.addEventListener('click', handleReset);
        if (restoreBtn) restoreBtn.addEventListener('click', handleRestore);
        if (restartBtn) restartBtn.addEventListener('click', handleRestart);
        if (factoryBtn) factoryBtn.addEventListener('click', handleFactoryReset);
        if (exportBtn) exportBtn.addEventListener('click', handleExport);
        if (sessionExportBtn) sessionExportBtn.addEventListener('click', function() {
            window.location.href = '/api/export';
        });
        if (importBtn) importBtn.addEventListener('click', function() {
            if (importFile) importFile.click();
        });
        if (importFile) importFile.addEventListener('change', handleImportFile);

        const logoutBtn = document.getElementById('settings-logout');
        if (logoutBtn) logoutBtn.addEventListener('click', handleLogout);
        initSessionBlock();
    }

    async function initSessionBlock() {
        const block = document.getElementById('settings-session-block');
        if (!block) return;
        try {
            const resp = await fetch('/auth/status', { credentials: 'same-origin' });
            if (!resp.ok) return;
            const data = await resp.json();
            if (data && data.password_enabled) {
                block.style.display = '';
            }
        } catch (err) {
            // Silent — auth status is optional UX gate
        }
    }

    async function handleLogout() {
        try {
            const resp = await fetch('/auth/logout', {
                method: 'POST',
                credentials: 'same-origin',
            });
            if (resp.ok) {
                window.location.href = '/';
                return;
            }
            HydraApp.showToast('Logout failed (' + resp.status + ')', 'error');
        } catch (err) {
            HydraApp.showToast('Logout failed — network error', 'error');
        }
    }

    async function handleRestart() {
        const warning = document.getElementById('settings-restart-warning');
        if (warning && warning.style.display === 'none') {
            warning.style.display = '';
            return;
        }
        const btn = document.getElementById('settings-restart-btn');
        if (btn) btn.disabled = true;
        const result = await HydraApp.apiPost('/api/restart', {});
        if (result && result.status === 'restarting') {
            HydraApp.showToast('Pipeline restarting...', 'info');
        }
        if (warning) warning.style.display = 'none';
        if (btn) setTimeout(() => { btn.disabled = false; }, 5000);
    }

    function clearElement(el) {
        while (el.firstChild) {
            el.removeChild(el.firstChild);
        }
    }

    function buildSlider(key, section, value, spec) {
        var container = document.createElement('div');
        container.className = 'slider-container';
        container.dataset.key = key;
        container.dataset.section = section;

        var slider = document.createElement('input');
        slider.type = 'range';
        slider.className = 'schema-slider';
        slider.min = String(spec.min);
        slider.max = String(spec.max);
        var range = spec.max - spec.min;
        slider.step = spec.type === 'float' ? (range > 100 ? '1' : range > 10 ? '0.1' : '0.01') : '1';
        slider.value = value;

        var valLabel = document.createElement('span');
        valLabel.className = 'slider-value';
        valLabel.textContent = value;

        // Show default hint
        var defaultHint = document.createElement('span');
        defaultHint.className = 'slider-default';
        if (spec.default != null) {
            defaultHint.textContent = 'default: ' + spec.default;
        }

        slider.addEventListener('input', function() {
            valLabel.textContent = slider.value;
            hasUnsavedChanges = true;
        });

        container.appendChild(slider);
        container.appendChild(valLabel);
        container.appendChild(defaultHint);
        return container;
    }

    function renderSection(section) {
        const form = document.getElementById('settings-form');
        const warning = document.getElementById('settings-warning');
        const error = document.getElementById('settings-error');
        const restart = document.getElementById('settings-restart');
        const logPanel = document.getElementById('log-viewer-panel');
        const actionsBar = document.querySelector('.settings-actions');
        const recoverySection = document.querySelector('.settings-recovery');

        if (!form) return;

        // Toggle log viewer vs config form
        if (section === 'system_logs') {
            form.style.display = 'none';
            if (logPanel) logPanel.style.display = '';
            if (actionsBar) actionsBar.style.display = 'none';
            if (recoverySection) recoverySection.style.display = 'none';
            if (warning) warning.style.display = 'none';
            if (error) error.style.display = 'none';
            if (restart) restart.style.display = 'none';
            fetchAndRenderLogs();
            startLogAutoRefresh();
            return;
        }

        // Normal config section — hide log viewer, show form + actions
        form.style.display = '';
        if (logPanel) logPanel.style.display = 'none';
        if (actionsBar) actionsBar.style.display = '';
        if (recoverySection) recoverySection.style.display = '';
        stopLogAutoRefresh();

        if (!configData) return;
        clearElement(form);
        if (error) error.style.display = 'none';
        if (restart) restart.style.display = 'none';

        // Show warning for autonomous section
        if (warning) {
            warning.style.display = section === 'autonomous' ? '' : 'none';
        }

        // Geofence not configured warning
        if (section === 'autonomous' && configData.autonomous) {
            const lat = parseFloat(configData.autonomous.geofence_lat || 0);
            const lon = parseFloat(configData.autonomous.geofence_lon || 0);
            if (lat === 0 && lon === 0) {
                const geoWarn = document.createElement('div');
                geoWarn.className = 'settings-warning';
                geoWarn.style.display = '';
                geoWarn.textContent = 'Geofence not configured \u2014 autonomous strike is DISABLED until valid coordinates are set.';
                form.appendChild(geoWarn);
            }
        }

        // Show power user link only in logging section
        const powerFooter = document.getElementById('settings-power-footer');
        if (powerFooter) {
            powerFooter.style.display = section === 'logging' ? '' : 'none';
        }

        const sectionData = configData[section];
        if (!sectionData) {
            form.textContent = 'Section not found in configuration.';
            return;
        }

        Object.entries(sectionData).forEach(([key, value]) => {
            const field = document.createElement('div');
            field.className = 'settings-field';

            const label = document.createElement('label');
            label.className = 'settings-field-label';
            label.textContent = key;

            // Schema metadata for this field (may be null)
            const spec = schemaData && schemaData[section] && schemaData[section][key];

            // Description tooltip from schema
            if (spec && spec.description) {
                label.title = spec.description;
            }

            // Restart icon
            if (RESTART_FIELDS[section] && RESTART_FIELDS[section].includes(key)) {
                const icon = document.createElement('span');
                icon.className = 'restart-icon';
                icon.textContent = ' \u27F3';
                icon.title = 'Requires restart';
                label.appendChild(icon);
            }

            let input;

            // Check for static dropdown (fixed options based on section + key)
            const staticOpts = STATIC_DROPDOWNS[key] && STATIC_DROPDOWNS[key][section];
            if (staticOpts) {
                input = document.createElement('select');
                input.dataset.key = key;
                input.dataset.section = section;
                input.addEventListener('change', () => { hasUnsavedChanges = true; });
                staticOpts.forEach(opt => {
                    const o = document.createElement('option');
                    o.value = opt.value;
                    o.textContent = opt.label;
                    if (opt.value === value) o.selected = true;
                    input.appendChild(o);
                });
            } else if (DROPDOWN_FIELDS[key]) {
                // Dropdown populated from API
                input = document.createElement('select');
                input.dataset.key = key;
                input.dataset.section = section;
                input.addEventListener('change', () => { hasUnsavedChanges = true; });
                const dfCfg = DROPDOWN_FIELDS[key];
                // Add current value as default while loading
                const defaultOpt = document.createElement('option');
                defaultOpt.value = value;
                defaultOpt.textContent = value + ' (loading...)';
                defaultOpt.selected = true;
                input.appendChild(defaultOpt);
                // Fetch options asynchronously
                (async () => {
                    if (!dropdownCache[key]) {
                        dropdownCache[key] = await HydraApp.apiGet(dfCfg.url);
                    }
                    const options = dropdownCache[key];
                    if (!options || !Array.isArray(options)) return;
                    while (input.firstChild) input.removeChild(input.firstChild);
                    options.forEach(opt => {
                        const o = document.createElement('option');
                        o.value = opt[dfCfg.valueKey];
                        let label = opt[dfCfg.labelKey];
                        if (dfCfg.detailKey && opt[dfCfg.detailKey] != null) {
                            label += ' (' + opt[dfCfg.detailKey] + (dfCfg.detailSuffix || '') + ')';
                        }
                        o.textContent = label;
                        if (opt[dfCfg.valueKey] === value) o.selected = true;
                        input.appendChild(o);
                    });
                })();
            } else if (spec && spec.type === 'enum' && spec.choices) {
                // Schema-driven dropdown for enum fields
                input = document.createElement('select');
                input.dataset.key = key;
                input.dataset.section = section;
                input.addEventListener('change', () => { hasUnsavedChanges = true; });
                spec.choices.forEach(choice => {
                    const o = document.createElement('option');
                    o.value = choice;
                    o.textContent = choice;
                    if (choice === value) o.selected = true;
                    input.appendChild(o);
                });
            } else if ((spec && spec.type === 'bool') || BOOLEAN_FIELDS.includes(key)) {
                // Toggle switch
                input = document.createElement('div');
                input.className = 'toggle-switch' + (value === 'true' ? ' active' : '');
                input.dataset.key = key;
                input.dataset.section = section;
                input.addEventListener('click', () => {
                    input.classList.toggle('active');
                    hasUnsavedChanges = true;
                });
            } else if (PASSWORD_FIELDS.includes(key)) {
                // Password with masked display
                input = document.createElement('input');
                input.type = 'password';
                input.value = value;
                input.dataset.key = key;
                input.dataset.section = section;
                input.addEventListener('input', () => { hasUnsavedChanges = true; });
            } else if (TEXTAREA_FIELDS.includes(key)) {
                input = document.createElement('textarea');
                input.value = value;
                input.dataset.key = key;
                input.dataset.section = section;
                input.rows = 3;
                input.addEventListener('input', () => { hasUnsavedChanges = true; });
            } else if (spec && (spec.type === 'float' || spec.type === 'int') && spec.min != null && spec.max != null) {
                // Schema-driven range slider for numeric fields with known bounds
                input = buildSlider(key, section, value, spec);
            } else {
                input = document.createElement('input');
                // Detect numeric fields
                const numVal = parseFloat(value);
                if (!isNaN(numVal) && value.trim() === String(numVal)) {
                    input.type = 'number';
                    input.step = value.includes('.') ? '0.01' : '1';
                } else {
                    input.type = 'text';
                }
                input.value = value;
                input.dataset.key = key;
                input.dataset.section = section;
                input.addEventListener('input', () => { hasUnsavedChanges = true; });
            }

            field.appendChild(label);
            field.appendChild(input);
            form.appendChild(field);
        });
    }

    async function handleApply() {
        if (!configData) return;

        const updates = {};
        const form = document.getElementById('settings-form');
        if (!form) return;

        // Collect values from all visible inputs
        form.querySelectorAll('[data-key]').forEach(el => {
            const section = el.dataset.section;
            const key = el.dataset.key;
            let value;

            if (el.classList.contains('toggle-switch')) {
                value = el.classList.contains('active') ? 'true' : 'false';
            } else if (el.classList.contains('slider-container')) {
                var rangeInput = el.querySelector('input[type="range"]');
                value = rangeInput ? rangeInput.value : '';
            } else {
                value = el.value;
            }

            // Only include changed values
            if (configData[section] && configData[section][key] !== value) {
                if (!updates[section]) updates[section] = {};
                updates[section][key] = value;
            }
        });

        if (Object.keys(updates).length === 0) {
            HydraApp.showToast('No changes to save', 'info');
            return;
        }

        const result = await HydraApp.apiPost('/api/config/full', updates);
        if (result) {
            hasUnsavedChanges = false;
            HydraApp.showToast('Configuration saved', 'success');

            // Show restart notice if needed
            if (result.restart_required && result.restart_required.length > 0) {
                const restart = document.getElementById('settings-restart');
                const fields = document.getElementById('restart-fields');
                if (restart && fields) {
                    fields.textContent = result.restart_required.join(', ');
                    restart.style.display = '';
                }
            }

            // Reload config to get fresh values
            await loadConfig();
        }
    }

    async function handleReset() {
        hasUnsavedChanges = false;
        await loadConfig();
        HydraApp.showToast('Form reset to saved values', 'info');
    }

    async function handleRestore() {
        if (!confirm('Restore config.ini from backup? This will overwrite current settings.')) return;
        const result = await HydraApp.apiPost('/api/config/restore-backup', {});
        if (result) {
            hasUnsavedChanges = false;
            await loadConfig();
            HydraApp.showToast('Configuration restored from backup', 'success');
        }
    }

    async function handleFactoryReset() {
        if (!confirm('FACTORY RESET: This will restore all settings to factory defaults and restart the pipeline. Continue?')) return;
        if (!confirm('Are you sure? All custom configuration will be lost.')) return;
        const result = await HydraApp.apiPost('/api/config/factory-reset', {});
        if (result) {
            hasUnsavedChanges = false;
            await loadConfig();
            HydraApp.showToast('Factory defaults restored — restarting pipeline', 'success');
        }
    }

    async function handleExport() {
        const config = await HydraApp.apiGet('/api/config/export');
        if (!config) return;
        const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'hydra-config-' + new Date().toISOString().slice(0, 10) + '.json';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        HydraApp.showToast('Config exported', 'success');
    }

    async function handleImportFile(event) {
        const file = event.target.files && event.target.files[0];
        if (!file) return;
        if (!confirm('Import configuration from ' + file.name + '? This will overwrite current settings.')) {
            event.target.value = '';
            return;
        }
        try {
            const text = await file.text();
            const data = JSON.parse(text);
            const result = await HydraApp.apiPost('/api/config/import', data);
            if (result) {
                hasUnsavedChanges = false;
                await loadConfig();
                HydraApp.showToast('Configuration imported', 'success');
                if (result.restart_required && result.restart_required.length > 0) {
                    const restart = document.getElementById('settings-restart');
                    const fields = document.getElementById('restart-fields');
                    if (restart && fields) {
                        fields.textContent = result.restart_required.join(', ');
                        restart.style.display = '';
                    }
                }
            }
        } catch (err) {
            HydraApp.showToast('Invalid config file: ' + err.message, 'error');
        }
        event.target.value = '';
    }

    // Power User easter egg — uses event delegation for reliable click handling
    document.addEventListener('click', e => {
        if (e.target.id === 'settings-power-user') {
            const modal = document.getElementById('power-user-modal');
            if (modal) HydraApp.openModal(modal, e.target);
        }
        if (e.target.id === 'power-user-cancel') {
            const modal = document.getElementById('power-user-modal');
            if (modal) HydraApp.closeModal(modal);
        }
        if (e.target.id === 'power-user-enable') {
            // Rickroll — commitment to the bit
            const url = 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?autoplay=1';
            // Try opening in new tab first (works even without internet on cached browsers)
            const win = window.open(url, '_blank');
            if (!win) {
                // Popup blocked — fall back to replacing page with iframe
                const container = document.createElement('div');
                container.style.cssText = 'position:fixed;inset:0;background:#000;z-index:99999;';
                const iframe = document.createElement('iframe');
                iframe.src = url;
                iframe.allow = 'autoplay; encrypted-media';
                iframe.allowFullscreen = true;
                iframe.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;border:none;';
                container.appendChild(iframe);
                document.body.appendChild(container);
            }
            // Close the modal
            const modal = document.getElementById('power-user-modal');
            if (modal) HydraApp.closeModal(modal);
        }
    });

    function showError(msg) {
        const el = document.getElementById('settings-error');
        if (el) {
            el.textContent = msg;
            el.style.display = '';
        }
    }

    // ── Log Viewer ──

    function initLogViewer() {
        const refreshBtn = document.getElementById('log-refresh-btn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', fetchAndRenderLogs);
        }
        const levelFilter = document.getElementById('log-level-filter');
        if (levelFilter) {
            levelFilter.addEventListener('change', fetchAndRenderLogs);
        }
        const lineCount = document.getElementById('log-line-count');
        if (lineCount) {
            lineCount.addEventListener('change', fetchAndRenderLogs);
        }
    }

    async function fetchAndRenderLogs() {
        const levelEl = document.getElementById('log-level-filter');
        const linesEl = document.getElementById('log-line-count');
        const output = document.getElementById('log-viewer-output');
        if (!output) return;

        const level = levelEl ? levelEl.value : 'INFO';
        const lines = linesEl ? linesEl.value : '50';
        const queryLevel = level === 'ALL' ? 'DEBUG' : level;

        const entries = await HydraApp.apiGet(
            '/api/logs?lines=' + lines + '&level=' + queryLevel
        );

        clearElement(output);

        if (!entries || !Array.isArray(entries) || entries.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'log-entry log-empty';
            empty.textContent = 'No log entries found.';
            output.appendChild(empty);
            return;
        }

        entries.forEach(function(entry) {
            const line = document.createElement('div');
            line.className = 'log-entry';

            // Color-code by level
            const lvl = (entry.level || '').toUpperCase();
            if (lvl === 'ERROR' || lvl === 'CRITICAL') {
                line.classList.add('log-error');
            } else if (lvl === 'WARNING') {
                line.classList.add('log-warning');
            } else if (lvl === 'DEBUG') {
                line.classList.add('log-debug');
            }

            // Build text: [TIMESTAMP] [LEVEL] [MODULE] message
            var parts = [];
            if (entry.timestamp) {
                parts.push('[' + entry.timestamp + ']');
            }
            if (entry.level) {
                parts.push('[' + entry.level + ']');
            }
            if (entry.module) {
                parts.push('[' + entry.module + ']');
            }
            parts.push(entry.message || '');
            line.textContent = parts.join(' ');

            output.appendChild(line);
        });

        // Auto-scroll to bottom
        output.scrollTop = output.scrollHeight;
    }

    function startLogAutoRefresh() {
        stopLogAutoRefresh();
        logAutoRefreshTimer = setInterval(fetchAndRenderLogs, 5000);
        const indicator = document.getElementById('log-auto-indicator');
        if (indicator) indicator.classList.add('active');
    }

    function stopLogAutoRefresh() {
        if (logAutoRefreshTimer) {
            clearInterval(logAutoRefreshTimer);
            logAutoRefreshTimer = null;
        }
        const indicator = document.getElementById('log-auto-indicator');
        if (indicator) indicator.classList.remove('active');
    }

    // Eager theme apply — runs once at module load so the saved theme is in
    // effect across every view, not just when the user opens Settings.
    async function bootstrapTheme() {
        try {
            const cfg = await HydraApp.apiGet('/api/config/full');
            if (cfg && cfg.web && cfg.web.theme) {
                applyTheme(cfg.web.theme);
            }
        } catch (err) {
            // Silent — theme is cosmetic; missing config must not break boot
        }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootstrapTheme);
    } else {
        bootstrapTheme();
    }

    return { onEnter, onLeave, applyTheme, THEME_CHOICES };
})();
