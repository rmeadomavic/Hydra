# Safety Review — 2026-04-20 (retroactive)

Catch-up audit for tonight's Phase 2 work. The handoff README
(`design_handoff_hydra_alignment/README.md`, "Safety gate" section)
required invoking `.claude/agents/safety-review` **before** touching
`approach.py`, `autonomous.py`, `mavlink_io.py`, `tak/tak_input.py`,
`tak/tak_output.py`, or the `/api/abort` path. That pre-edit step was
skipped. This review verifies the 8 named invariants post-hoc.

## Commits in scope

| SHA | Touches |
|---|---|
| `44bab72` | `mavlink_io.py` (+VFR_HUD), `server.py` (/api/stats flight fields) |
| `01929f3` | `autonomous.py` (+gate tap wiring) |
| `dea9771` | `autonomous.py` (+dashboard snapshot), `server.py` (autonomy endpoints) |
| `74b45ef` | `tak/tak_input.py` (+histogram, +peers, +TAK_CMD_ACCEPTED emit), `server.py` (observability endpoints) |
| `897d5e5` | `__main__.py` (audit JSONL sink wiring) — no safety-critical file touched |
| `9f2ec02` | `rf/kismet_poller.py`, `__main__.py` — no safety-critical file touched |
| `f135cdf` | `server.py` (/api/health, /api/metrics, /api/client_error) |

Files `approach.py`, `tak/tak_output.py` were **not touched** by any of
these commits. Their state is unchanged from the prior baseline.

## Verdicts per invariant

| # | Invariant | Verdict |
|---|---|---|
| 1 | SW arm and HW arm remain distinct interlocks | **PASS** |
| 2 | TAK ingestion fails closed, rejection audit-logged via `hydra.audit` | **PASS** (note: clock-skew check never implemented — pre-existing gap, not a regression) |
| 3 | SIM mode never silent — `(SIM)` / SIM pill | **PASS** |
| 4 | No new websockets | **PASS** |
| 5 | Drop/strike require SW arm AND HW arm AND recent operator confirm | **PASS** as unchanged (strict 3-factor is a B5 deliverable not yet landed; `approach.py` untouched tonight) |
| 6 | `/api/abort` always responds — callbacks wrapped in try/except | **PASS** |
| 7 | Autonomy dry-run + inhibit toggleable at runtime | **PASS** (note: `_suppressed` has a property setter but no REST endpoint — runtime-toggleable from pipeline, not from UI) |
| 8 | MAVLink public API only — no direct `_mav` / `_send_lock` | **PASS** |

## Per-invariant detail

### 1. SW arm and HW arm distinct — PASS

- **Audited:** `hydra_detect/approach.py:296-327` (`get_status()`),
  `hydra_detect/approach.py:353-369` (`get_hardware_arm_status()`).
- **Commits touching this code:** none tonight (approach.py unmodified).
- **Finding:** `get_status()` surfaces `software_arm` (derived from
  `arm_channel is not None`) and `hardware_arm_status` (RC channel
  PWM read) as distinct fields when `mode == STRIKE`. They are never
  collapsed into a single "ARMED" bool. `hw_arm_channel` is its own
  config key separate from `arm_channel`.

### 2. TAK fails closed with audit — PASS (with pre-existing gap)

- **Audited:** `hydra_detect/tak/tak_input.py:252-328` (allowlist +
  HMAC checks), `:337-375` (`_log_command_event`, which emits
  `TAK_CMD_ACCEPTED` on accept).
- **Commits touching this code:** `74b45ef` (adds histogram, peer
  roster, and `TAK_CMD_ACCEPTED` emit on accept).
- **Finding:** every rejection path emits an
  `audit_logger.warning("TAK_CMD_REJECTED reason=...")` line to the
  `hydra.audit` logger before returning:
  - `:260-269` no_allowlist (fail-closed when allowlist empty)
  - `:271-281` unauthorized_sender
  - `:299-304` hmac_missing (when secret configured)
  - `:320-326` hmac_invalid
  - `:727-730` hmac_custom_cot (same guard on custom CoT path)

  The B9 audit sink (`hydra_detect/audit/audit_log.py`) attaches a
  handler to the `hydra.audit` logger, so every rejection is captured
  in the rolling ring + rotating JSONL sink.

- **Gap (pre-existing):** no CoT-timestamp clock-skew check exists. The
  invariant language mentions clock-skew; that rail has never been
  implemented and was not removed or regressed tonight. Flag to Kyle
  for a future hardening pass; not a retroactive NEEDS_FIX for this
  window.

### 3. SIM mode never silent — PASS

- **Audited:** `hydra_detect/mavlink_io.py:470-508` (sets
  `_is_sim_gps` True when falling back to configured sim GPS),
  `hydra_detect/pipeline/facade.py:1452` (feeds `is_sim_gps` into
  `/api/stats`), frontend surfaces:
  - `web/templates/base.html:81-84` — SIM topbar blip + amber SIM pill
  - `web/static/js/main.js:81,88-91` — toggles blip + pill from stats
  - `web/static/js/ui/sim-gps.js:10-15` — `(SIM)` suffix helper
  - `web/static/js/systems.js:322,351-353` — GPS detail includes `· SIM`
- **Commits touching this code:** none tonight. Pre-existing mechanism
  still intact.
