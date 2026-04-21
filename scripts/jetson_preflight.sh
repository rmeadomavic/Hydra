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
  if echo "$mode_raw" | grep -qi "MAXN"; then
    ok "Power model reports MAXN"
  else
    warn "Power model is not MAXN. Run: sudo nvpmodel -m 0"
  fi
fi

if command -v jetson_clocks >/dev/null 2>&1; then
  if jetson_clocks --show 2>/dev/null | grep -qiE "ON|running"; then
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

# Check for USB capture dongles (CVBS-to-USB for analog FPV cameras)
CAPTURE_FOUND=0
for dev in /dev/video*; do
  [ -e "$dev" ] || continue
  idx="${dev#/dev/video}"
  sysfs="/sys/class/video4linux/video${idx}/name"
  if [ -r "$sysfs" ]; then
    devname="$(cat "$sysfs" 2>/dev/null | tr '[:upper:]' '[:lower:]')"
    case "$devname" in
      *capture*|*macrosilicon*|*"av to usb"*|*"usb video"*|*easycap*|*uvc*)
        ok "USB capture dongle detected: $dev ($devname)"
        CAPTURE_FOUND=1
        ;;
    esac
  fi
done
if [ "$CAPTURE_FOUND" -eq 0 ]; then
  warn "No USB capture dongle detected (only needed for analog FPV cameras)"
fi

check_cmd v4l2-ctl "v4l2-ctl is installed (v4l-utils, needed for analog cameras)"

SERIAL_FOUND=""
for dev in /dev/ttyTHS1 /dev/ttyTHS2 /dev/ttyACM0 /dev/ttyUSB0; do
  if [ -e "$dev" ]; then
    SERIAL_FOUND="${SERIAL_FOUND:+$SERIAL_FOUND, }$dev"
  fi
done
if [ -n "$SERIAL_FOUND" ]; then
  ok "Telemetry serial device present: $SERIAL_FOUND"
else
  warn "No telemetry serial device found (checked /dev/ttyTHS1, /dev/ttyTHS2, /dev/ttyACM0, /dev/ttyUSB0)"
fi

if id -nG | grep -qw dialout; then
  ok "User is in dialout group (serial access)"
else
  warn "User is NOT in dialout group. Run: sudo usermod -aG dialout \$USER && logout"
fi

if [ -r config.ini ]; then
  if grep -q "^yolo_model\s*=" config.ini; then
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

# Verify fail-safe defaults are set in config.ini (checklist sec 12)
if [ -r config.ini ]; then
  fail_safe_ok=1
  for key in "autonomous.enabled" "rf_homing.enabled"; do
    section="${key%%.*}"
    setting="${key#*.}"
    val="$(awk -v s="[$section]" -v k="$setting" '
      $0==s {in_s=1; next}
      /^\[/ {in_s=0}
      in_s && $0 ~ "^[[:space:]]*"k"[[:space:]]*=" {
        sub(/^[^=]*=[[:space:]]*/, "")
        sub(/[[:space:]]*$/, "")
        print
        exit
      }' config.ini 2>/dev/null)"
    if [ "$val" = "false" ] || [ "$val" = "False" ] || [ "$val" = "0" ] || [ "$val" = "no" ]; then
      ok "Fail-safe default: [$section] $setting = $val"
    else
      warn "Fail-safe default: [$section] $setting = '$val' (expected false)"
      fail_safe_ok=0
    fi
  done
  drop_ch="$(awk '/^\[drop\]/{in_s=1; next} /^\[/{in_s=0} in_s && /^[[:space:]]*servo_channel[[:space:]]*=/ {sub(/^[^=]*=[[:space:]]*/, ""); sub(/[[:space:]]*$/, ""); print; exit}' config.ini 2>/dev/null)"
  if [ "$drop_ch" = "0" ]; then
    ok "Fail-safe default: [drop] servo_channel = 0 (disabled)"
  else
    warn "Fail-safe default: [drop] servo_channel = '$drop_ch' (expected 0 if drop disarmed)"
  fi
fi

# RTL-SDR dongle (only relevant if RF hunt enabled)
if command -v rtl_test >/dev/null 2>&1; then
  if rtl_test -t 2>&1 | grep -qiE "found.*device|tuner"; then
    ok "RTL-SDR dongle detected"
  else
    warn "rtl_test installed but no RTL-SDR dongle responded (skip if not using RF hunt)"
  fi
else
  warn "rtl_test not installed (skip if not using RF hunt; otherwise: sudo apt install rtl-sdr)"
fi

# Kismet service (only relevant if RF hunt enabled)
if command -v systemctl >/dev/null 2>&1; then
  kismet_state="$(systemctl is-active kismet 2>/dev/null || true)"
  case "$kismet_state" in
    active) ok "kismet service is active" ;;
    inactive|failed) warn "kismet service is $kismet_state (skip if not using RF hunt)" ;;
    *) warn "kismet service status: ${kismet_state:-unknown} (skip if not using RF hunt)" ;;
  esac
fi

# Disk space — fail under 1 GB on /, warn under 5 GB
root_free_kb="$(df --output=avail / 2>/dev/null | tail -1 | tr -d ' ')"
if [ -n "$root_free_kb" ]; then
  root_free_gb=$((root_free_kb / 1024 / 1024))
  if [ "$root_free_kb" -lt 1048576 ]; then
    fail "Root filesystem free: ${root_free_gb} GB (< 1 GB — clear space before deploy)"
  elif [ "$root_free_kb" -lt 5242880 ]; then
    warn "Root filesystem free: ${root_free_gb} GB (< 5 GB recommended)"
  else
    ok "Root filesystem free: ${root_free_gb} GB"
  fi
fi
if [ -d output_data ]; then
  out_free_kb="$(df --output=avail output_data 2>/dev/null | tail -1 | tr -d ' ')"
  if [ -n "$out_free_kb" ] && [ "$out_free_kb" -lt 1048576 ]; then
    warn "output_data/ filesystem free: $((out_free_kb / 1024)) MB (< 1 GB — logs may fill quickly)"
  fi
fi

# /api/health probe (only if service is already running and curl is available)
if command -v curl >/dev/null 2>&1; then
  health_code="$(curl --max-time 2 -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/health 2>/dev/null || true)"
  health_code="${health_code:-000}"
  case "$health_code" in
    200) ok "/api/health responding (200)" ;;
    000) ;; # service not running — silent (preflight is pre-deploy)
    *) warn "/api/health returned $health_code (service running but unhealthy?)" ;;
  esac
fi

echo
echo "Summary: PASS=$PASS WARN=$WARN FAIL=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
