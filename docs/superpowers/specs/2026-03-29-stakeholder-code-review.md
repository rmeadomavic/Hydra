# Hydra Detect v2.1.0 — Red Team Stakeholder Code Review

**Date:** 2026-03-29
**Reviewer:** Automated multi-stakeholder red team analysis
**Codebase commit:** `7f7fb49` (v2.1.0)
**Scope:** Full repository review — pipeline, web server, MAVLink, autonomous controller, approach controller, TAK I/O, RF hunt, camera, tracker, detection/event logging, config schema, model manifest, TLS, tests

---

## Preamble

This report evaluates Hydra Detect from the perspective of three distinct stakeholders who would each apply their own acquisition and safety lens. The review is intentionally adversarial — it is designed to identify every weakness, not to validate the work. Findings are organized by stakeholder, then by domain, with a rating table and a GO/NO-GO recommendation at the end.

Source files examined: `pipeline.py`, `web/server.py`, `mavlink_io.py`, `autonomous.py`, `approach.py`, `config_schema.py`, `tak/tak_output.py`, `tak/tak_input.py`, `rf/hunt.py`, `camera.py`, `tracker.py`, `detection_logger.py`, `event_logger.py`, `model_manifest.py`, `tls.py`, `web/config_api.py`, `detectors/base.py`, `requirements.txt`, plus representative tests across 50+ test files.

---

## Stakeholder 1 — Defense Contractor (Acquisition/Integration)

This reviewer asks: "Can we integrate this into our existing platform and rely on it?"

### Security and Hardening

**Authentication model — CONDITIONAL PASS with major gap.**
The web API uses HMAC-safe `hmac.compare_digest` for bearer token comparison, which is correct and avoids timing attacks. Auth is enforced on all control endpoints (strike, lock, mode change, camera switch, RF start, etc.) and unenforced only on explicitly listed read-only and abort endpoints. Rate limiting (50 failures per 60 seconds per IP) is present. This is solid for a field device.

The major gap: when `api_token` is empty in `config.ini` — which is the out-of-box state before first boot — auth is disabled entirely. The auto-token generation at first boot (`secrets.token_hex(32)`) mitigates this, but only after `pipeline.start()` runs. If the web server briefly accepts requests before the pipeline reaches that code path, or if a test/dev instance never runs `start()`, the system is wide open. The token is also written to `config.ini` on disk in cleartext with no file permission hardening.

**TLS — CONDITIONAL PASS.**
TLS support exists behind a `tls_enabled` config flag. It uses subprocess-spawned openssl to generate a self-signed RSA-2048 cert valid for 3650 days. This is acceptable for field-internal use, but:
- TLS is off by default. Credentials and MJPEG video traverse the network in cleartext unless the operator explicitly enables it.
- The 10-year cert lifetime is excessive. For a DoD system, 365 days or less is standard practice.
- There is no cert rotation mechanism or validity check on existing certs.
- The self-signed cert will trigger browser warnings, which field operators may habitually click through.

**CORS — PASS.**
Restricted to only `/api/stats` and `/api/abort` for the Fleet View page (cross-Jetson polling). All other endpoints stay same-origin. This is correctly scoped.

**CSP — CONDITIONAL PASS.**
`'unsafe-inline'` is used for both script-src and style-src on all pages. This negates the XSS protection benefit of the CSP entirely. The Fleet View page additionally uses `connect-src *`, making it a relay attack surface. These are acceptable trade-offs for an intranet device but would fail a DoD STIG review.

**Input validation — PASS on most endpoints, GAPS on two.**
Most control endpoints perform explicit type checking, range validation, and format validation (e.g., BSSID regex, mode allowlist). The `strike` endpoint requires `confirm: true` as an explicit boolean. The `set_mode` endpoint allowlists five modes. This is above average.

Gaps:
1. `POST /api/camera/switch` accepts a `source` value with no format validation. An operator can submit an arbitrary string. The Camera module handles this, but there is no length bound or URL schema check at the API layer.
2. `POST /api/models/switch` accepts an arbitrary model filename string with no path traversal check at the API layer. The model loader searches specific directories, so actual traversal is bounded — but the API does not reject suspicious input with a 400.

**Command injection — PASS.**
No `subprocess.run(shell=True)` calls were found in the hot paths. The TLS cert generation uses a static command list (not string interpolation). The Kismet manager uses subprocess with argument lists.

