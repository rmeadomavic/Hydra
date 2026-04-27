---
name: jetson-check
description: Pre-session Jetson hardware verification — checks connectivity, service, serial, disk, deps, camera, models
user-invocable: true
disable-model-invocation: false
---

# Jetson Pre-Flight Check

Run all checks via SSH to the Jetson (`ssh ${HYDRA_JETSON_USER}@${HYDRA_JETSON_IP}`).
If SSH fails, report the failure and stop.

This skill is **read-only** — it reports status but does not fix issues.
Present results as a markdown table (Check | Status | Detail).

## Checks

Run these via SSH commands:

1. **SSH** — verify connectivity (`ssh ${HYDRA_JETSON_USER}@${HYDRA_JETSON_IP} echo ok`)
2. **Service** — `systemctl is-active hydra-detect`
3. **Serial perms** — check udev rules exist: `ls /etc/udev/rules.d/*tty* /etc/udev/rules.d/*serial* 2>/dev/null`, then check current perms on `/dev/ttyTHS1` and `/dev/ttyUSB*`
4. **MAVLink** — check service logs for heartbeat: `journalctl -u hydra-detect --no-pager -n 50 | grep -i heartbeat`. Do NOT open the serial port directly.
5. **Disk** — `df -h /` (warn if <10% free)
6. **Python deps** — `cd ~/Hydra && pip check 2>&1 | head -20`
7. **Camera** — `ls /dev/video* 2>/dev/null`
8. **Models** — `ls ~/Hydra/models/*.pt ~/Hydra/models/*.engine 2>/dev/null`
9. **App Logs** — fetch recent errors: `curl -s 'http://${HYDRA_JETSON_IP}:8080/api/logs?lines=10&level=ERROR'`. Report count of errors; show messages if any found.

Report all results, then summarize: "X/9 checks passed. [list failures]"
