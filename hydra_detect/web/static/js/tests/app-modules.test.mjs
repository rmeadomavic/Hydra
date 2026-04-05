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