**Secrets management — CONDITIONAL PASS.**
The Kismet password is stored in `config.ini` in cleartext. It is correctly redacted from GET `/api/config/full` responses. The API token is also stored in `config.ini` but redacted from API responses. There is no secrets vault, no env-var injection path, and no encryption at rest. For a system that could be captured (drone crash), an adversary with physical access to the Jetson's eMMC gets the Kismet password and API token immediately.

**Rate limiting — CONDITIONAL PASS.**
Per-IP auth failure rate limiting (`50 / 60s`) is present. There is no rate limiting on read-only endpoints or the MJPEG stream. A DoS against the MJPEG stream by opening many connections is possible — each consumer gets a frame copy and JPEG encode. On a Jetson with 4-8 GB shared RAM, 10+ simultaneous MJPEG consumers could degrade GPU inference throughput.

**Supply chain — CONDITIONAL PASS.**
`requirements.txt` uses version ranges rather than pinned hashes. There is no lock file and no software bill of materials. Model integrity is handled correctly via SHA-256 hashes in `manifest.json` with chunked reads. The model hash is embedded in every detection log record for chain-of-custody. This is strong. The weak point is the PyPI dependency chain itself.

### Code Quality and "Vibe Coding" Detection

**AI-generated code indicators — LOW to MODERATE concern.**
The codebase shows signs consistent with AI-assisted development, but it appears to have been actively reviewed and shaped rather than copy-pasted without thought:
- Consistent `from __future__ import annotations` across all modules (enforced)
- Consistent dataclass usage for data containers
- Consistent audit logging pattern (`hydra.audit` logger)
- The "vibe coding" signal to watch for — unused imports, dead code, over-commented obvious things — is present at a low level:
  - Several modules import `Optional`, `Dict`, `Any`, `Callable` from `typing` while also using modern `X | None` syntax (the old imports are unnecessary under `from __future__ import annotations`)
  - This is a telltale AI generation artifact but is a minor code quality issue, not a functional one

**Architectural coherence — STRONG.**
The codebase has a clear design philosophy: composition over inheritance, dataclass containers, daemon threads with Events for lifecycle, callback injection from the pipeline to the web server, and a single-reader MAVLink pattern to prevent serial contention. These are deliberate architectural decisions. The `Pipeline` class is large but logically organized.

**Dead code and test quality — STRONG.**
50+ test files. Test quality is genuine:
- `test_autonomous.py` tests haversine, point-in-polygon, qualification criteria, geofence, cooldown, operator lock, and suppression
- `test_chain_of_custody.py` tests SHA-256 hash chain integrity including tampering detection
- `test_tak_security.py` tests HMAC verification, callsign routing including wildcards, and duplicate callsign detection
- Tests use specific `MagicMock` `.return_value` assertions — this demonstrates review

One weakness: `_init_target_state()` is called both from `__init__` and from `start()` (restart path), and it sets `self._servo_tracker = None` — overwriting the servo tracker built during init. This is a subtle bug introduced when the restart capability was added.

**Error handling — PASS.**
Specific exception types with contextual log messages. The MAVLink reader catches `TypeError` specifically (with a comment about the pymavlink internal bug) before the broad `Exception` catch. Critical paths like strike and lock use explicit return value checking.

### Utility and Integration

**MAVLink implementation — STRONG.**
- Single reader thread prevents `recv_match` race conditions
- Correct `source_component=191` (MAV_COMP_ID_ONBOARD_COMPUTER)
- `autoreconnect=True` on the connection
- Per-label alert throttling with configurable interval
- Global alert rate cap with priority-label bypass
- MGRS coordinate formatting with graceful fallback to lat/lon
- Custom `MAV_CMD_USER_1/2/3` for lock/strike/unlock over telemetry radio
- `_send_lock` serializes all MAVLink sends to prevent interleaving

One gap: the mode map reverse lookup builds a new dict on every HEARTBEAT message. At 2 Hz this is negligible but could be cached.

**TAK/CoT compliance — STRONG.**
- Correct MIL-STD-2525 type codes
- Proper `<event>`, `<point>`, `<detail>` XML structure
- Multicast TTL=32 for multi-segment LAN
- HMAC-SHA256 command authentication with `hmac.compare_digest`
- Callsign allowlist is fail-closed

