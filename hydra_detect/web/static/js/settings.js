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
    const TEXTAREA_FIELDS = ['geofence_polygon', 'alert_classes', 'allowed_classes'];

    // Boolean fields
    const BOOLEAN_FIELDS = [
        'enabled', 'alert_statustext', 'auto_loiter_on_detect',
        'guided_roi_on_detect', 'geo_tracking', 'save_images', 'save_crops',
    ];

    // Password fields
    const PASSWORD_FIELDS = ['api_token', 'kismet_pass'];

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

            if (BOOLEAN_FIELDS.includes(key)) {
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

    function showError(msg) {
        const el = document.getElementById('settings-error');
        if (el) {
            el.textContent = msg;
            el.style.display = '';
        }
    }

    return { onEnter, onLeave };
})();