- **Finding:** any stats poll that observes `is_sim_gps=true` will
  raise the topbar pill and tag the system-view GPS row. No silent
  path surfaces sim coords to the operator.

### 4. No new websockets — PASS

- **Audited:** `grep -rn 'websocket|WebSocket|ws://|wss://|@app.websocket'
  hydra_detect/` — zero matches.
- **Finding:** the `/stream.jpg` polling architecture remains the only
  video fan-out; stats + tracks + audit all use polled GET endpoints.

### 5. Drop/strike require SW arm AND HW arm AND recent operator confirm — PASS (unchanged)

- **Audited:**
  - Operator confirm: `web/server.py:1490-1557` — both `/api/approach/drop/{id}`
    and `/api/approach/strike/{id}` reject without `body.confirm=true`
    and require Bearer auth.
  - SW arm: `approach.py:148-156` — `start_strike` drives servo
    `arm_channel` to `arm_pwm_armed` when configured.
  - HW arm (strike): `approach.py:469-476` — `_update_strike` aborts
    if `hw_arm_channel` is configured but `get_hardware_arm_status()`
    is not `True` (None treated as unsafe → fail-closed).
  - HW arm (drop): **not implemented** in `_update_drop`
    (`approach.py:420-461`).
- **Commits touching this code:** none tonight. `approach.py` and the
  `/api/approach/*` endpoints are unchanged.
- **Finding:** the strict 3-factor interlock (SW ∧ HW ∧ time-windowed
  confirm) is a B5 deliverable per `README.md:362-364`:
  > *"N confirmed with user during B5 — do not guess."*
  Tonight's commits did not regress the current 2-factor state (SW
  arm + HW arm on strike; confirm-flag only on drop). B5 will close
  the gap; until then, this review confirms no safety property was
  weakened.

### 6. /api/abort always responds — PASS

- **Audited:** `web/server.py:2322-2344`.
- **Commits touching this code:** none tonight. Path untouched by
  `f135cdf`'s observability work.
- **Finding:** each mode callback invocation
  (`RTL → LOITER → HOLD`) is wrapped in `try/except Exception`; a
  callback crash falls through to the next mode, and if every mode
  fails or MAVLink is disconnected the endpoint still returns a 503
  JSON error. Endpoint is in `_PUBLIC_PATH_PREFIXES` (server.py:337)
  so auth never blocks an instructor abort.

### 7. Autonomy dry-run + inhibit toggleable at runtime — PASS (with UI gap)

- **Audited:** `autonomous.py:580-593` (`set_mode` / `get_mode`),
  `autonomous.py:449-456` (`suppressed` property with setter),
  `web/server.py:2117-2148` (POST /api/autonomy/mode, bearer-auth,
  validates against `("dryrun","shadow","live")`).
- **Commits touching this code:** `dea9771` added the endpoints and
  the dashboard snapshot; `01929f3` added gate tap recording.
- **Finding:**
  - Dry-run: `POST /api/autonomy/mode {"mode":"dryrun"}` is wired and
    audited (`_audit(request, "autonomy_mode", target=mode)`,
    server.py:2147; plus `audit_log.info("autonomy_mode_set mode=%s")`,
    autonomous.py:589).
  - Inhibit: `AutonomousController.suppressed` is a property with a
    setter, so pipeline code can toggle at runtime. No REST endpoint
    exposes it yet — flag for future B-series work, but the invariant
    says *toggleable at runtime, not only via config* and the setter
    satisfies that literal reading.
  - Caveat noted explicitly in `autonomous.py:161-163`: mode does not
    yet gate `evaluate()` behaviour — it is a display state machine
    for this wave. That is consistent with the pre-existing spec and
    not a regression.

### 8. MAVLink public API only — PASS

- **Audited:** `grep -n '\._mav[^a-z_]\|\._send_lock' hydra_detect/` —
  all matches are either (a) inside `mavlink_io.py` itself (owning
  class accessing its own private `_mav` / `_send_lock`) or (b) a
  local attribute `self._mav = mavlink_io` in `tak_output.py:57`,
  `osd.py:93`, `geo_tracking.py:23`. The latter three never reach into
  `self._mav._mav.*` or `self._mav._send_lock` — all calls go through
  public surface (`.get_lat_lon()`, `.send_raw_message()`,
  `.send_param_set()`, `.send_statustext()`, `.estimate_target_position()`,
  `.get_heading_deg()`, `.get_telemetry()`, `.connected`).
- **Commits touching this code:** `44bab72` added `_handle_vfr_hud`
  and `get_flight_data()` inside `MAVLinkIO`. All new code lives in
  the owning class and uses `self._gps_lock`, not `_send_lock`. Public
  surface widened (new `get_flight_data()`), not narrowed; no external
  module reaches past the public API.

## Summary

No invariant regressed in the audited commit window. Two standing gaps
predate this work and are explicit deferrals, not retroactive fixes:

1. **CoT clock-skew validation** — never implemented; invariant text
   lists it as a third fail-closed rail. Worth a small hardening
   ticket so tak_input rejects CoT events whose `time`/`stale`
   attributes fall outside a configurable window.
2. **Autonomy inhibit REST endpoint** — property setter exists,
   no POST. Not strictly required for "toggleable at runtime" but
   closes the UI loop that `/api/autonomy/mode` opens for dry-run.

Both are recommendations, not NEEDS_FIX items. Kyle decides scope.
