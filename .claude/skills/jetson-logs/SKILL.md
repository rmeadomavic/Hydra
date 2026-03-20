---
name: jetson-logs
description: Fetch live application logs from the Jetson for debugging — use when diagnosing runtime errors, crashes, or unexpected behavior
user-invocable: true
disable-model-invocation: false
argument-hint: "[lines] [level]"
---

# Jetson Live Logs

Fetch and display live application logs from the running Hydra instance on the
Jetson. Use this whenever you need runtime context for debugging.

## Usage

`/jetson-logs` — last 100 lines at INFO+
`/jetson-logs 200 WARNING` — last 200 lines at WARNING+

## How

The Hydra web API exposes `GET /api/logs?lines=N&level=LEVEL` which tails
`hydra.log` (a RotatingFileHandler writing all Python logging output).

Fetch via the Jetson's Tailscale IP:

```bash
curl -s 'http://100.109.160.122:8080/api/logs?lines=100&level=INFO'
```

Parse the args: first arg is line count (default 100), second is level (default INFO).

## Output

The API returns a JSON array of log entries:
```json
[{"timestamp": "...", "level": "INFO", "module": "hydra_detect.pipeline", "message": "..."}]
```

Display as a formatted table or list. Highlight ERROR and WARNING lines.
If the response is empty or the endpoint is unreachable, report that clearly.

## When to Use Proactively

- After a deploy fails or service won't start
- When the user reports unexpected behavior on the Jetson
- When debugging hardware issues (MAVLink, camera, Kismet)
- After changing config.ini settings that affect runtime behavior
- When a feature isn't working as expected on the live system
