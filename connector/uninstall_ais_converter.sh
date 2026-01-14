#!/bin/bash
set -e

# === CONFIG ===
PROJECT_DIR="/opt/ais_converter_env"
SERVICE_NAME="ais_converter"
ENV_FILE="/etc/default/${SERVICE_NAME}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
UPDATE_SCRIPT="/usr/local/bin/update_ais_converter.sh"
LOGROTATE_FILE="/etc/logrotate.d/${SERVICE_NAME}"
AIS_OUTPUT_DIR="/var/lib/ais_converter"
AIS_LOG_DIR="/var/log/ais_converter"

echo "[!] Stopping service if running..."
if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
    sudo systemctl stop "${SERVICE_NAME}.service"
fi

echo "[!] Disabling service..."
if systemctl is-enabled --quiet "${SERVICE_NAME}.service"; then
    sudo systemctl disable "${SERVICE_NAME}.service"
fi

echo "[!] Removing systemd service file..."
sudo rm -f "${SERVICE_FILE}"
sudo systemctl daemon-reload

echo "[!] Removing environment file..."
sudo rm -f "${ENV_FILE}"

echo "[!] Removing logrotate config..."
sudo rm -f "${LOGROTATE_FILE}"

echo "[!] Removing update script..."
sudo rm -f "${UPDATE_SCRIPT}"

echo "[!] Removing Python virtual environment and project files..."
sudo rm -rf "${PROJECT_DIR}"

echo "[!] Removing AIS output directory..."
sudo rm -rf "${AIS_OUTPUT_DIR}"

echo "[!] Removing AIS output log directory..."
sudo rm -rf "${AIS_LOG_DIR}"

echo "[✅] AIS Converter uninstalled completely!"
