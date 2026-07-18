import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function loadScript(path) {
  const code = fs.readFileSync(path, 'utf8');
  vm.runInThisContext(code, { filename: path });
}

function setupGlobals() {
  global.window = { HydraModules: {}, location: { hash: '' }, addEventListener: () => {} };
  global.sessionStorage = { getItem: () => '', setItem: () => {} };
  global.document = {
    body: { classList: { remove: () => {}, add: () => {} } },
    querySelectorAll: () => [],
    getElementById: () => null,
    addEventListener: () => {},
    querySelector: () => null,
    activeElement: null,
    contains: () => true,
    hidden: false,
  };
}

test('hash-route switching normalizes aliases and invalid hashes', () => {
  setupGlobals();
  loadScript('hydra_detect/web/static/js/state/store.js');
  loadScript('hydra_detect/web/static/js/router/view-router.js');

  const store = window.HydraModules.createStore();
  const router = window.HydraModules.createViewRouter({ store });

  assert.equal(router.normalizeView('#operations'), 'config');
  assert.equal(router.normalizeView('#not-a-view'), 'ops');
  router.switchView('settings');
  assert.equal(store.getState().currentView, 'settings');
});

test('poller starts/stops detail pollers per active view', async () => {
  setupGlobals();
  loadScript('hydra_detect/web/static/js/state/store.js');
  loadScript('hydra_detect/web/static/js/polling/poller-manager.js');

  const store = window.HydraModules.createStore();
  const fetchImpl = async () => ({ ok: true, json: async () => ({}) });
  const manager = window.HydraModules.createPollerManager({ store, fetchImpl });

  manager.updatePollers('ops');
  await new Promise(r => setTimeout(r, 0));
  assert.deepEqual(manager.getActivePollers().sort(), ['detections', 'rf', 'stats', 'target', 'tracks']);

  manager.updatePollers('settings');
  assert.deepEqual(manager.getActivePollers().sort(), ['stats']);
  manager.stopPoller('stats');
});

test('backoff state is per poller — one failing endpoint does not slow the rest', async (t) => {
  if (!t.mock || !t.mock.timers || typeof t.mock.timers.enable !== 'function') {
    t.skip('node:test mock.timers unavailable on this Node — need >= 20.4');
    return;
  }
  setupGlobals();
  loadScript('hydra_detect/web/static/js/state/store.js');
  loadScript('hydra_detect/web/static/js/polling/poller-manager.js');

  t.mock.timers.enable({ apis: ['setTimeout'] });

  const calls = { good: 0, bad: 0 };
  const fetchImpl = async (url) => {
    if (url === '/good') { calls.good += 1; return { ok: true, json: async () => ({}) }; }
    calls.bad += 1;
    return { ok: false };
  };
  const store = window.HydraModules.createStore();
  const manager = window.HydraModules.createPollerManager({ store, fetchImpl });

  manager.startPoller('good', '/good', 1000, () => {});
  manager.startPoller('bad', '/bad', 1000, () => {});
  // Flush the immediate first polls.
  await new Promise(r => { r(); });
  await new Promise(r => { r(); });

  // Advance 8 base intervals. The healthy poller must fire ~once per
  // interval; the failing one backs off exponentially (2s, 4s, 8s...).
  // The window length is load-bearing for test honesty: with the old
  // SHARED fail counter the healthy poller's resets collapse the failing
  // poller's backoff and it reaches 5 calls by tick 8 (empirically traced
  // 2026-07-18) — over only 4 ticks both implementations stay <= 3 and the
  // test proves nothing.
  for (let i = 0; i < 8; i++) {
    t.mock.timers.tick(1000);
    // Let the async poll bodies settle between ticks.
    await new Promise(r => { r(); });
    await new Promise(r => { r(); });
  }

  assert.ok(calls.good >= 8, `healthy poller throttled: ${calls.good} calls in 8 intervals`);
  assert.ok(calls.bad <= 3, `failing poller not backing off: ${calls.bad} calls in 8 intervals`);

  manager.stopPoller('good');
  manager.stopPoller('bad');
  t.mock.timers.reset();
});

test('restarting a named poller mid-flight does not fork a second chain', async (t) => {
  if (!t.mock || !t.mock.timers || typeof t.mock.timers.enable !== 'function') {
    t.skip('node:test mock.timers unavailable on this Node — need >= 20.4');
    return;
  }
  setupGlobals();
  loadScript('hydra_detect/web/static/js/state/store.js');
  loadScript('hydra_detect/web/static/js/polling/poller-manager.js');

  t.mock.timers.enable({ apis: ['setTimeout'] });

  let calls = 0;
  let releaseFirst;
  const firstGate = new Promise(r => { releaseFirst = r; });
  const fetchImpl = async () => {
    calls += 1;
    if (calls === 1) await firstGate;  // hold the first request in flight
    return { ok: true, json: async () => ({}) };
  };
  const store = window.HydraModules.createStore();
  const manager = window.HydraModules.createPollerManager({ store, fetchImpl });

  manager.startPoller('stats', '/api/stats', 1000, () => {});
  await new Promise(r => { r(); });
  // Restart the same name while request #1 is still awaiting.
  manager.startPoller('stats', '/api/stats', 1000, () => {});
  await new Promise(r => { r(); });
  releaseFirst();
  await new Promise(r => { r(); });
  await new Promise(r => { r(); });

  const before = calls;
  // Two chains would fire ~2 calls per tick; a single chain fires 1.
  for (let i = 0; i < 4; i++) {
    t.mock.timers.tick(1000);
    await new Promise(r => { r(); });
    await new Promise(r => { r(); });
  }
  const perTick = (calls - before) / 4;
  assert.ok(perTick <= 1, `stale chain survived restart: ${perTick} calls/tick (want <= 1)`);

  manager.stopPoller('stats');
  t.mock.timers.reset();
});

test('modal controller handles escape close and tab focus trap', () => {
  setupGlobals();
  loadScript('hydra_detect/web/static/js/ui/modal.js');

  const focused = [];
  const first = { offsetParent: {}, focus: () => focused.push('first') };
  const last = { offsetParent: {}, focus: () => focused.push('last') };

  const modal = {
    __triggerElement: null,
    classList: { add: () => {}, remove: () => {} },
    querySelector: () => ({ focus: () => focused.push('dialog'), querySelectorAll: () => [first, last] }),
  };

  document.activeElement = last;
  document.querySelector = () => modal;

  const controller = window.HydraModules.createModalController();
  controller.openModal(modal, null);

  let prevented = false;
  controller._onKeyDown({ key: 'Tab', shiftKey: false, preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.ok(focused.includes('first'));

  let escPrevented = false;
  controller._onKeyDown({ key: 'Escape', preventDefault: () => { escPrevented = true; } });
  assert.equal(escPrevented, true);
});

test('stream pauses/resumes on visibility changes', () => {
  setupGlobals();
  let visibilityHandler;
  const img = { addEventListener: () => {}, src: '' };
  document.getElementById = (id) => (id === 'mjpeg-stream' ? img : null);
  document.addEventListener = (event, cb) => { if (event === 'visibilitychange') visibilityHandler = cb; };

  loadScript('hydra_detect/web/static/js/stream/stream-controller.js');

  let currentView = 'ops';
  const stream = window.HydraModules.createStreamController({ getCurrentView: () => currentView });
  stream.initStreamWatcher();
  stream.resumeStream();
  assert.equal(stream.isPolling(), true);

  document.hidden = true;
  visibilityHandler();
  assert.equal(stream.isPolling(), false);

  document.hidden = false;
  visibilityHandler();
  assert.equal(stream.isPolling(), true);
});
