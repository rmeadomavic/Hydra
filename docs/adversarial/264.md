# Adversarial Analysis — feat/pixhawk-wizard-pr-a (#158 PR-A)

**Generated:** 2026-05-25T22:12:00Z
**Target kind:** pr
**Target:** feat/pixhawk-wizard-pr-a (PR pending open)
**Branch head:** 32e5a5a40ff496d8cf75a92abe947e5f3d0d5321
**Base:** main at 19f1d3a8910fa8e885194cefe1ec472bfc4d69f5
**Diff size:** +1207 / -0 across 7 files (3 param packs, pixhawk_wizard.py, test_pixhawk_wizard.py, +398 server.py, +1 server.py import)

## Summary

- **Round 1:** 5 findings (0 high · 3 medium · 2 low). Pattern-match focused on the parallel-writer race window between /diff confirmation and /apply commit, freshness of the confirmed-hash check, and the failure-mode shape of the apply-time diff recomputation.
- **Round 2:** 4 second-order findings; **partially challenged R1-2 (downgraded scope)** — backup-filename collision is a real failure mode but only between two wizard runs in the same process, not across processes. Sharpened R1-3 (op-text), confirmed R1-1/R1-4/R1-5.
- **Round 3:** 4 findings, framing identified — *both prior rounds audited the wizard as the only writer to the FC, never asked what happens when a second writer (Mission Planner, on a separate USB serial; a GCS over telemetry radio) is connected at apply time and writes a param 50 ms after we do.*
- **Consolidated:** 0 blockers · 1 gate · 7 follow-ups · 0 dropped.

## Round 1 — Pattern Match

5 findings. Dominant pattern: a confirm-then-apply flow that takes a slow human round-trip in the middle (operator looks at the diff, clicks "apply") and re-races against the live FC at apply time. Hash check covers the race for the *params we are reading*, but the FC is a shared object with multiple potential writers.

| ID | Sev | Cat | Where | One-line claim |
|---|---|---|---|---|
| R1-1 | medium | race-window | server.py:1110-1180 (api_pixhawk_apply) | Window between GET /diff and POST /apply lets a third party (Mission Planner connected on a separate USB serial, or a GCS over radio) change params. `confirmed_diff_hash` recomputes the diff at apply and rejects with 409 if mismatched — addresses the race, but only for the params already in the pack. A change to an *unrelated* param (RC trim, motor PID) between diff and apply does not trigger 409. |
| R1-2 | medium | backup-collision | server.py:1058-1067 (_pixhawk_backup_path) | The spec called out ISO8601-with-second-precision collision; the implementation uses microsecond-precision UTC + PID suffix, which closes the collision window. The remaining gap: two wizard runs *in the same FastAPI process* at the same microsecond is mathematically possible but operationally negligible; two runs *across processes* are safe by PID. |
| R1-3 | medium | operator-text | server.py:1136 (409 response) | The 409 body says "diff changed since confirmation — re-run /diff and re-confirm." The operator sees this in PR-B's UI; without `fresh_diff` and `fresh_diff_hash` in the body, the UI has to re-fetch GET /diff to show the new state. The response *does* include both — good — but the message does not say *which* params changed, only that the hash changed. Operator needs that delta to decide whether to re-confirm. |
| R1-4 | low | path-traversal | server.py:1209-1218 (api_pixhawk_restore) | `backup_path.resolve()` against `Path("output_data") / "missions"` constrains the read to that directory tree, but uses a relative root that depends on the CWD of the FastAPI server. If the server is launched from a non-repo directory the comparison breaks — a malformed `backup_path` could escape. |
| R1-5 | low | param-set-type | pixhawk_wizard.py:191 (apply_pack) | Every param write uses `MAV_PARAM_TYPE_REAL32` (9) regardless of the param's actual on-FC type. ArduPilot accepts REAL32 for everything, but the on-the-wire type sent does not match the on-FC type for integer params (FENCE_ENABLE, ARMING_CHECK, FRAME_CLASS). EEPROM commits still happen correctly; on-the-wire fidelity is the only visible cost. |

