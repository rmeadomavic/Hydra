# Security Policy

## Supported versions

The latest tagged release on `main` (currently v2.1.0) is supported. Earlier versions receive no fixes.

## Reporting a software vulnerability

Email **kyle.adomavicius@gmail.com** with:
- a description of the issue
- reproduction steps if you have them
- the affected version, tag, or commit hash

I will acknowledge within 7 days and aim to issue a fix or mitigation within 30 days for confirmed issues.

Please do **not** open a public GitHub issue for security reports — use email so the fix can ship before disclosure.

## Reporting a flight-safety / autonomy issue

Hydra is a payload that interacts with autonomous vehicles (drones, rovers, boats). Bugs that could cause unsafe vehicle behavior are treated as the highest priority.

If you discover a defect that could result in:
- loss of operator control,
- bypass of the autonomy gates (geofence, vehicle_mode, operator_lock, gps_fresh, cooldown),
- unintended Drop / Cue / Pixel-Lock actuation,
- incorrect TAK/CoT output to a tactical network,
- or any failure mode with risk to people, aircraft, vessels, or terrain,

**email immediately** with subject prefix `[FLIGHT-SAFETY]`. These reports take precedence over routine security reports.

## Scope

This policy covers:
- The `hydra_detect` Python package and ancillary scripts in this repository.
- The TAK/CoT pipeline, GeoChat input handling, and audit log integrity.
- The autonomy gate stack and dry-run/shadow/live mode transitions.

This policy does **not** cover:
- Vulnerabilities in third-party Python dependencies (report those upstream).
- ArduPilot firmware itself (report to https://github.com/ArduPilot/ardupilot/security).
- The host operating system on the Jetson Orin Nano.
- Physical hardware vulnerabilities in the camera, MAVLink link, or network stack.
