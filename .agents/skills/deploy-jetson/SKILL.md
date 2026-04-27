---
name: deploy-jetson
description: Deploy latest code to Jetson — pull, install deps, restart service, validate
user-invocable: true
disable-model-invocation: false
---

# Deploy to Jetson

Deploy and validate Hydra on the Jetson via SSH (`ssh ${HYDRA_JETSON_USER}@${HYDRA_JETSON_IP}`).

## Important Lessons Learned

- **Code is baked into Docker** — `git pull` alone does NOT update the running
  code. Must rebuild the Docker image.
- **`/api/restart`** only restarts the pipeline loop, NOT the web server process.
  Code changes to `server.py` or JS require a full container restart.
- **Container name is `hydra-detect`** (not `hydra`).
- **Local `config.ini` changes** on the Jetson block `git pull` — always
  `git stash` first.
- **Never use `BaseHTTPMiddleware`** with `StreamingResponse` — it hangs
  infinite streams. Use pure ASGI middleware instead.

## Steps

Run each step via SSH. Report pass/fail for each.
Use `echo ${HYDRA_JETSON_PASS} | sudo -S` for sudo commands (password: ${HYDRA_JETSON_PASS}).

### Quick Deploy (preferred)

If `scripts/deploy.sh` exists on the Jetson:
```bash
ssh ${HYDRA_JETSON_USER}@${HYDRA_JETSON_IP} 'cd ~/Hydra && bash scripts/deploy.sh [branch]'
```

### Manual Deploy

1. **Connect** — `ssh ${HYDRA_JETSON_USER}@${HYDRA_JETSON_IP} echo ok`
2. **Pre-deploy snapshot** — record current commit: `cd ~/Hydra && git rev-parse --short HEAD`
3. **Stash local changes** — `git stash` (config.ini often has local edits)
4. **Pull** — `git pull origin <branch>`
5. **Rebuild Docker image** — `sudo docker build -t hydra-detect:latest .`
   (Code is baked into the image at build time — git pull alone does NOT update the running code)
6. **Restart service** — `sudo systemctl restart hydra-detect`
   (This stops the old container and starts a new one from the rebuilt image)
7. **Validate** — wait 20 seconds (YOLO model load), then:
   - `systemctl is-active hydra-detect`
   - `curl --max-time 3 -s -o /dev/null -w "%{http_code}" http://localhost:8080/stream.jpg` (expect 200)
   - `curl -s http://localhost:8080/api/stats | python3 -m json.tool | head -5`

If restart fails, report: "Service failed. Pre-deploy commit was `<hash>`.
Revert with: `git checkout <hash>`"

Report status of each step, then summarize overall result.
