#!/bin/bash
set -e

# === CONFIG - must match the install script ===
SERVICE_NAME="ais_converter"
PROJECT_DIR="/opt/ais_converter_env"
ENV_FILE="/etc/default/${SERVICE_NAME}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
AIS_OUTPUT_DIR="/var/lib/ais_converter"
AIS_LOG_DIR="/var/log/ais_converter"
UPDATE_SCRIPT="/usr/local/bin/update_ais_converter.sh"
LOGROTATE_FILE="/etc/logrotate.d/${SERVICE_NAME}"

echo "============================================================="
echo "          AIS Converter Uninstall Script"
echo "============================================================="
echo
echo "This script will:"
echo "  • Stop and disable the systemd service"
echo "  • Remove the systemd service file"
echo "  • Delete the virtual environment (${PROJECT_DIR})"
echo "  • Remove the git repository clone"
echo "  • Delete output & log directories (${AIS_OUTPUT_DIR}, ${AIS_LOG_DIR})"
echo "  • Remove environment file, update script and logrotate config"
echo
read -p "Are you sure you want to completely remove AIS Converter? [y/N] " confirm

if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Uninstall cancelled."
    exit 0
fi

echo

# 1. Stop and disable service
if systemctl is-active --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
    echo "[+] Stopping ${SERVICE_NAME} service..."
    systemctl stop "${SERVICE_NAME}.service"
fi

if systemctl is-enabled --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
    echo "[+] Disabling ${SERVICE_NAME} service..."
    systemctl disable "${SERVICE_NAME}.service" >/dev/null 2>&1
fi

# 2. Remove systemd service file
if [ -f "${SERVICE_FILE}" ]; then
    echo "[+] Removing systemd service file..."
    rm -f "${SERVICE_FILE}"
    systemctl daemon-reload
    systemctl reset-failed "${SERVICE_NAME}.service" 2>/dev/null || true
fi

# 3. Remove logrotate configuration
if [ -f "${LOGROTATE_FILE}" ]; then
    echo "[+] Removing logrotate configuration..."
    rm -f "${LOGROTATE_FILE}"
fi

# 4. Remove update script
if [ -f "${UPDATE_SCRIPT}" ]; then
    echo "[+] Removing update script..."
    rm -f "${UPDATE_SCRIPT}"
fi

# 5. Remove environment file
if [ -f "${ENV_FILE}" ]; then
    echo "[+] Removing environment file..."
    rm -f "${ENV_FILE}"
fi

# 6. Remove project directory (venv + repo)
if [ -d "${PROJECT_DIR}" ]; then
    echo "[+] Removing virtual environment and repository (${PROJECT_DIR})..."
    rm -rf "${PROJECT_DIR}"
fi

# 7. Remove data and log directories
#    (only if they are empty - safety measure)
for dir in "${AIS_OUTPUT_DIR}" "${AIS_LOG_DIR}"; do
    if [ -d "$dir" ]; then
        if [ -z "$(ls -A "$dir")" ]; then
            echo "[+] Removing empty directory: $dir"
            rmdir "$dir" 2>/dev/null || true
        else
            echo "[!] Directory $dir is NOT empty - keeping it!"
            echo "    → You may want to remove it manually:  sudo rm -rf \"$dir\""
        fi
    fi
done

echo
echo "============================================================="
echo "             Uninstall completed"
echo "============================================================="
echo
echo "Removed:"
echo "  • Systemd service"
echo "  • Virtual environment & git repo"
echo "  • Environment file, update script, logrotate config"
echo
echo "Kept (if not empty):"
echo "  • ${AIS_OUTPUT_DIR}  (output files)"
echo "  • ${AIS_LOG_DIR}     (logs)"
echo
echo "You can now safely remove them manually if desired:"
echo "  sudo rm -rf ${AIS_OUTPUT_DIR}"
echo "  sudo rm -rf ${AIS_LOG_DIR}"
echo
echo "Done."
echo