## Round 2 — Counter-Critique

**Partially challenged:** R1-2 — re-read the path builder. `os.getpid()` differentiates across processes; microsecond precision differentiates within a process. The only path to collision is two wizard runs that share both PID *and* microsecond — i.e. the same FastAPI worker calling the apply endpoint from two coroutines that both reach `strftime("%Y%m%dT%H%M%S.%fZ")` in the same microsecond. With FastAPI's single-event-loop default, this is impossible (one coroutine yields between the strftime and the file write); under uvicorn with `--workers > 1`, each worker has a distinct PID. **Real-world collision probability is zero unless the operator is running two FastAPI processes with the same PID on the same host, which the OS does not permit.** Downgrade severity but keep the file-name format.

**Confirmed:** R1-1, R1-3, R1-4, R1-5 with sharper evidence. R1-1 is the most load-bearing — see R3 framing.

R2 second-order findings:

| ID | Sev | Cat | Where | One-line claim |
|---|---|---|---|---|
| R2-1 | medium | partial-failure | server.py:1170-1175 (apply loop) | `apply_pack` returns per-name results. If a write fails (timeout) mid-way through the pack, the FC is left in a mixed state: some params from the new pack applied, others still at their pre-wizard values. The endpoint does not currently attempt rollback from the just-captured backup on partial failure. Backup is captured before apply *for this reason* — operator can hit /restore to roll back — but the discoverability lives in PR-B's UI, not in the response shape. |
| R2-2 | medium | auth-bypass-via-cookie | server.py:1093-1101 (api_pixhawk_detect) | Bearer-token check is the same `_check_auth(...)` pattern used by every other control endpoint; valid Bearer or session cookie passes. The Pixhawk wizard does not add a *second* gate (e.g., explicit "platform setup mode" runtime flag) so an authenticated dashboard user can fire /apply at any time. This is consistent with other control endpoints, but the wizard is the first endpoint that writes to FC EEPROM, which is a strictly higher-blast-radius action than mode-set or LOITER. |
| R2-3 | low | conn-leak | server.py:1097-1101, 1138-1141 | Every endpoint opens a fresh mavutil connection and closes in `finally`. If the FC drops mid-collection (USB unplug), `recv_match(blocking=True)` may hold the connection open until quiescent timeout fires — a worst-case 10 s of leaked file descriptor per call. Acceptable for low-frequency wizard use, but a fast-clicking operator can stack a handful of dangling FDs. |
| R2-4 | nit | mavlink-cmd-magic-number | server.py:1102-1106 | `link.mav.command_long_send(..., 520, ...)` uses the literal MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES enum value. Other modules import `pymavlink.dialects.v20.common as mavlink2` and reference `mavlink2.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES`. Style nit. |

## Round 3 — Orthogonal Sweep

**Shared framing of R1+R2:** *Both rounds audited the wizard as the only writer to the FC — "is the diff fresh, is the apply atomic, is the backup correctly captured." Neither asked the structural question: ArduPilot accepts param writes from multiple MAVLink sources concurrently (USB + telemetry radio + RFD900 + companion computer can all push PARAM_SET). The wizard opens a fresh mavutil connection, takes the role of a new MAVLink endpoint, and treats the FC like a single-writer object. In the field the FC is more often a multi-writer object: SORCC operators routinely run Mission Planner on a laptop over telemetry radio while the Jetson companion (Hydra) also has MAVLink. A param the wizard "applied" can be overwritten 50 ms later by a Mission Planner push the operator forgot was queued. The wizard's response reports `post_value` for each name — but `post_value` is the value observed in the PARAM_VALUE ack, which is the FC's value at ack time, not the value the wizard wrote. If a second writer races between PARAM_SET-issued and PARAM_VALUE-acked, the ack carries the *winning* value, and the wizard surfaces it as `applied=True, post_value=<the wrong value>`.*

R3 findings:

