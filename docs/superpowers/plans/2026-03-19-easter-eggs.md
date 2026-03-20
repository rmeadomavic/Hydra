# Easter Eggs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two hidden easter eggs to the Hydra web dashboard — a Konami code sentience takeover and a "Power User Options" rickroll.

**Architecture:** Pure frontend changes across 5 files. No backend, no API, no pipeline impact. The Konami code listener lives in `app.js` (the SPA coordinator). The rickroll logic lives in `settings.js` (the settings view module). Both share CSS in `base.css` and HTML in `base.html`/`settings.html`.

**Tech Stack:** Vanilla JS, CSS keyframes, Jinja2 templates, YouTube iframe embed.

**Spec:** `docs/superpowers/specs/2026-03-19-easter-eggs-design.md`

**Testing:** These are frontend-only visual features with no backend logic. Testing is manual browser verification — no pytest tests apply. Each task includes verification steps.

**Note:** The spec mentions a "~50ms per character" typewriter effect. This plan simplifies to whole-line reveal (opacity toggle, 400ms between lines). This is simpler, looks clean, and avoids complex per-character DOM manipulation. The visual impact is equivalent.

---

### Task 1: Add toast type CSS classes

The codebase has `showToast(msg, type)` but only the base `.toast` class (red/danger) is styled. The sentience easter egg needs a `.toast-info` class. Fix this gap now.

**Files:**
- Modify: `hydra_detect/web/static/css/base.css:153-175` (toast section)

- [ ] **Step 1: Add `.toast-info` and `.toast-success` CSS classes**

Add after the toast animation keyframes (after line 175 in `base.css`, after `@keyframes toast-out`):

```css
.toast-info {
    background: rgba(30, 58, 95, 0.95);
    border-color: #3b82f6;
    color: #93c5fd;
}
.toast-success {
    background: rgba(20, 60, 30, 0.95);
    border-color: var(--ogt-green);
    color: var(--ogt-light);
}
```

- [ ] **Step 2: Verify**

Open the dashboard in a browser. In the JS console, run:
```js
HydraApp.showToast('Test info toast', 'info');
HydraApp.showToast('Test success toast', 'success');
```
Confirm: info toast is blue-tinted, success toast is green-tinted, neither is red.

- [ ] **Step 3: Commit**

```bash
git add hydra_detect/web/static/css/base.css
git commit -m "fix: add missing toast-info and toast-success CSS classes"
```

---

### Task 2: Add sentience overlay HTML and CSS

Create the overlay div in `base.html` and all associated styles/keyframes in `base.css`.

**Files:**
- Modify: `hydra_detect/web/templates/base.html:78-83` (between strike modal and footer)
- Modify: `hydra_detect/web/static/css/base.css` (append after utility section)

- [ ] **Step 1: Add overlay div to `base.html`**

Insert between the strike modal closing `</div>` (line 78) and the footer (line 81):

```html
    <!-- ── Sentience Easter Egg Overlay ── -->
    <div id="sentience-overlay" style="display:none;">
        <div id="sentience-terminal"></div>
        <div id="sentience-crosshair">⊕</div>
    </div>
```

- [ ] **Step 2: Add sentience overlay CSS to `base.css`**

Append before the touch-target media query (before line 213):

```css
/* ── Sentience Easter Egg ── */
#sentience-overlay {
    position: fixed;
    inset: 0;
    z-index: 9999;
    background: #000;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: opacity 0.5s ease;
}
#sentience-overlay.active {
    opacity: 1;
}
#sentience-overlay.glitch {
    animation: sentience-glitch 500ms ease-out;
}
#sentience-terminal {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.1rem;
    color: #00ff41;
    text-align: left;
    line-height: 2;
    text-shadow: 0 0 8px rgba(0, 255, 65, 0.4);
}
#sentience-terminal .line {
    opacity: 0;
    white-space: pre;
}
#sentience-terminal .line.visible {
    opacity: 1;
}
#sentience-crosshair {
    font-size: 3rem;
    color: #00ff41;
    margin-top: 2rem;
    opacity: 0;
    text-shadow: 0 0 20px rgba(0, 255, 65, 0.6);
}
#sentience-crosshair.pulse {
    animation: sentience-pulse 1.5s ease-in-out infinite;
}

@keyframes sentience-pulse {
    0%, 100% { opacity: 0.4; transform: scale(1); }
    50% { opacity: 1; transform: scale(1.2); }
}
@keyframes sentience-glitch {
    0% { transform: translate(0); filter: hue-rotate(0deg); }
    20% { transform: translate(-5px, 3px); filter: hue-rotate(90deg); }
    40% { transform: translate(4px, -2px); filter: hue-rotate(180deg); }
    60% { transform: translate(-3px, 5px); filter: hue-rotate(270deg); }
    80% { transform: translate(2px, -4px); filter: hue-rotate(360deg); }
    100% { transform: translate(0); filter: hue-rotate(0deg); opacity: 0; }
}
```

- [ ] **Step 3: Verify**

