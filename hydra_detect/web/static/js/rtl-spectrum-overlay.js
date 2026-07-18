'use strict';
/**
 * rtl-spectrum-overlay.js v6 — live SDR spectrum for the Ops cockpit cell.
 *
 * Sole owner of the #ops-cockpit-sdr-spectrum SVG (issue #294: the legacy
 * sine-wave sim-animator in ops.js is deleted, along with the setInterval
 * monkey-patch this file used to suppress it). Polls /api/rf/spectrum and
 * paints real bars when the SDR is enabled, or an explicit "no spectrum
 * source" state when it is not — never decorative data.
 *
 * The peak LIST (#ops-cockpit-sdr-list) is only touched while spectrum data
 * is live; when the SDR is off, ops.js renderCockpitSdr owns the list.
 */
(function () {
    var SVG_NS = 'http://www.w3.org/2000/svg';
    var POLL_MS = 1000;
    var PAINT_MS = 200;
    var SPAN_H = 34;
    var cached = null;

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

    // Honest empty state (issue #294): a flat baseline + label instead of a
    // blank (or previously, animated fake) spectrum. Painted once; cleared
    // by the next real paintBars call.
    function paintSpectrumOff() {
        var svg = document.getElementById('ops-cockpit-sdr-spectrum');
        if (!svg || svg.getAttribute('data-rtl-off') === '1') return;
        while (svg.firstChild) svg.removeChild(svg.firstChild);
        var base = document.createElementNS(SVG_NS, 'rect');
        base.setAttribute('x', '0');
        base.setAttribute('y', String(SPAN_H - 1));
        base.setAttribute('width', '200');
        base.setAttribute('height', '1');
        base.setAttribute('fill', 'var(--text-dim, #666)');
        base.setAttribute('opacity', '0.4');
        svg.appendChild(base);
        var label = document.createElementNS(SVG_NS, 'text');
        label.setAttribute('x', '100');
        label.setAttribute('y', String(SPAN_H / 2 + 3));
        label.setAttribute('text-anchor', 'middle');
        label.setAttribute('fill', 'var(--text-dim, #666)');
        label.setAttribute('font-family', 'var(--font-mono, monospace)');
        label.setAttribute('font-size', '7');
        label.textContent = 'no spectrum source';
        svg.appendChild(label);
        svg.setAttribute('data-rtl-off', '1');
        svg.removeAttribute('data-rtl-paint');
    }

    function repaint() {
        var d = cached;
        if (!d) { paintSpectrumOff(); return; }
        var svgEl = document.getElementById('ops-cockpit-sdr-spectrum');
        if (svgEl) svgEl.removeAttribute('data-rtl-off');
        paintBars(svgEl, d);
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

    var pollFails = 0;
    function poll() {
        fetch('/api/rf/spectrum', { cache: 'no-store' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && d.enabled) {
                    cached = d;
                    pollFails = 0;
                } else {
                    // Explicit disabled/absent signal: drop immediately so
                    // stale bars don't linger as if live (issue #294).
                    cached = null;
                    pollFails = 0;
                }
            })
            .catch(function () {
                // Transient network hiccups keep the last real frame for a
                // few seconds; sustained failure falls to the empty state.
                pollFails++;
                if (pollFails >= 3) cached = null;
            });
    }

    function start() {
        poll();
        setInterval(poll, POLL_MS);
        setInterval(repaint, PAINT_MS);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
