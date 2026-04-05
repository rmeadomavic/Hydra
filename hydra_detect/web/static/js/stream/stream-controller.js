'use strict';

window.HydraModules = window.HydraModules || {};

window.HydraModules.createStreamController = function createStreamController({ getCurrentView }) {
    let streamPolling = false;
    let streamBackoff = 1000;

    function pauseStream() {
        streamPolling = false;
    }

    function resumeStream() {
        if (streamPolling) return;
        streamPolling = true;
        streamBackoff = 1000;
        const img = document.getElementById('mjpeg-stream');
        if (img) img.src = '/stream.jpg?t=' + Date.now();
    }

    function toggleFullscreen() {
        const el = document.getElementById('mjpeg-stream');
        if (!el) return;
        if (document.fullscreenElement) document.exitFullscreen();
        else el.requestFullscreen().catch(() => {});
    }

    function initStreamWatcher() {
        const streamImg = document.getElementById('mjpeg-stream');
        if (!streamImg) return;

        const pollFrame = () => {
            if (!streamPolling) return;
            streamImg.src = '/stream.jpg?t=' + Date.now();
        };

        streamImg.addEventListener('load', () => {
            const lost = document.getElementById('ops-stream-lost');
            if (lost) lost.style.display = 'none';
            streamBackoff = 1000;
            if (streamPolling) setTimeout(pollFrame, 33);
        });

        streamImg.addEventListener('error', () => {
            if (!streamPolling) return;
            const lost = document.getElementById('ops-stream-lost');
            if (lost) lost.style.display = '';
            setTimeout(pollFrame, streamBackoff);
            streamBackoff = Math.min(streamBackoff * 2, 10000);
        });

        document.addEventListener('visibilitychange', () => {
            if (document.hidden) pauseStream();
            else {
                const view = getCurrentView();
                if (view === 'ops' || view === 'config') resumeStream();
            }
        });

        streamImg.addEventListener('dblclick', toggleFullscreen);
    }

    function syncForView(view) {
        if (view === 'config') resumeStream();
        else pauseStream();
    }

    return {
        initStreamWatcher,
        pauseStream,
        resumeStream,
        syncForView,
        toggleFullscreen,
        isPolling: () => streamPolling,
    };
};