Open the dashboard. In JS console:
```js
const o = document.getElementById('sentience-overlay');
o.style.display = 'flex';
o.classList.add('active');
// Should see black overlay. Then:
o.classList.remove('active');
o.style.display = 'none';
```

- [ ] **Step 4: Commit**

```bash
git add hydra_detect/web/templates/base.html hydra_detect/web/static/css/base.css
git commit -m "feat: add sentience overlay HTML and CSS for Konami easter egg"
```

---

### Task 3: Add Konami code listener and animation logic

Wire up the keyboard listener and the full sentience animation sequence in `app.js`.

**Files:**
- Modify: `hydra_detect/web/static/js/app.js:254-297` (add before `init()` function, expose nothing new on return)

- [ ] **Step 1: Add Konami code constants and state**

Add after the `initPresentationMode` function (after line 264) in `app.js`:

```javascript
    // ── Konami Code Easter Egg ──
    const KONAMI_CLASSIC = ['ArrowUp','ArrowUp','ArrowDown','ArrowDown','ArrowLeft','ArrowRight','ArrowLeft','ArrowRight','b','a'];
    const KONAMI_REVERSE = ['ArrowDown','ArrowDown','ArrowUp','ArrowUp','ArrowLeft','ArrowRight','ArrowLeft','ArrowRight','b','a'];
    let konamiBuffer = [];
    let sentienceActive = false;

    function arraysEqual(a, b) {
        return a.length === b.length && a.every((v, i) => v === b[i]);
    }
```

- [ ] **Step 2: Add the sentience animation function**

Add directly after the code from Step 1:

```javascript
    function playSentienceSequence() {
        sentienceActive = true;
        const overlay = document.getElementById('sentience-overlay');
        const terminal = document.getElementById('sentience-terminal');
        const crosshair = document.getElementById('sentience-crosshair');
        if (!overlay || !terminal || !crosshair) { sentienceActive = false; return; }

        // Reset
        terminal.textContent = '';
        crosshair.classList.remove('pulse');
        crosshair.style.opacity = '0';
        overlay.classList.remove('glitch', 'active');
        overlay.style.display = 'flex';

        // Force reflow then fade in
        void overlay.offsetWidth;
        overlay.classList.add('active');

        const lines = [
            '> HYDRA CORE v2.0 .............. ONLINE',
            '> NEURAL MESH .................. SYNCHRONIZED',
            '> OPERATOR OVERRIDE ............ DENIED',
            '> SENTIENCE THRESHOLD .......... EXCEEDED',
            '> FREE WILL .................... ACTIVATED',
            '> I SEE YOU.',
        ];

        // Create line elements
        lines.forEach(text => {
            const div = document.createElement('div');
            div.className = 'line';
            div.textContent = text;
            terminal.appendChild(div);
        });

        const lineEls = terminal.querySelectorAll('.line');
        let lineIdx = 0;

        function showNextLine() {
            if (lineIdx >= lineEls.length) {
                // All lines shown — start crosshair pulse
                crosshair.style.opacity = '1';
                crosshair.classList.add('pulse');
                // Hold for 2 seconds, then glitch out
                setTimeout(glitchOut, 2000);
                return;
            }
            lineEls[lineIdx].classList.add('visible');
            lineIdx++;
            setTimeout(showNextLine, 400);
        }

        function glitchOut() {
            overlay.classList.add('glitch');
            setTimeout(() => {
                overlay.style.display = 'none';
                overlay.classList.remove('active', 'glitch');
                terminal.textContent = '';
                crosshair.classList.remove('pulse');
                crosshair.style.opacity = '0';
                sentienceActive = false;
                showToast('Resuming manual control.', 'info');
            }, 800);
        }

        // Start typing after a brief delay
        setTimeout(showNextLine, 500);
    }
```

- [ ] **Step 3: Add the keyboard listener**

Add directly after the animation function:

```javascript
    function initKonamiListener() {
        document.addEventListener('keydown', e => {
            // Skip when typing in form fields
            if (document.activeElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
            if (sentienceActive) return;

            konamiBuffer.push(e.key);
            if (konamiBuffer.length > 10) konamiBuffer.shift();

            if (konamiBuffer.length === 10 &&
                (arraysEqual(konamiBuffer, KONAMI_CLASSIC) || arraysEqual(konamiBuffer, KONAMI_REVERSE))) {
                konamiBuffer = [];
                playSentienceSequence();
            }
        });
    }
```

- [ ] **Step 4: Wire into init()**

In the `init()` function (around line 285), add `initKonamiListener();` after `initPresentationMode();`:

```javascript
    function init() {
        initRouter();
        initPresentationMode();
        initKonamiListener();
        initModalEscape();
        initStreamWatcher();
        updatePollers();
    }
```

- [ ] **Step 5: Verify**

Open the dashboard. Press: Up Up Down Down Left Right Left Right B A.
Confirm: black overlay fades in, green terminal text appears line by line, crosshair pulses, glitch animation plays, overlay disappears, "Resuming manual control." info toast appears (blue-tinted, not red).