One concern: incoming XML parsed with no size limit. UDP caps at 65535 bytes, but deep XML nesting could cause parsing overhead.

**Configuration management — STRONG.**
The `config_schema.py` typed validation system is well-designed:
- Every key has type, optional range, and plain-English description
- Unknown key detection catches typos
- `REDACTED_FIELDS` prevents token leakage
- Engagement-active field locking prevents safety-critical changes mid-engagement
- fsync-safe atomic write prevents corruption on power loss
- Auto-backup on boot for recovery

The `[approach]` and `[drop]` sections are missing from the SCHEMA definition. Keys in these sections will never be validated.

### Production Readiness

**Logging and observability — STRONG.**
- `hydra.audit` with structured `ts=actor=action=target=outcome=` format
- RotatingFileHandler (5 MB, 3 backups)
- Detection log chain-of-custody via SHA-256 hash chain
- Event timeline with action, detection, track, and state change events
- `/api/logs` endpoint for remote access without SSH
- `/api/preflight` structured pre-flight check

**Graceful degradation — STRONG.**
Camera loss suppresses autonomous controller. MAVLink failure does not abort the pipeline. RTSP, MAVLink video, RF hunt, TAK each fail independently with a warning, never cascading. Watchdog force-exits after configurable stall time.

**Resource management — CONDITIONAL PASS.**
Bounded queues and ring buffers throughout. One concern: auto-loiter calls `command_loiter()` on every detection frame (~15/s), potentially saturating the MAVLink link.

---

## Stakeholder 2 — USASOC SORD (SOF Fielding Evaluation)

This reviewer asks: "Is this safe to put on aircraft and in the hands of operators?"

### Safety and Fail-Safe Architecture

**Autonomous strike safety — STRONG.**
The `AutonomousController.evaluate()` implements a multi-gate safety model:
1. Enabled flag (off by default)
2. Vehicle mode allowlist
3. Geofence check (circle or polygon)
4. Cooldown timer between strikes (default 30s)
5. Class whitelist (fail-closed: empty = no valid targets)
6. Confidence threshold (default 85%)
7. Track persistence (default 5 consecutive frames)
8. Operator lock requirement (optional)
9. GPS staleness check
10. External suppression (camera loss)

All criteria must hold simultaneously. This is a defensible design for a training system.

**Critical safety concern — `require_operator_lock` defaults to `False`.**
If autonomous mode is enabled but `require_operator_lock` is not set in `config.ini`, the system will autonomously strike without requiring any human to identify and lock the target first. The default should be `True`.

**Servo channel safety — PASS.** Channel collision detection at init time with CRITICAL-level logging.

**Abort path — PASS.** `POST /api/abort` is unauthenticated. You should never need to authenticate to stop a vehicle.

**Camera loss response — STRONG.** Suppresses autonomous controller, sends STATUSTEXT, logs the state change, updates dashboard.

### Operator-Facing UX and Safety

**Pre-flight checklist — STRONG.** Structured checks with plain-English error messages.

**Config wipe-on-start — CONDITIONAL PASS.** `wipe_on_start = True` deletes all detection images and logs silently — no confirmation prompt.

**Multi-instance (CULEX) — CONDITIONAL PASS.** Callsign-based path separation works, but no runtime check for callsign uniqueness.

### Operational Reliability

**Restart — CONDITIONAL PASS.** MAVLink, RF hunt, and TAK are not reconnected on restart. Users expecting a full reset may be surprised.

**Watchdog — CONDITIONAL PASS.** `_exit(1)` bypasses Python cleanup, potentially leaving Kismet as an orphan process.

---

## Stakeholder 3 — JEB (Joint Experimentation Board)

This reviewer asks: "Does this meet the bar for broader DoD experimentation?"

### Standards Compliance

**MAVLink compliance — STRONG.** Uses `MAV_CMD_USER_1/2/3` and `MAV_COMP_ID_ONBOARD_COMPUTER`. Note: `REQUEST_DATA_STREAM` is deprecated in MAVLink 2 — should update to `SET_MESSAGE_INTERVAL`.

**TAK/CoT compliance — STRONG.** Proper MIL-STD-2525 type codes. HMAC authentication is a differentiator most TAK integrations lack.

**SBOM and dependency tracking — FAIL.** No SBOM, no pinned hashes, no CVE scanning.

**Documentation quality — STRONG.** Structured per-topic docs. API documentation matches code.

