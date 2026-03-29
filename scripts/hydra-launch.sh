#!/usr/bin/env bash
# Launch Hydra Detect and open the web UI in a browser.
set -euo pipefail

HYDRA_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8080
URL="http://localhost:${PORT}"

cd "$HYDRA_DIR"

# Start Hydra in the background (native mode, not Docker)
sudo python3 -m hydra_detect --config config.ini &
HYDRA_PID=$!

# Wait for the web UI to come up
echo "Starting Hydra Detect..."
for i in $(seq 1 30); do
    if curl -s -o /dev/null "$URL" 2>/dev/null; then
        echo "Web UI is ready."
        xdg-open "$URL" 2>/dev/null &
        break
    fi
    sleep 1
done

# Keep running until Hydra exits
wait $HYDRA_PID
