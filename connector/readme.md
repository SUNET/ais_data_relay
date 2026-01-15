# AIS Converter Service

Description of **AIS Converter Service**, including environment setup, secure AIS relay connectivity, systemd integration, logging, and updates.

## 1. Environment Setup

### 1.1 Create Virtual Environment and Install Code

```bash
cd /opt
python3 -m venv ais_converter
source ais_converter/bin/activate

cd ais_converter
git clone https://github.com/SUNET/ais_data_relay.git
```

Install Python dependencies:

For python >= 3.7

```bash
/opt/ais_converter/bin/python3 -m pip install -r ais_data_relay/connector/requirements_py3.7.above.txt
```

else:

```bash
/opt/ais_converter/bin/python3 -m pip install -r ais_data_relay/connector/requirements.txt
```

## 2. Secure AIS Relay Connection (Stunnel)

The AIS relay requires a **TLS-encrypted connection**. This is implemented using **stunnel**, which acts as a local TCP proxy.
Stunnel wraps the AIS TCP connection in TLS and exposes a **secure local endpoint (localhost)** for the AIS relay to connect to.

To simplify setup, a helper script is provided that installs and configures stunnel as a **system service**.

### Option A: Automated Setup (Recommended on servers)

Use `setup_stunnel_ais_client.sh` to automatically install and configure the stunnel client.

#### 1. Install stunnel

**Debian / Ubuntu**

```bash
sudo apt update
sudo apt install -y stunnel4
```

**openSUSE**

```bash
sudo zypper install -y stunnel
```

#### 2. Run the stunnel client setup script

```bash
chmod +x ais_data_relay/connector/setup_stunnel_ais_client.sh
./ais_data_relay/connector/setup_stunnel_ais_client.sh
```

perfomr test:

```bash
openssl s_client -connect ais-data-relay.streams.sunet.se:5000 \
  -cert /opt/stunnel/client.crt \
  -key /opt/stunnel/client.key \
  -CAfile /opt/stunnel/ca.crt
```

```bash
nc locahost 5000
```

you should see:

```bash
!ABVDM,1,1,3,B,339K?R301fPls9hP71Hi9@wD210P,0*16
$ABVSI,Helsingborg,3,093142,1590,-105,4*1B
!ABVDM,1,1,9,B,15?dMl000q0r8j`Oi>3WHUmD059t,0*58
$ABVSI,Trelleborg,9,093142,1592,-81,24*68
!ABVDM,1,1,5,A,B3uNdF0000DjQD`O`@803wk5kP06,0*6B
...
```

Make sure to add certificates.

### Option B: Manual Setup (Advanced / Personal Machines)

If you prefer a manual configuration or are running on a personal workstation, follow the steps below.

### 2.1 Create the Stunnel Directory

Create a dedicated directory for stunnel configuration and assets:

```bash
sudo mkdir -p /opt/stunnel
cd /opt/stunnel
```

> **Note:** `/opt/stunnel` is used to keep the setup isolated and portable. You may choose another location if required by your environment.

### 2.2 Create Stunnel Configuration

Create the file `client-stunnel.conf`:

```ini
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
```

### 2.3 Add Certificates

Place the following files inside `/opt/stunnel`:

* `client.crt`
* `client.key`
* `ca.crt`

Ensure correct permissions:

```bash
chmod 600 client.key
```

### 2.4 Start Stunnel

```bash
sudo stunnel /opt/stunnel/client-stunnel.conf
```

Stunnel will now expose the secure AIS stream locally on:

```
127.0.0.1:5000
```

## 3 Ais connector client

On a server setup, you simply use, `install_ais_converter.sh` to install a client ais service or follow the follwoing manul seetup steps.

### 3.1 Environment Variables

Create an environment configuration file:

**`/etc/default/ais_converter`**

```env
INTERVAL=60

# AIS source (via stunnel)
AIS_SERVER_HOST=127.0.0.1
AIS_SERVER_PORT=5000
ENVIRONMENT=production|development
```

### 3.1 Systemd Service Configuration

To run it directly:

```bash
python ais_converter.py --interval 60 --output ais_live_data.csv --no-asn
```

#### 3.1.1 Create Systemd Unit File

Create:

**`/etc/systemd/system/ais_converter.service`**

```ini
[Unit]
Description=AIS Converter Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/ais_converter_env/ais_converter
EnvironmentFile=/etc/default/ais_converter
ExecStart=/opt/ais_converter_env/bin/python3 /opt/ais_converter_env/ais_converter/ais_converter.py --interval \${INTERVAL}ExecStart=/opt/ais_converter_env/bin/python3 /opt/ais_converter_env/ais_converter/ais_converter.py --interval \${INTERVAL} --output ${AIS_OUTPUT_DIR}/ais_live_data.csv
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
```

#### 3.1.2 Enable and Start Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable ais_converter.service
sudo systemctl start ais_converter.service
```

Check status:

```bash
sudo systemctl status ais_converter.service
```

View logs:

```bash
journalctl -u ais_converter.service -f
```

### 3.2 Log Rotation (Optional but Recommended)

If you prefer file-based logs instead of journald.

#### 3.2.1 Logrotate Configuration

Create:

**`/etc/logrotate.d/ais_converter`**

```conf
/var/log/ais_converter.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
```

#### 3.2.2 Update Systemd Service for File Logging

Modify the service file:

```ini
StandardOutput=append:/var/log/ais_converter.log
StandardError=append:/var/log/ais_converter.log
```

Apply changes:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ais_converter.service
```

### 3.3 Update / Deployment Script

A simple script to pull updates, reinstall dependencies, and restart the service.

#### 3.3.1 Create Update Script

**`/usr/local/bin/update_ais_converter.sh`**

```bash
#!/bin/bash
set -e

cd /opt/ais_converter/ais_convert_minimal
git pull

/opt/ais_converter/bin/python3 -m pip install -r requirements.txt

sudo systemctl restart ais_converter.service
echo "AIS Converter updated and restarted successfully."
```

Make executable:

```bash
sudo chmod +x /usr/local/bin/update_ais_converter.sh
```

#### 3.3.2 Usage

```bash
sudo /usr/local/bin/update_ais_converter.sh
```
