# UGV strike profile

## Select the profile

On each kit, set `/etc/hydra/vehicle.env` to:

```sh
HYDRA_VEHICLE=ugv_strike
```

Then reload the service:

```sh
sudo systemctl restart hydra-detect
```

The profile enables autonomous evaluation, servo tracking, truck/car target
selection, GUIDED operation, operator-selected target locking, a 25 km circular
geofence, and continuous strike approach updates at 0.1-second intervals to a
0.5 m close-approach estimate. Arm channels remain disabled. Post-strike,
abort, and autonomous fallback modes are HOLD.

## Geofence

Centered on the OGT Test & Training Center, 158 Rushing Rd, Hoffman NC
(35.03671, -79.52414), derived from campus grid `17SPU 3462 7811`. The 25 km
radius is deliberately non-binding and covers T&TC, the Villages cultural site,
Broadacres Lake, and the Foxfire Rd field roughly 17 km out.

The fence cannot be disabled: `_has_valid_geofence()` requires a polygon or a
nonzero lat/lon, and both `_eval_autonomy_live` and `_eval_autonomy_dryrun`
reject every detection without one. A wide fence is the supported way to keep
it out of the way.

Operating outside the radius does not raise an error — the evaluator simply
never qualifies a target, which presents as the autonomy silently doing
nothing. To run at another site, re-center rather than widening.
`tests/test_ugv_strike_profile.py` pins the covered sites.

## Profiles do not inherit

`hydra_detect/pipeline/bootstrap.py` reads only the single `[vehicle.<name>]`
section named by `HYDRA_VEHICLE`. `[vehicle.ugv_strike]` gets nothing from
`[vehicle.ugv]`. Any key you need must be restated in full.

This matters most for `reserved_channels`, which is a vehicle-local key with no
section prefix. `facade.py` reads it with `fallback=""`, so a missing value
skips the servo and light-bar channel-conflict check rather than failing
loudly. On the truck, channels 1 and 3 are steering and throttle.

## Capability preconditions

- **Follow** checks MAVLink connectivity, GPS fix type 3 or higher, and a locked
  track in `hydra_detect/capability_status.py::_eval_follow`.
- **Servo Tracking** checks that a servo controller has claimed its channel. A
  claimed controller with no locked track reports WARN; a claimed controller
  with a locked track reports READY. The check is in
  `hydra_detect/capability_status.py::_eval_servo_tracking`.
- **Autonomy Live** checks MAVLink connectivity, GPS fix type 3 or higher, a
  valid configured geofence, `autonomous.enabled=true`, and operating mode
  `ARMED`. The check is in
  `hydra_detect/capability_status.py::_eval_autonomy_live`.

## Strike paths

`hydra_detect/pipeline/facade.py::_handle_strike_command` sends one GUIDED
waypoint at the estimated target GPS position. Other profiles retain this
one-shot autonomous path.

`hydra_detect/pipeline/facade.py::_handle_approach_strike_command` calls
`approach.start_strike()` and enters `ApproachMode.STRIKE`. That path updates a
GUIDED waypoint continuously from the current locked track and commands twice
the configured follow maximum speed. Use the continuous path for a moving
target.

For `ugv_strike`, the autonomous controller is wired to the continuous handler
by `hydra_detect/pipeline/facade.py::_autonomous_strike_handler`.

Payload drop and RF hunt remain unconditionally BLOCKED in
`hydra_detect/capability_status.py::_eval_drop` and `_eval_rf_hunt` on issue
#147.