**Threat model — NOT DOCUMENTED.** Good security decisions exist but are not organized into a coherent threat model document.

---

## Consolidated Findings

### Critical (Block fielding)

| ID | Finding | Location | Confidence |
|----|---------|----------|------------|
| C1 | `require_operator_lock` defaults to `False` — autonomous strike without human confirmation | `config_schema.py`, `pipeline.py` | 95% |
| C2 | Auto-loiter sent every detection frame (~15/s), flooding MAVLink | `pipeline.py` | 90% |
| C3 | `_init_target_state()` clobbers servo tracker on restart | `pipeline.py` | 85% |

### Important (Fix before CULEX)

| ID | Finding | Location | Confidence |
|----|---------|----------|------------|
| I1 | Auth disabled before first `pipeline.start()` call | `web/server.py`, `pipeline.py` | 80% |
| I2 | TLS off by default, 10-year cert lifetime | `config_schema.py`, `tls.py` | 80% |
| I3 | `[approach]` and `[drop]` missing from config schema | `config_schema.py` | 90% |
| I4 | No pinned dependency hashes / SBOM | `requirements.txt` | 85% |
| I5 | `config.ini` permissions not hardened after token write | `pipeline.py` | 80% |

### Moderate (Fix before external evaluation)

| ID | Finding | Location | Confidence |
|----|---------|----------|------------|
| M1 | Callsign uniqueness not enforced at runtime | TAK subsystem | 80% |
| M2 | `REQUEST_DATA_STREAM` deprecated in MAVLink 2 | `mavlink_io.py` | 80% |
| M3 | Mode map rebuilt on every HEARTBEAT | `mavlink_io.py` | 80% |
| M4 | TAKInput has no UDP receive buffer size limit | `tak/tak_input.py` | 80% |
| M5 | Camera switch endpoint has no source validation | `web/server.py` | 82% |

---

## Rating Summary

| Domain | Defense Contractor | USASOC SORD | JEB |
|---|---|---|---|
| Security / Hardening | 3/5 | 4/5 | 2/5 |
| Code Quality / Authenticity | 4/5 | 4/5 | 4/5 |
| Utility / Integration | 4/5 | 4/5 | 4/5 |
| Production Readiness | 4/5 | 3/5 | 3/5 |
| **Overall** | **3.75/5** | **3.75/5** | **3.25/5** |

**Scoring key:** 1 = Unacceptable, 2 = Significant gaps, 3 = Meets minimum bar with conditions, 4 = Solid / field-ready, 5 = Exceeds expectations

---

## GO/NO-GO Recommendations

### Defense Contractor: CONDITIONAL GO
Architecturally sound, well-tested, operationally coherent. MAVLink and TAK implementations are above average. Address C1, C2, I2, I4, and produce a threat model document. After those actions, this is a viable integration candidate.

### USASOC SORD: CONDITIONAL GO for SORCC, NO-GO for operational use
For SORCC (controlled environment, supervised), the system is ready with one precondition: **C1 must be fixed** before operators use autonomous mode. C2 and C3 should also be resolved before CULEX. For operational use, I2/I4 and an ATO process would be required.

### JEB: NO-GO at current state
Blockers: no SBOM (I4), no threat model document, cleartext transport (I2), and operator lock default (C1). None are architectural — all are resolvable in one sprint. The test suite, audit logging, chain-of-custody, and TAK integration are genuine differentiators.

---

## What This Codebase Does Well

1. SHA-256 hash chain on detection logs with standalone `verify_log.py` — tamper-evident evidence trail
2. Model hash embedded in every detection record — provenance tracking
3. `hmac.compare_digest` for token comparison — no timing oracle
4. Audit logger with structured fields on every control action — accountability
5. Engagement-active safety lock on config writes — prevents mid-strike tampering
6. Camera loss suppresses autonomous controller — correct fail-safe response
7. Abort endpoint is explicitly unauthenticated — you can always stop the vehicle
8. Strike and drop require `confirm: true` — two-stage API confirmation
9. Callsign allowlist in TAK command listener is fail-closed
10. Geofence required before autonomous strike — spatial constraint enforcement
11. Detection queue bounded (maxsize=100) with drop-on-full — no unbounded memory growth
12. 50+ genuine test files with behavioral assertions, not just smoke tests

---

*Total source lines reviewed: ~8,000. Total test lines reviewed: ~2,500.*
