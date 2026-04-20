import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function loadOpsHarness() {
  const elements = new Map();

  function makeEl(id) {
    const el = {
      id,
      style: { display: '' },
      _text: '',
      _listeners: {},
      classList: {
        _set: new Set(),
        add(c) { this._set.add(c); },
        remove(c) { this._set.delete(c); },
        toggle(c, force) {
          const has = this._set.has(c);
          const want = force === undefined ? !has : !!force;
          if (want) this._set.add(c); else this._set.delete(c);
        },
        contains(c) { return this._set.has(c); },
      },
      addEventListener(type, cb) {
        (this._listeners[type] = this._listeners[type] || []).push(cb);
      },
      dispatchEvent(type) {
        (this._listeners[type] || []).forEach(cb => cb({}));
      },
      get textContent() { return this._text; },
      set textContent(v) { this._text = String(v); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      getBoundingClientRect() { return { width: 1280, height: 720, left: 0, top: 0 }; },
      children: [],
      appendChild() {},
      removeChild() {},
      replaceChild() {},
      contains() { return false; },
      requestFullscreen() { return { catch() {} }; },
    };
    return el;
  }

  const toasts = [];
  const apiCalls = [];
  let apiResponse = { status: 'ok' };

  global.window = {
    HydraModules: {},
    location: { hash: '' },
    addEventListener: () => {},
    devicePixelRatio: 1,
  };
  global.sessionStorage = { getItem: () => '', setItem: () => {} };
  global.document = {
    body: { classList: { remove: () => {}, add: () => {} } },
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener: () => {},
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, makeEl(id));
      return elements.get(id);
    },
    activeElement: null,
    contains: () => true,
    hidden: false,
    fullscreenElement: null,
    exitFullscreen: () => {},
  };
  global.setInterval = () => 0;
  global.clearInterval = () => {};
  global.setTimeout = () => 0;

  global.HydraApp = {
    state: { stats: {}, target: {}, tracks: [] },
    apiPost: async (url, body) => {
      apiCalls.push({ url, body });
      return apiResponse;
    },
    showToast: (msg, type) => { toasts.push({ msg, type }); },
    openModal: () => {},
    closeModal: () => {},
  };

  const code = fs.readFileSync('hydra_detect/web/static/js/ops.js', 'utf8');
  vm.runInThisContext(code, { filename: 'ops.js' });

  return {
    HydraOps: global.HydraOps,
    elements,
    toasts,
    apiCalls,
    setApiResponse(r) { apiResponse = r; },
    getEl(id) { return elements.get(id); },
  };
}

test('updateApproachPanel hides section when stats has no approach key', () => {
  const h = loadOpsHarness();
  h.HydraOps.updateApproachPanel({});
  assert.equal(h.getEl('ops-approach-section').style.display, 'none');
});

test('updateApproachPanel hides section when approach.mode === "idle"', () => {
  const h = loadOpsHarness();
  h.HydraOps.updateApproachPanel({ approach: { mode: 'idle' } });
  assert.equal(h.getEl('ops-approach-section').style.display, 'none');
});

test('updateApproachPanel with follow active shows section, hides arm sub-panel', () => {
  const h = loadOpsHarness();
  h.HydraOps.updateApproachPanel({
    approach: { mode: 'follow', elapsed_sec: 12, waypoints_sent: 3 },
  });
  assert.equal(h.getEl('ops-approach-section').style.display, '');
  assert.equal(h.getEl('ops-approach-mode').textContent, 'FOLLOW');
  assert.equal(h.getEl('ops-approach-elapsed').textContent, '12s');
  assert.equal(h.getEl('ops-approach-wp').textContent, '3');
  assert.equal(h.getEl('ops-approach-arm-status').style.display, 'none');
});

test('updateApproachPanel strike + SW armed + HW null → N/A with dim', () => {
  const h = loadOpsHarness();
  h.HydraOps.updateApproachPanel({
    approach: {
      mode: 'strike',
      elapsed_sec: 4,
      waypoints_sent: 1,
      software_arm: true,
      hardware_arm_status: null,
    },
  });
  assert.equal(h.getEl('ops-approach-section').style.display, '');
  assert.equal(h.getEl('ops-approach-arm-status').style.display, 'block');
  assert.equal(h.getEl('ops-approach-sw-arm').textContent, 'ARMED');
  assert.equal(h.getEl('ops-approach-sw-arm').style.color, 'var(--olive-muted)');
  assert.equal(h.getEl('ops-approach-hw-arm').textContent, 'N/A');
  assert.equal(h.getEl('ops-approach-hw-arm').style.color, 'var(--text-dim)');
});

test('updateApproachPanel strike + both armed → both ARMED with olive-muted', () => {
  const h = loadOpsHarness();
  h.HydraOps.updateApproachPanel({
    approach: {
      mode: 'strike',
      elapsed_sec: 9,
      waypoints_sent: 2,
      software_arm: true,
      hardware_arm_status: true,
    },
  });
  assert.equal(h.getEl('ops-approach-hw-arm').textContent, 'ARMED');
  assert.equal(h.getEl('ops-approach-hw-arm').style.color, 'var(--olive-muted)');
  assert.equal(h.getEl('ops-approach-sw-arm').style.color, 'var(--olive-muted)');
});

test('updateApproachPanel does not throw on undefined stats', () => {
  const h = loadOpsHarness();
  assert.doesNotThrow(() => h.HydraOps.updateApproachPanel(undefined));
  assert.equal(h.getEl('ops-approach-section').style.display, 'none');
});

test('updateApproachPanel guards against missing mode key (not a string)', () => {
  const h = loadOpsHarness();
  h.HydraOps.updateApproachPanel({ approach: {} });
  assert.equal(h.getEl('ops-approach-section').style.display, 'none');
});

test('abortApproach posts to /api/approach/abort and fires info toast on success', async () => {
  const h = loadOpsHarness();
  h.setApiResponse({ status: 'ok' });
  await h.HydraOps.abortApproach();
  assert.equal(h.apiCalls.length, 1);
  assert.equal(h.apiCalls[0].url, '/api/approach/abort');
  assert.equal(h.toasts.length, 1);
  assert.equal(h.toasts[0].type, 'info');
});

test('abortApproach fires error toast when apiPost returns falsy', async () => {
  const h = loadOpsHarness();
  h.setApiResponse(null);
  await h.HydraOps.abortApproach();
  assert.equal(h.toasts.length, 1);
  assert.equal(h.toasts[0].type, 'error');
});
