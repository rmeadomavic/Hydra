#!/bin/bash
# Deploy Hydra to the local Jetson.
# Usage: ./scripts/deploy.sh [branch]
set -e

BRANCH="${1:-main}"

cd ~/Hydra

# config.ini is per-unit state and is not tracked in git (the committed
# template is config.ini.factory). Keep a copy across the pull: units
# that still have the old *tracked* config.ini would otherwise lose it
# when the pull applies the commit that untracked the file.
echo "=== Preserving config.ini ==="
if [ -f config.ini ]; then
    cp config.ini /tmp/hydra-config.ini.deploy-keep
fi

echo "=== Stashing local changes ==="
git stash 2>/dev/null || true

echo "=== Pulling $BRANCH ==="
git pull origin "$BRANCH"

if [ ! -f config.ini ]; then
    if [ -f /tmp/hydra-config.ini.deploy-keep ]; then
        echo "=== Restoring preserved config.ini ==="
        cp /tmp/hydra-config.ini.deploy-keep config.ini
    else
        echo "=== No config.ini — bootstrapping from factory defaults ==="
        cp config.ini.factory config.ini
    fi
fi

echo "=== Building Docker image ==="
sudo docker build -t hydra-detect:latest .

echo "=== Restarting service ==="
sudo systemctl restart hydra-detect

echo "=== Waiting for startup (35s — YOLO model load) ==="
sleep 35

echo "=== Verifying ==="
HTTP=$(curl --max-time 3 -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/health)
if [ "$HTTP" = "200" ]; then
    echo "DEPLOY OK — /api/health returns 200"
else
    echo "DEPLOY WARNING — /api/health returned $HTTP"
    echo "Check: sudo docker logs hydra-detect --tail 30"
fi
