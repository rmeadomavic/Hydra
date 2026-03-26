'use strict';

const HydraSettings = (() => {
    let configData = null;
    let currentSection = 'camera';
    let hasUnsavedChanges = false;

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
                { value: 'statustext', label: 'statustext — simple, no Lua' },
                { value: 'named_value', label: 'named_value — richer, needs Lua script' },
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

    function onEnter() {
        loadConfig();
        initNavHandlers();
        initActionHandlers();
    }

    function onLeave() {
        if (hasUnsavedChanges) {
            // Could warn, but for now just reset
            hasUnsavedChanges = false;
        }
    }

    async function loadConfig() {
        configData = await HydraApp.apiGet('/api/config/full');
        if (!configData) {
            showError('Failed to load configuration');
            return;
        }
        renderSection(currentSection);
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

        if (applyBtn) applyBtn.addEventListener('click', handleApply);
        if (resetBtn) resetBtn.addEventListener('click', handleReset);
        if (restoreBtn) restoreBtn.addEventListener('click', handleRestore);
    }

    function clearElement(el) {
        while (el.firstChild) {
            el.removeChild(el.firstChild);
        }
    }

    function renderSection(section) {
        const form = document.getElementById('settings-form');
        const warning = document.getElementById('settings-warning');
        const error = document.getElementById('settings-error');
        const restart = document.getElementById('settings-restart');

        if (!form || !configData) return;
        clearElement(form);
        if (error) error.style.display = 'none';
        if (restart) restart.style.display = 'none';

        // Show warning for autonomous section
        if (warning) {
            warning.style.display = section === 'autonomous' ? '' : 'none';
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
            } else if (BOOLEAN_FIELDS.includes(key)) {
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

    // Power User easter egg — uses event delegation for reliable click handling
    document.addEventListener('click', e => {
        if (e.target.id === 'settings-power-user') {
            const modal = document.getElementById('power-user-modal');
            if (modal) modal.classList.add('active');
        }
        if (e.target.id === 'power-user-cancel') {
            const modal = document.getElementById('power-user-modal');
            if (modal) modal.classList.remove('active');
        }
        if (e.target.id === 'power-user-enable') {
            // Replace page with rickroll — commitment to the bit
            const container = document.createElement('div');
            container.style.cssText = 'position:fixed;inset:0;background:#000;';
            const iframe = document.createElement('iframe');
            iframe.width = '100%';
            iframe.height = '100%';
            iframe.src = 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?autoplay=1&mute=1';
            iframe.frameBorder = '0';
            iframe.allow = 'autoplay; encrypted-media';
            iframe.allowFullscreen = true;
            iframe.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;';
            container.appendChild(iframe);
            document.body.textContent = '';
            document.body.appendChild(container);
        }
    });

    function showError(msg) {
        const el = document.getElementById('settings-error');
        if (el) {
            el.textContent = msg;
            el.style.display = '';
        }
    }

    return { onEnter, onLeave };
})();
