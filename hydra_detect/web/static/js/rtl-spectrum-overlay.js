'use strict';
/**
 * rtl-spectrum-overlay.js v5 — live SDR spectrum for the Ops cockpit cell.
 *
 * Must be loaded BEFORE ops.js. Intercepts setInterval at registration so
 * the legacy SDR sim-animator (which clobbers the SVG every 700ms) is
 * never started. Then polls /api/rf/spectrum and repaints real bars.
 */
(function () {
    var SVG_NS = 'http://www.w3.org/2000/svg';
    var POLL_MS = 1000;
    var PAINT_MS = 200;
    var SPAN_H = 34;
    var cached = null;

    var origSetInterval = window.setInterval;
    window.setInterval = function (fn, ms) {
        try {
            var fnStr = (typeof fn === 'function') ? String(fn) : '';
            if (ms >= 500 && ms <= 900 &&
                fnStr.indexOf('ops-cockpit-sdr-spectrum') !== -1) {
                return -1;
            }
        } catch (e) {}
        return origSetInterval.apply(window, arguments);
    };
    setTimeout(function () {
        try { window.setInterval = origSetInterval; } catch (e) {}
    }, 5000);

    function downsample(bins, n) {
        if (!bins || bins.length === 0) return [];
        var step = bins.length / n;
        var out = [];
        for (var i = 0; i < n; i++) {
            var lo = Math.floor(i * step);
            var hi = Math.floor((i + 1) * step);
            var maxDb = -Infinity;
            for (var j = lo; j < hi && j < bins.length; j++) {
                if (bins[j][1] > maxDb) maxDb = bins[j][1];
            }
            out.push(maxDb === -Infinity ? -100 : maxDb);
        }
        return out;
    }

    function paintBars(svg, d) {
        if (!svg || !d || !d.bins || !d.bins.length) return;
        var powers = downsample(d.bins, 80);
        var nf = (d.noise_floor_dbm == null) ? -30 : d.noise_floor_dbm;
        var thr = (d.threshold_dbm == null) ? -15 : d.threshold_dbm;
        var dbMin = nf - 1;
        var dbMax = nf + 30;
        var range = Math.max(1, dbMax - dbMin);
        while (svg.firstChild) svg.removeChild(svg.firstChild);
        var n = powers.length;
        var stride = 200 / n;
        for (var i = 0; i < n; i++) {
            var frac = Math.max(0, Math.min(1, (powers[i] - dbMin) / range));
            var h = Math.max(3, frac * SPAN_H);
            var bar = document.createElementNS(SVG_NS, 'rect');
            bar.setAttribute('x', (i * stride).toFixed(2));
            bar.setAttribute('y', (SPAN_H - h).toFixed(2));
            bar.setAttribute('width', Math.max(1, stride - 0.3).toFixed(2));
            bar.setAttribute('height', h.toFixed(2));
            var above = powers[i] >= thr;
            bar.setAttribute('fill', above ? 'var(--accent-alert, #ff6b35)' : 'var(--olive-primary, #7a8247)');
            bar.setAttribute('opacity', (0.55 + frac * 0.45).toFixed(2));
            svg.appendChild(bar);
        }
        svg.setAttribute('data-rtl-paint', String(Date.now()));
    }

    function fmtMhz(v) { return (Math.round(v * 1000) / 1000).toFixed(3) + ' MHz'; }

    function paintList(listEl, d) {
        if (!listEl) return;
        var peaks = (d && d.peaks) || [];
        var nf = d ? d.noise_floor_dbm : null;
        var emptyText = 'sweeping · NF ' + (nf == null ? '--' : nf.toFixed(1)) + ' dBm · no peaks';
        if (peaks.length === 0 &&
            listEl.firstElementChild &&
            listEl.firstElementChild.classList.contains('cockpit-sdr-empty') &&
            listEl.firstElementChild.textContent === emptyText) {
            return;
        }
        while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
        if (peaks.length === 0) {
            var e = document.createElement('div');
            e.className = 'cockpit-sdr-empty';
            e.textContent = emptyText;
            listEl.appendChild(e);
            return;
        }
        for (var i = 0; i < Math.min(peaks.length, 6); i++) {
            var p = peaks[i];
            var row = document.createElement('div');
            row.className = 'cockpit-sdr-row is-alert is-new';
            for (var k = 0; k < 5; k++) row.appendChild(document.createElement('span'));
            row.children[0].className = 'cockpit-sdr-row-type';
            row.children[1].className = 'cockpit-sdr-row-name';
            row.children[2].className = 'cockpit-sdr-row-mac';
            row.children[3].className = 'cockpit-sdr-row-vendor';
            row.children[4].className = 'cockpit-sdr-row-rssi';
            row.children[0].textContent = 'RF';
            row.children[1].textContent = fmtMhz(p.freq_mhz);
            row.children[2].textContent = '—';
            row.children[3].textContent = 'spectrum';
            row.children[4].textContent = p.dbm.toFixed(1);
            listEl.appendChild(row);
        }
    }

    function repaint() {
        var d = cached;
        if (!d) return;
        paintBars(document.getElementById('ops-cockpit-sdr-spectrum'), d);
        paintList(document.getElementById('ops-cockpit-sdr-list'), d);
        var subEl = document.querySelector('#ops-cockpit-sdr .cockpit-sdr-sub');
        var devEl = document.getElementById('ops-cockpit-sdr-dev');
        var newEl = document.getElementById('ops-cockpit-sdr-new');
        if (subEl) {
            subEl.textContent = 'RTL-SDR · ' + d.freq_low_mhz + '–' + d.freq_high_mhz +
                ' MHz · NF ' + (d.noise_floor_dbm || 0).toFixed(1) + ' dBm';
        }
        var pc = (d.peaks || []).length;
        if (devEl) devEl.textContent = pc || '--';
        if (newEl) newEl.textContent = pc || '--';
    }

    function poll() {
        fetch('/api/rf/spectrum', { cache: 'no-store' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d && d.enabled) cached = d; })
            .catch(function () {});
    }

    function start() {
        poll();
        origSetInterval.call(window, poll, POLL_MS);
        origSetInterval.call(window, repaint, PAINT_MS);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
