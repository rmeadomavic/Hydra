#!/usr/bin/env bash
set -euo pipefail

PASS=0
WARN=0
FAIL=0

ok()   { echo "[PASS] $1"; PASS=$((PASS+1)); }
warn() { echo "[WARN] $1"; WARN=$((WARN+1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

usage() {
  cat <<'EOF'
Usage: setup_headless.sh [OPTIONS]

Configure a Jetson for headless field-boot mode. After running this script,
the Jetson will boot to a running Hydra Detect dashboard with zero operator
interaction — just plug in power.

What this script does:
  1. Installs and configures Avahi (mDNS) so the Jetson is discoverable
  2. Optionally persists a WiFi network via NetworkManager
  3. Enables the hydra-detect systemd service to start on boot
  4. Runs a preflight self-test to verify the full boot chain

Options:
  --hostname NAME    mDNS hostname (default: hydra → reachable at hydra.local)
  --ssid SSID        WiFi network name to persist (optional)
  --password PASS    WiFi password (required if --ssid is given)
  --ethernet-only    Skip WiFi setup (Ethernet will be used)
  --no-enable        Do not enable hydra-detect service (just set up mDNS/WiFi)
  -h, --help         Show this help message

Examples:
  # Full setup with WiFi
  sudo bash scripts/setup_headless.sh --ssid "ClassroomWiFi" --password "s3cret"

  # Ethernet-only setup
  sudo bash scripts/setup_headless.sh --ethernet-only

  # Custom mDNS hostname (reachable at jetson-03.local)
  sudo bash scripts/setup_headless.sh --hostname jetson-03 --ssid "FieldNet" --password "pw123"

  # Re-run to verify everything is still configured
  sudo bash scripts/setup_headless.sh --ethernet-only
EOF
  exit 0
}

# ── Defaults ──────────────────────────────────────────────────────────
MDNS_HOSTNAME="hydra"
WIFI_SSID=""
WIFI_PASSWORD=""
ETHERNET_ONLY=false
ENABLE_SERVICE=true

# ── Parse arguments ───────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --hostname)      MDNS_HOSTNAME="$2"; shift 2 ;;
    --ssid)          WIFI_SSID="$2"; shift 2 ;;
    --password)      WIFI_PASSWORD="$2"; shift 2 ;;
    --ethernet-only) ETHERNET_ONLY=true; shift ;;
    --no-enable)     ENABLE_SERVICE=false; shift ;;
    -h|--help)       usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

# ── Validate arguments ───────────────────────────────────────────────
if [ "$ETHERNET_ONLY" = false ] && [ -z "$WIFI_SSID" ]; then
  echo "Error: Provide --ssid and --password for WiFi, or use --ethernet-only."
  echo "Run with -h for usage."
  exit 1
fi

if [ -n "$WIFI_SSID" ] && [ -z "$WIFI_PASSWORD" ]; then
  echo "Error: --password is required when --ssid is given."
  exit 1
fi

# ── Root check ────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  echo "This script must be run as root (sudo)."
  exit 1
fi

echo "Hydra Detect — Headless Field-Boot Setup"
echo "========================================="

# ── Step 1: Install and configure Avahi (mDNS) ───────────────────────
echo
echo "Step 1: Setting up mDNS (Avahi)..."

if ! command -v avahi-daemon >/dev/null 2>&1; then
  echo "  Installing avahi-daemon..."
  apt-get update -qq && apt-get install -y -qq avahi-daemon avahi-utils
fi

if command -v avahi-daemon >/dev/null 2>&1; then
  ok "avahi-daemon is installed"
else
  fail "avahi-daemon installation failed"
  echo "Summary: PASS=$PASS WARN=$WARN FAIL=$FAIL"
  exit 1
fi

# Set the mDNS hostname
hostnamectl set-hostname "$MDNS_HOSTNAME" 2>/dev/null || true

# Configure Avahi
AVAHI_CONF="/etc/avahi/avahi-daemon.conf"
if [ -f "$AVAHI_CONF" ]; then
  # Ensure host-name is set
  if grep -q "^host-name=" "$AVAHI_CONF"; then
    sed -i "s/^host-name=.*/host-name=$MDNS_HOSTNAME/" "$AVAHI_CONF"
  elif grep -q "^\[server\]" "$AVAHI_CONF"; then
    sed -i "/^\[server\]/a host-name=$MDNS_HOSTNAME" "$AVAHI_CONF"
  fi

  # Enable publishing
  if grep -q "^publish-addresses=" "$AVAHI_CONF"; then
    sed -i "s/^publish-addresses=.*/publish-addresses=yes/" "$AVAHI_CONF"
  fi
fi

# Publish the Hydra web dashboard as an mDNS service
mkdir -p /etc/avahi/services
cat > /etc/avahi/services/hydra-detect.service <<AVAHI_SVC
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Hydra Detect on %h</name>
  <service>
    <type>_http._tcp</type>
    <port>8080</port>
    <txt-record>path=/</txt-record>
  </service>
</service-group>
AVAHI_SVC
ok "mDNS service file created for Hydra dashboard"

systemctl enable --now avahi-daemon 2>/dev/null || true
systemctl restart avahi-daemon 2>/dev/null || true

if systemctl is-active --quiet avahi-daemon; then
  ok "avahi-daemon is running (hostname: $MDNS_HOSTNAME.local)"
else
  fail "avahi-daemon failed to start"
fi

# ── Step 2: WiFi network persistence ─────────────────────────────────
echo
echo "Step 2: Network configuration..."

