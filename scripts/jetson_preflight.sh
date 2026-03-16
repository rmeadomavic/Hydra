#!/usr/bin/env bash
set -euo pipefail

PASS=0
WARN=0
FAIL=0

ok() { echo "[PASS] $1"; PASS=$((PASS+1)); }
warn() { echo "[WARN] $1"; WARN=$((WARN+1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

check_cmd() {
  local cmd="$1"
  local label="$2"
  if command -v "$cmd" >/dev/null 2>&1; then
    ok "$label"
    return 0
  fi
  fail "$label (missing command: $cmd)"
  return 0
}

echo "Hydra Detect Jetson preflight"
echo "============================="

check_cmd python3 "python3 is installed"

if python3 -m pip --version >/dev/null 2>&1; then
  ok "pip is installed"
else
  fail "pip is not installed. Run: sudo apt install -y python3-pip"
fi

check_cmd nvpmodel "nvpmodel utility is available"
check_cmd tegrastats "tegrastats utility is available"
check_cmd systemctl "systemctl is available"

if command -v nvpmodel >/dev/null 2>&1; then
  mode_raw="$(nvpmodel -q 2>/dev/null || true)"
  if echo "$mode_raw" | rg -qi "MAXN"; then
    ok "Power model reports MAXN"
  else
    warn "Power model is not MAXN. Run: sudo nvpmodel -m 0"
  fi
fi

if command -v jetson_clocks >/dev/null 2>&1; then
  if jetson_clocks --show 2>/dev/null | rg -qi "ON|running"; then
    ok "jetson_clocks appears enabled"
  else
    warn "jetson_clocks may be disabled. Consider: sudo jetson_clocks"
  fi
else
  warn "jetson_clocks utility not found"
fi

if python3 -c "import cv2" >/dev/null 2>&1; then
  ok "OpenCV import works"
else
  fail "OpenCV import failed (check opencv-python-headless/opencv libs)"
fi

if python3 -c "import fastapi,uvicorn" >/dev/null 2>&1; then
  ok "Web stack imports (fastapi, uvicorn)"
else
  fail "Web stack imports failed"
fi

if [ -e /dev/video0 ]; then
  ok "Camera device /dev/video0 exists"
else
  warn "No /dev/video0 found (skip if using RTSP/file source)"
fi

if [ -e /dev/ttyACM0 ] || [ -e /dev/ttyUSB0 ]; then
  ok "Telemetry serial device present"
else
  warn "No telemetry serial device found (/dev/ttyACM0 or /dev/ttyUSB0)"
fi

if id -nG | rg -qw dialout; then
  ok "User is in dialout group (serial access)"
else
  warn "User is NOT in dialout group. Run: sudo usermod -aG dialout \$USER && logout"
fi

if [ -r config.ini ]; then
  if rg -q "^yolo_model\s*=" config.ini; then
    ok "config.ini detector model is configured"
  else
    warn "config.ini yolo_model not set (will use default yolov8n.pt)"
  fi
else
  fail "config.ini not found"
fi

# Check for model files
MODEL_DIR="$(dirname "$0")/../models"
if [ -d "$MODEL_DIR" ] && ls "$MODEL_DIR"/*.pt "$MODEL_DIR"/*.engine "$MODEL_DIR"/*.onnx >/dev/null 2>&1; then
  ok "Model files found in models/"
else
  warn "No model files found in models/. Download one: wget -P models https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8s.pt"
fi

echo
echo "Summary: PASS=$PASS WARN=$WARN FAIL=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
