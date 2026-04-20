#!/bin/bash
# Deploy Hydra to the local Jetson.
# Usage: ./scripts/deploy.sh [branch]
set -e

BRANCH="${1:-main}"

cd ~/Hydra
echo "=== Stashing local changes ==="
git stash 2>/dev/null || true

echo "=== Pulling $BRANCH ==="
git pull origin "$BRANCH"

echo "=== Building Docker image ==="
sudo docker build -t hydra-detect:latest .

echo "=== Restarting service ==="
sudo systemctl restart hydra-detect

echo "=== Waiting for startup (20s) ==="
sleep 20

echo "=== Verifying ==="
HTTP=$(curl --max-time 3 -s -o /dev/null -w "%{http_code}" http://localhost:8080/stream.jpg)
if [ "$HTTP" = "200" ]; then
    echo "DEPLOY OK — /stream.jpg returns 200"
else
    echo "DEPLOY WARNING — /stream.jpg returned $HTTP"
    echo "Check: sudo docker logs hydra-detect --tail 30"
fi