if [ "$ETHERNET_ONLY" = true ]; then
  ok "Ethernet-only mode — skipping WiFi setup"
else
  if command -v nmcli >/dev/null 2>&1; then
    # Check if this connection already exists
    if nmcli connection show "$WIFI_SSID" >/dev/null 2>&1; then
      echo "  WiFi connection '$WIFI_SSID' already exists, updating..."
      nmcli connection modify "$WIFI_SSID" \
        wifi-sec.key-mgmt wpa-psk \
        wifi-sec.psk "$WIFI_PASSWORD" \
        connection.autoconnect yes \
        connection.autoconnect-priority 100
      ok "WiFi connection '$WIFI_SSID' updated"
    else
      echo "  Creating WiFi connection '$WIFI_SSID'..."
      nmcli connection add \
        type wifi \
        con-name "$WIFI_SSID" \
        ssid "$WIFI_SSID" \
        wifi-sec.key-mgmt wpa-psk \
        wifi-sec.psk "$WIFI_PASSWORD" \
        connection.autoconnect yes \
        connection.autoconnect-priority 100
      ok "WiFi connection '$WIFI_SSID' created"
    fi

    # Try to activate the connection now
    nmcli connection up "$WIFI_SSID" 2>/dev/null && \
      ok "WiFi connected to '$WIFI_SSID'" || \
      warn "Could not connect to '$WIFI_SSID' now (will auto-connect on boot if in range)"
  else
    fail "NetworkManager (nmcli) not found — cannot persist WiFi"
  fi
fi

# ── Step 3: Enable hydra-detect service ───────────────────────────────
echo
echo "Step 3: Hydra Detect service..."

if [ "$ENABLE_SERVICE" = true ]; then
  if [ -f /etc/systemd/system/hydra-detect.service ]; then
    ok "hydra-detect.service is already installed"
  elif [ -f /home/sorcc/Hydra/scripts/hydra-detect.service ]; then
    cp /home/sorcc/Hydra/scripts/hydra-detect.service /etc/systemd/system/
    systemctl daemon-reload
    ok "hydra-detect.service installed from repo"
  else
    fail "hydra-detect.service not found — install it first (see docs/setup/jetson-docker)"
  fi

  systemctl enable hydra-detect 2>/dev/null || true

  if systemctl is-enabled --quiet hydra-detect 2>/dev/null; then
    ok "hydra-detect is enabled (will start on boot)"
  else
    fail "hydra-detect could not be enabled"
  fi
else
  ok "Skipping service enablement (--no-enable)"
fi

# ── Step 4: Docker auto-start ─────────────────────────────────────────
echo
echo "Step 4: Docker daemon..."

systemctl enable docker 2>/dev/null || true

if systemctl is-enabled --quiet docker 2>/dev/null; then
  ok "Docker is enabled on boot"
else
  warn "Docker is not enabled on boot — run: sudo systemctl enable docker"
fi

# ── Step 5: Preflight self-test ───────────────────────────────────────
echo
echo "Step 5: Preflight self-test (verifying headless boot chain)..."

# Test 1: Auto-login check
if [ -d /etc/gdm3 ]; then
  GDM_CONF="/etc/gdm3/custom.conf"
  if [ -f "$GDM_CONF" ] && grep -q "AutomaticLoginEnable\s*=\s*true" "$GDM_CONF" 2>/dev/null; then
    ok "GDM auto-login is enabled"
  elif [ -f "$GDM_CONF" ] && grep -q "AutomaticLoginEnable\s*=\s*True" "$GDM_CONF" 2>/dev/null; then
    ok "GDM auto-login is enabled"
  else
    warn "GDM auto-login may not be enabled (headless boot works without it for services)"
  fi
else
  ok "No GDM installed (headless-friendly — services start without desktop login)"
fi

# Test 2: Network connectivity
if ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1; then
  ok "Network connectivity verified (internet reachable)"
else
  warn "No internet connectivity right now (WiFi may connect on next boot)"
fi

# Test 3: mDNS resolution of self
if command -v avahi-resolve >/dev/null 2>&1; then
  SELF_IP="$(avahi-resolve -4 -n "$MDNS_HOSTNAME.local" 2>/dev/null | awk '{print $2}' || true)"
  if [ -n "$SELF_IP" ]; then
    ok "mDNS self-resolution works ($MDNS_HOSTNAME.local → $SELF_IP)"
  else
    warn "mDNS self-resolution failed (avahi may need a moment to start)"
  fi
else
  warn "avahi-resolve not available for self-test"
fi

# Test 4: Docker image exists
if docker image inspect hydra-detect:latest >/dev/null 2>&1; then
  ok "hydra-detect:latest Docker image exists"
else
  warn "hydra-detect:latest image not found — build it first (see docs/setup/jetson-docker)"
fi

# Test 5: Service chain summary
echo
echo "  Boot chain:"
echo "    1. Power on → systemd starts"
if [ "$ETHERNET_ONLY" = false ]; then
  echo "    2. NetworkManager → connects to '$WIFI_SSID'"
else
  echo "    2. NetworkManager → connects via Ethernet"
fi
echo "    3. Docker daemon starts"
echo "    4. hydra-detect.service → starts Hydra in Docker"
echo "    5. avahi-daemon → advertises $MDNS_HOSTNAME.local"
echo "    6. GCS browser → http://$MDNS_HOSTNAME.local:8080"

echo
echo "Summary: PASS=$PASS WARN=$WARN FAIL=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
echo
echo "Headless mode is configured. On next power-on, open a browser to:"
echo
echo "    http://$MDNS_HOSTNAME.local:8080"
echo
