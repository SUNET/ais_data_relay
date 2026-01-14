#!/usr/bin/env bash
set -euo pipefail

# =========================
# Configuration
# =========================
STUNNEL_BIN="/usr/bin/stunnel"
STUNNEL_DIR="/opt/stunnel"
STUNNEL_CONF_FILE="${STUNNEL_DIR}/client-stunnel.conf"

SERVICE_NAME="stunnel-ais-client.service"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"

# =========================
# 0. Stunnel Config Setup
# =========================
echo "=== Setting up stunnel client configuration ==="

# Validate stunnel binary
if [[ ! -x "$STUNNEL_BIN" ]]; then
  echo "ERROR: stunnel binary not found at $STUNNEL_BIN"
  exit 1
fi

# Create stunnel directory
if [[ ! -d "$STUNNEL_DIR" ]]; then
  echo "Creating directory: $STUNNEL_DIR"
  sudo mkdir -p "$STUNNEL_DIR"
fi

# Create stunnel config (do not overwrite)
if [[ -f "$STUNNEL_CONF_FILE" ]]; then
  echo "ERROR: Config file already exists: $STUNNEL_CONF_FILE"
  echo "Refusing to overwrite existing configuration."
  exit 1
fi

sudo tee "$STUNNEL_CONF_FILE" > /dev/null <<'EOF'
client = yes
foreground = yes
debug = info

[secure-service-client]
accept = 127.0.0.1:5000
connect = ais-data-relay.streams.sunet.se:5000
checkHost = ais-data-relay.streams.sunet.se
cert = ./client.crt
key = ./client.key
CAfile = ./ca.crt
verifyChain = yes
verifyPeer = no
EOF

echo "Stunnel client configuration written to:"
echo "  $STUNNEL_CONF_FILE"

echo
echo "Next steps (required before starting service):"
echo "  1. Copy client.crt, client.key, and ca.crt into $STUNNEL_DIR"
echo "  2. Secure the private key:"
echo "     chmod 600 $STUNNEL_DIR/client.key"
echo

# =========================
# 1. Systemd Service Setup
# =========================
echo "=== Installing systemd service for stunnel ==="

if [[ -f "$SERVICE_FILE" ]]; then
  echo "Service already exists: $SERVICE_FILE"
else
  sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Stunnel Client for AIS Secure Relay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${STUNNEL_BIN} ${STUNNEL_CONF_FILE}
WorkingDirectory=${STUNNEL_DIR}
Restart=always
RestartSec=5

# Security hardening
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  echo "Systemd service created: $SERVICE_FILE"
fi

# Reload systemd
echo "Reloading systemd..."
sudo systemctl daemon-reload

# Enable service
echo "Enabling service: $SERVICE_NAME"
sudo systemctl enable "$SERVICE_NAME"

# =========================
# 2. Start Service (if certs exist)
# =========================
if [[ -f "$STUNNEL_DIR/client.crt" && -f "$STUNNEL_DIR/client.key" && -f "$STUNNEL_DIR/ca.crt" ]]; then
  echo "Certificates found. Starting stunnel service..."
  sudo systemctl restart "$SERVICE_NAME"
else
  echo "WARNING: Certificates not found yet."
  echo "Service is installed but NOT started."
  echo "Once certificates are in place, run:"
  echo "  sudo systemctl start $SERVICE_NAME"
fi

# =========================
# Final Status
# =========================
echo
echo "=== Setup complete ==="
echo "Service status:"
sudo systemctl status "$SERVICE_NAME" --no-pager || true

echo
echo "Logs:"
echo "  journalctl -u $SERVICE_NAME -f"
