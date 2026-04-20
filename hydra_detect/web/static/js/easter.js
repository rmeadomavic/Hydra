/**
 * Hydra Detect v2.0 — Konami "Sentience" easter egg.
 *
 * Self-contained listener. Attaches to document on load and drives the
 * pre-existing #sentience-overlay / #sentience-terminal / #sentience-crosshair
 * DOM targets in base.html (plus @keyframes sentience-pulse / sentience-glitch
 * in base.css). Depends on a global `showToast(msg, type)` function exposed by
 * base.js / main.js — if absent, the final toast is silently skipped.
 *
 * Restored from git show ed03c43:hydra_detect/web/static/js/app.js after the
 * Apr 8 modular refactor (commit d71f5a3) accidentally dropped the listener.
 *
 * Trigger sequences (10 keys, match against e.key):
 *   Classic:  Up,Up,Down,Down,Left,Right,Left,Right,B,A
 *   Reverse:  Down,Down,Up,Up,ArrowLeft,ArrowRight,ArrowLeft,ArrowRight,KeyB,KeyA
 *
 * Skipped when document.activeElement is INPUT / TEXTAREA / SELECT.
 */

'use strict';

(function () {
    const KONAMI_CLASSIC = [
        'ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown',
        'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight',
        'b', 'a',
    ];
    const KONAMI_REVERSE = [
        'ArrowDown', 'ArrowDown', 'ArrowUp', 'ArrowUp',
        'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight',
        'b', 'a',
    ];

    let konamiBuffer = [];
    let sentienceActive = false;

    function arraysEqual(a, b) {
        return a.length === b.length && a.every((v, i) => v === b[i]);
    }

    function matchesSequence(buf) {
        const lower = buf.map(k => k.length === 1 ? k.toLowerCase() : k);
        return arraysEqual(lower, KONAMI_CLASSIC) || arraysEqual(lower, KONAMI_REVERSE);
    }

    function playSentienceSequence() {
        sentienceActive = true;
        const overlay = document.getElementById('sentience-overlay');
        const terminal = document.getElementById('sentience-terminal');
        const crosshair = document.getElementById('sentience-crosshair');
        if (!overlay || !terminal || !crosshair) { sentienceActive = false; return; }

        terminal.textContent = '';
        crosshair.classList.remove('pulse');
        crosshair.style.opacity = '0';
        overlay.classList.remove('glitch', 'active');
        overlay.style.display = 'flex';

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
                crosshair.style.opacity = '1';
                crosshair.classList.add('pulse');
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
                if (typeof window.showToast === 'function') {
                    window.showToast('Resuming manual control.', 'info');
                }
            }, 800);
        }

        setTimeout(showNextLine, 500);
    }

    function onKeydown(e) {
        const ae = document.activeElement;
        if (ae && ['INPUT', 'TEXTAREA', 'SELECT'].includes(ae.tagName)) return;
        if (sentienceActive) return;

        konamiBuffer.push(e.key);
        if (konamiBuffer.length > 10) konamiBuffer.shift();

        if (konamiBuffer.length === 10 && matchesSequence(konamiBuffer)) {
            konamiBuffer = [];
            playSentienceSequence();
        }
    }

    document.addEventListener('keydown', onKeydown);

    window.HydraEaster = {
        version: '2.0.0-konami-restored',
        attached: true,
        sequences: {
            classic: KONAMI_CLASSIC.slice(),
            reverse: KONAMI_REVERSE.slice(),
        },
    };
})();