| ID | Sev | Cat | Where | One-line claim |
|---|---|---|---|---|
| **R3-1** | **high** | **multi-writer** | server.py:1170-1175 (apply loop) + pixhawk_wizard.py:_await_param_value | After PARAM_SET, the wizard reads the next PARAM_VALUE for that name as the post-apply value. ArduPilot emits PARAM_VALUE *both* on operator-set acks *and* on third-party PARAM_SET broadcasts. If Mission Planner pushes FENCE_ENABLE=0 ~10 ms after our FENCE_ENABLE=1 PARAM_SET, our `_await_param_value` may match against MP's broadcast PARAM_VALUE (param_id matches), record `post_value=0.0`, and mark `applied=True` even though the *value the operator confirmed* did not survive. **Gate:** the response is the operator's only confirmation that the wizard's target value is the final value; surfacing the wrong value here means the operator believes geofence is enabled when it isn't. Recommendation: after the apply loop completes, do one final re-read of all touched names via `capture_backup`-style flow, and emit that final-state map alongside `results` so the operator can compare "what we tried" with "what the FC ended up at." The spec already calls for `post_value` per row — connect the dots by treating that re-read as authoritative. |
| R3-2 | medium | race-window-widening | server.py:1119-1135 (api_pixhawk_apply) | The /apply flow does: (1) connect, (2) request all params (~3-10 s on a wired link, longer over radio), (3) compute fresh diff, (4) hash check, (5) capture backup, (6) apply. Total wall-clock is on the order of 10-30 s. The hash freshness check happens at step 4; a writer that pushes a param at step 5 (during backup capture) or step 6 (during apply) is not caught. R3-1 addresses the apply-step race; the backup-step race means our captured backup may already contain stale values for params we're about to overwrite. Not a gate (the backup is "best-effort known-good-state" by construction), but worth documenting. |
| R3-3 | medium | restore-asymmetry | server.py:1242-1273 (api_pixhawk_restore) | /apply re-fetches live params and runs a hash check against the operator's confirmed view. /restore does not — it reads the backup file and writes every value back without any current-state comparison. If the FC is *already* at the backup values (operator hit restore twice by accident), every write is wasted EEPROM cycles. If the FC is at *different* values than the backup expects (operator hit restore on the wrong backup), there is no abort-on-mismatch. Restore is the more-dangerous endpoint of the two (writes pre-wizard values without any visual diff) but has the looser gate. Add a dry-run preview to /restore: read live, compute diff vs backup, return diff for confirmation, then accept a separate POST with the hash to commit. |
| R3-4 | low | callsign-from-runtime | server.py:1083 (_pixhawk_callsign_from_runtime) | The backup-path callsign falls back to "unknown" when runtime_config is missing or unreadable. SORCC fleet routinely runs without setting a callsign explicitly (it defaults inside MAVLinkIO/BatteryMonitor to "HYDRA"). Both happy paths land at "unknown" in `output_data/missions/unknown/` rather than the expected per-platform folder. Pull the same defaults as MAVLinkIO/BatteryMonitor ("HYDRA") so the wizard's backups land in a per-deployment folder by default. |

### Consistency-bias callouts

- **R1+R2 ranked R1-2 (backup-collision) above R3-1 (multi-writer post_value)** because the file-naming concern was pattern-visible from the spec. R3-1 is the higher-severity finding by a wide margin — the spec's `post_value` field becomes a credibility surface that lies about success in exactly the scenario the wizard is supposed to prevent.
- **The race-window framing (R1-1) led both rounds to focus on the operator+wizard dyad**, not the operator+wizard+MP triad. Once the third writer is admitted, four R1/R2 findings change shape: R1-1 widens (diff covers only the pack-relevant params), R1-3 widens (the 409 body should also include *who* wrote the changed params if any of them have origin tags — they don't in ArduPilot, so we can't), R2-1 widens (partial failure includes "applied by us, overwritten by MP"), and R3-1 emerges as a distinct finding.

## Consolidated Risk Register