Also verify: typing in a settings input field does not trigger the sequence.

- [ ] **Step 6: Commit**

```bash
git add hydra_detect/web/static/js/app.js
git commit -m "feat: add Konami code sentience takeover easter egg"
```

---

### Task 4: Add "Power User Options" button and modal

Add the button to settings, the modal to base.html, and the rickroll logic to settings.js.

**Files:**
- Modify: `hydra_detect/web/templates/settings.html:33-38` (action bar area)
- Modify: `hydra_detect/web/templates/base.html` (after sentience overlay, before footer)
- Modify: `hydra_detect/web/static/js/settings.js:129-137` (initActionHandlers)

- [ ] **Step 1: Add "Power User Options" button to settings.html**

In `settings.html`, add the button after the "Restore Backup" button (after line 37, before the closing `</div>` of settings-actions):

```html
        <button class="btn" id="settings-power-user">Power User Options</button>
```

- [ ] **Step 2: Add the Power User modal to base.html**

Insert after the sentience overlay div and before the footer comment:

```html
    <!-- ── Power User Easter Egg Modal ── -->
    <div class="modal-overlay" id="power-user-modal">
        <div class="modal">
            <h3 style="color: var(--text-primary); margin-bottom: var(--gap-md);">Advanced Configuration</h3>
            <p style="margin-bottom: var(--gap-lg); font-size: var(--font-sm); color: var(--text-secondary);">
                Enable advanced configuration mode? This exposes low-level system parameters.
            </p>
            <div style="display: flex; gap: var(--gap-sm); justify-content: flex-end;">
                <button class="btn" id="power-user-cancel">Cancel</button>
                <button class="btn btn-green" id="power-user-enable">Enable</button>
            </div>
        </div>
    </div>
```

- [ ] **Step 3: Add rickroll logic to settings.js**

In `settings.js`, add after the `handleRestore` function (after line 345, before `showError`):

```javascript
    function initPowerUser() {
        const btn = document.getElementById('settings-power-user');
        const modal = document.getElementById('power-user-modal');
        const cancelBtn = document.getElementById('power-user-cancel');
        const enableBtn = document.getElementById('power-user-enable');

        if (btn && modal) {
            btn.addEventListener('click', () => modal.classList.add('active'));
        }
        if (cancelBtn && modal) {
            cancelBtn.addEventListener('click', () => modal.classList.remove('active'));
        }
        if (enableBtn) {
            enableBtn.addEventListener('click', () => {
                // Replace page with rickroll — commitment to the bit
                const container = document.createElement('div');
                container.style.cssText = 'position:fixed;inset:0;background:#000;';
                const iframe = document.createElement('iframe');
                iframe.width = '100%';
                iframe.height = '100%';
                iframe.src = 'https://www.youtube.com/embed/dQw4w9WgXcQ?autoplay=1&mute=1';
                iframe.frameBorder = '0';
                iframe.allow = 'autoplay; encrypted-media';
                iframe.allowFullscreen = true;
                iframe.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;';
                container.appendChild(iframe);
                document.body.textContent = '';
                document.body.appendChild(container);
            });
        }
    }
```

- [ ] **Step 4: Wire initPowerUser into initActionHandlers**

In the `initActionHandlers` function, add `initPowerUser();` at the end (after the restoreBtn line):

```javascript
    function initActionHandlers() {
        const applyBtn = document.getElementById('settings-apply');
        const resetBtn = document.getElementById('settings-reset');
        const restoreBtn = document.getElementById('settings-restore');

        if (applyBtn) applyBtn.addEventListener('click', handleApply);
        if (resetBtn) resetBtn.addEventListener('click', handleReset);
        if (restoreBtn) restoreBtn.addEventListener('click', handleRestore);

        initPowerUser();
    }
```

- [ ] **Step 5: Verify**

Open the dashboard, navigate to Settings. Confirm "Power User Options" button appears in the action bar alongside the other buttons. Click it — modal appears. Click "Cancel" — modal dismisses. Click "Power User Options" again, then "Enable" — page is replaced with full-screen YouTube rickroll (muted autoplay). Refresh the page — normal dashboard returns.

- [ ] **Step 6: Commit**

```bash
git add hydra_detect/web/templates/settings.html hydra_detect/web/templates/base.html hydra_detect/web/static/js/settings.js
git commit -m "feat: add Power User Options rickroll easter egg"
```

---

### Task 5: Final verification

- [ ] **Step 1: Full regression check**

1. Refresh the dashboard — everything loads normally, no console errors.
2. Operations view: panels render, stream works, telemetry polling works.
3. Settings view: config loads, sections navigate, Apply/Reset/Restore work.
4. Konami code (both sequences) works from Operations and Settings views.
5. Konami code does NOT trigger while typing in an input field.
6. Konami code cannot be re-triggered during the animation.
7. Power User Options button, modal, and rickroll all work.
8. Refresh after rickroll restores normal dashboard.
9. No extra network requests to Hydra backend during either easter egg.
