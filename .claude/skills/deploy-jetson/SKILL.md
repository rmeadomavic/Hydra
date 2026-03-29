---
name: deploy-jetson
description: Deploy latest code to Jetson — pull, install deps, restart service, validate
user-invocable: true
disable-model-invocation: false
---

# Deploy to Jetson

Deploy and validate Hydra on the Jetson via SSH (`ssh ${HYDRA_JETSON_USER}@${HYDRA_JETSON_IP}`).

## Steps

Run each step via SSH. Report pass/fail for each.
Use `echo ${HYDRA_JETSON_PASS} | sudo -S` for sudo commands (password: ${HYDRA_JETSON_PASS}).

1. **Connect** — `ssh ${HYDRA_JETSON_USER}@${HYDRA_JETSON_IP} echo ok`
2. **Pre-deploy snapshot** — record current commit: `cd ~/Hydra && git rev-parse --short HEAD`
3. **Show changes** — `git log --oneline HEAD..origin/main` (after `git fetch`)
4. **Pull** — `git pull origin main`
5. **Install deps** — `pip install -r requirements.txt`
6. **Rebuild Docker image** — `cd ~/Hydra && sudo docker build -t hydra-detect:latest .`
   (Code is baked into the image at build time — git pull alone does NOT update the running code)
7. **Stop old container** — `sudo docker rm -f hydra-detect` (prevents name conflict on restart)
8. **Restart service** — `sudo systemctl restart hydra-detect`
9. **Validate** — wait 15 seconds (YOLO model load), then:
   - `systemctl is-active hydra-detect`
   - `curl -s -o /dev/null -w "%{http_code}" http://localhost:8080` (expect 200)
   - `ls /dev/video* 2>/dev/null`

If restart fails, report: "Service failed. Pre-deploy commit was `<hash>`.
Revert with: `git checkout <hash>`"

Report status of each step, then summarize overall result.