| Sev | Category | Tag | Claim | Origin | Recommended gate |
|---|---|---|---|---|---|
| **high** | multi-writer | R3-1 | `post_value` in the apply response is the value observed in the next PARAM_VALUE for that name, which can be a third-party PARAM_SET broadcast (Mission Planner over telemetry radio). The operator reads `post_value` as confirmation; in the multi-writer case it can confirm the wrong value. Add an authoritative re-read pass after the apply loop and surface that map (or replace `post_value` with the re-read result). | R3-1 | **gate before merge of PR-B**; PR-A backend is technically correct in isolation but the operator-facing surface is in PR-B |
| medium | race-window | R1-1 + R3-2 | Hash freshness check covers the pack-relevant params at the start of /apply but the FC remains mutable through backup capture and apply. Document that the wizard assumes single-writer for the duration of /apply; recommend operator disconnect MP / power off the second radio for the duration. | R1-1, R3-2 | follow-up issue + UI banner in PR-B |
| medium | operator-text | R1-3 | 409 response body includes `fresh_diff` + `fresh_diff_hash` but the human-facing error string says only "diff changed since confirmation." PR-B UI should diff the operator's previous diff against `fresh_diff` and show only the params that changed, so the operator sees the deltas without re-reading the whole table. | R1-3 | follow-up in PR-B |
| medium | partial-failure | R2-1 | A mid-pack write timeout leaves the FC in a mixed state. Backup-and-restore is the recovery path, but the response shape does not currently surface "consider /restore" guidance. Add a `recommended_action` field to the apply response when `failed > 0` pointing the operator at `backup_path`. | R2-1 | follow-up in PR-B |
| medium | auth-gate | R2-2 | Wizard endpoints share the same auth gate as all other control endpoints, but they are the first to write FC EEPROM. A second gate ("platform setup mode" runtime flag the operator must enable before the wizard endpoints respond) would limit blast radius. Not blocking; the existing auth is consistent with the rest of the API. | R2-2 | follow-up issue |
| medium | restore-asymmetry | R3-3 | /restore writes the entire backup without a current-state diff. Add a `dry_run` query param that returns the diff vs current state; require a second call with the hash to commit. | R3-3 | follow-up in PR-B |
| low | path-traversal | R1-4 | `output_data/missions` is resolved relative to CWD. The constraint works only when the server is launched from the repo root. Anchor the comparison to `Path(__file__).resolve().parent.parent.parent / "output_data" / "missions"` so the relative-CWD assumption is explicit. | R1-4 | follow-up issue |
| low | param-set-type | R1-5 | Every PARAM_SET uses REAL32; integer params (FENCE_ENABLE, ARMING_CHECK, FRAME_CLASS) accepted by ArduPilot but the on-the-wire type is wrong. Best practice: read the param's reported type from PARAM_VALUE during the live-collection pass and reuse it for the apply pass. | R1-5 | follow-up issue |
| low | conn-leak | R2-3 | Worst-case 10 s of FD leak per call on unplugged-USB scenarios. Acceptable. Document the limit. | R2-3 | nit |
| low | callsign-default | R3-4 | Backups land under `output_data/missions/unknown/` when runtime_config has no callsign. Default to "HYDRA" to match other modules. | R3-4 | follow-up issue |
| nit | mavlink-magic | R2-4 | Use `mavlink2.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES` instead of the literal `520`. | R2-4 | nit |

## Recommendation

**One gate** before merge of PR-B (which adds the operator UI that surfaces `post_value` to the human):

1. **R3-1** — After the apply loop completes, do one re-read of every touched name via the same `_await_param_value` / `param_request_read_send` flow and replace each row's `post_value` with the re-read value (and add a `re_read_at` ISO8601 timestamp). The operator-visible "this is what the FC now reports" is then authoritative against multi-writer races. PR-A's backend can ship without this fix because PR-A has no UI; the credibility of the response field becomes load-bearing only in PR-B.

The other findings are follow-ups, not gates. The PR-A backend is testable, isolated from other surfaces, and the structural fixes (R3-1 re-read pass, R3-3 restore dry-run) can land alongside the PR-B UI work without churn on the PR-A merge.
