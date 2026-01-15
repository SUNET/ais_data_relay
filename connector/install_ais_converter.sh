#!/bin/bash
set -e

# === CONFIG ===
SERVICE_USER="operator"
SERVICE_GROUP=$(id -gn "${SERVICE_USER}")  # automatically gets 'users'
PROJECT_DIR="/opt/ais_converter_env"
REPO_URL="https://github.com/SUNET/ais_data_relay.git"
REPO_NAME="ais_data_relay"
INTERVAL=60
SERVICE_NAME="ais_converter"
ENV_FILE="/etc/default/${SERVICE_NAME}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
AIS_OUTPUT_DIR="/var/lib/ais_converter"
AIS_LOG_DIR="/var/log/ais_converter"
UPDATE_SCRIPT="/usr/local/bin/update_ais_converter.sh"
LOGROTATE_FILE="/etc/logrotate.d/${SERVICE_NAME}"

# === 1. Environment Setup ===
echo "[+] Creating Python virtual environment..."
# mkdir -p /opt
mkdir -p "${AIS_OUTPUT_DIR}"
mkdir -p "${AIS_LOG_DIR}"
python3 -m venv "${PROJECT_DIR}"
sudo chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${AIS_OUTPUT_DIR}" "${AIS_LOG_DIR}"
sudo chmod 755 "${AIS_OUTPUT_DIR}" "${AIS_LOG_DIR}"

echo "[+] Activating virtual environment..."
source "${PROJECT_DIR}/bin/activate"

echo "[+] Cloning project repository..."
cd "${PROJECT_DIR}"
if [ ! -d "${PROJECT_DIR}/${REPO_NAME}" ]; then
    git clone "${REPO_URL}"
else
    echo "[+] Repository already exists, skipping clone."
fi

if "${PROJECT_DIR}/bin/python3" -c 'import sys; sys.exit(0) if sys.version_info >= (3,7) else sys.exit(1)'; then
    REQ_FILE="${PROJECT_DIR}/${REPO_NAME}/connector/requirements_py3.7.above.txt"
else
    REQ_FILE="${PROJECT_DIR}/${REPO_NAME}/connector/requirements.txt"
fi

echo "[+] Installing dependencies from $REQ_FILE ..."
"${PROJECT_DIR}/bin/python3" -m pip install -r "$REQ_FILE"

# === 2. Environment File ===
# Prompt for sensitive values
read -rp "Enter AIS_SERVER_HOST (127.0.0.1): " AIS_SERVER_HOST
read -rp "Enter AIS_SERVER_PORT (5000): " AIS_SERVER_PORT
read -rp "Enable ASN mode? (true/false): " IS_ASN

# Normalize IS_ASN
IS_ASN="${IS_ASN,,}"

if [[ "$IS_ASN" != "true" && "$IS_ASN" != "false" ]]; then
    echo "ERROR: IS_ASN must be true or false"
    exit 1
fi

echo "[+] Writing environment configuration to ${ENV_FILE}..."
cat > "${ENV_FILE}" <<EOF
AIS_OUTPUT_DIR=/var/lib/ais_converter
INTERVAL=60
AIS_SERVER_HOST=${AIS_SERVER_HOST}
AIS_SERVER_PORT=${AIS_SERVER_PORT}
IS_ASN=${IS_ASN}
ENVIRONMENT=production
EOF

# Restrict permissions
chmod 600 "${ENV_FILE}"
chown root:root "${ENV_FILE}"

ASN_FLAG=""
if [[ "$IS_ASN" == "false" ]]; then
    ASN_FLAG="--no-asn"
fi

# === 3. Systemd Service ===
echo "[+] Creating systemd service at ${SERVICE_FILE}..."
cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=AIS Converter Service
After=network.target

[Service]
User=${SERVICE_USER}
Group=users
Type=simple
WorkingDirectory=${PROJECT_DIR}/${REPO_NAME}/connector
EnvironmentFile=${ENV_FILE}
ExecStart=${PROJECT_DIR}/bin/python3 ${PROJECT_DIR}/${REPO_NAME}/connector/ais_converter.py --interval ${INTERVAL} --output ${AIS_OUTPUT_DIR}/ais_live_data.csv ${ASN_FLAG}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"

echo "[+] Service started. Checking status..."
systemctl status "${SERVICE_NAME}.service" --no-pager

# === 4. Optional Log Rotation ===
echo "[+] Creating logrotate config at ${LOGROTATE_FILE}..."
cat > "${LOGROTATE_FILE}" <<EOF
/var/log/${SERVICE_NAME}.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
EOF

echo "[+] If using log rotation, update service to:"
echo "StandardOutput=append:/var/log/${SERVICE_NAME}.log"
echo "StandardError=append:/var/log/${SERVICE_NAME}.log"
echo "Then run: systemctl daemon-reload && systemctl restart ${SERVICE_NAME}.service"

# === 5. Update Script ===
echo "[+] Creating update script at ${UPDATE_SCRIPT}..."
cat > "${UPDATE_SCRIPT}" <<EOF
#!/bin/bash
set -e
cd ${PROJECT_DIR}/${REPO_NAME}
git pull
${PROJECT_DIR}/bin/python3 -m pip install -r requirements.txt
systemctl restart ${SERVICE_NAME}.service
echo "AIS Converter updated and restarted."
EOF

chmod +x "${UPDATE_SCRIPT}"

echo "[✅] Installation complete!"
echo "View logs with: journalctl -u ${SERVICE_NAME}.service -f"
echo "Update later using: sudo ${UPDATE_SCRIPT}"
