---
name: deploy-jetson
description: Deploy latest code to Jetson — pull, install deps, restart service, validate
user-invocable: true
disable-model-invocation: false
---

# Deploy to Jetson

Deploy and validate Hydra on the Jetson via SSH (`ssh sorcc@100.109.160.122`).

## Steps

Run each step via SSH. Report pass/fail for each.

1. **Connect** — `ssh sorcc@100.109.160.122 echo ok`
2. **Pre-deploy snapshot** — record current commit: `cd ~/Hydra && git rev-parse --short HEAD`
3. **Show changes** — `git log --oneline HEAD..origin/main` (after `git fetch`)
4. **Pull** — `git pull origin main`
5. **Install deps** — `pip install -r requirements.txt`
6. **Restart service** — `sudo systemctl restart hydra-detect`
7. **Validate** — wait 5 seconds, then:
   - `systemctl is-active hydra-detect`
   - `curl -s -o /dev/null -w "%{http_code}" http://localhost:8080` (expect 200)
   - `ls /dev/video* 2>/dev/null`

If restart fails, report: "Service failed. Pre-deploy commit was `<hash>`.
Revert with: `git checkout <hash>`"

Report status of each step, then summarize overall result.
