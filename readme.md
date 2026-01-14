# AIS Relay Processor

This document describes how to run and interact with the **AIS Relay Processor**, which securely receives AIS data via a TLS-encrypted upstream connection and exposes it locally via a TCP relay and HTTP API.

## 1. Running the Service with Docker

The AIS Relay Processor can be started using Docker Compose.

### Standard startup

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d
```

### Startup with explicit environment file

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.test.yml \
  --env-file .env.test \
  up -d
```

## 2. Server Information

Upstream AIS relay server:

```text
Hostname: shared-dco-prod-aisdatarelay-1.streams.sunet.se
```

The upstream connection is TLS-encrypted and accessed via **stunnel**.

## 3. Dependencies

Required Python packages:

```text
fastapi==0.125.0
uvicorn==0.38.0
websockets==15.0.1
pyais==2.14.0
python-dotenv==1.2.1
```

### Using `pipenv`

```bash
pipenv install \
  fastapi==0.125.0 \
  uvicorn==0.38.0 \
  websockets==15.0.1 \
  pyais==2.14.0 \
  python-dotenv==1.2.1
```

### Using `pip`

```bash
pip install \
  fastapi==0.125.0 \
  uvicorn==0.38.0 \
  websockets==15.0.1 \
  pyais==2.14.0 \
  python-dotenv==1.2.1
```

## 4. Running the Server (Non-Docker)

Start the FastAPI server:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

The TCP relay listener is started automatically by the application.

## 5. Secure TCP Relay (AIS Data Stream)

### Overview

The AIS Relay Processor does **not** expose the upstream AIS feed directly.
Instead:

1. The upstream AIS source requires a TLS-encrypted connection.
2. **stunnel** is used to terminate TLS locally.
3. The application connects to a **local TCP socket** exposed by stunnel.

This design avoids embedding TLS logic inside the application and allows multiple consumers to safely reuse the same AIS feed.

### Stunnel Client Setup

Stunnel must be configured and running before starting the AIS Relay Processor.

Example stunnel configuration (`client-stunnel.conf`):

```ini
client = yes
foreground = yes
debug = info

[secure-ais-client]
accept = 127.0.0.1:5000
connect = shared-dco-prod-aisdatarelay-1.streams.sunet.se:5000
checkHost = shared-dco-prod-aisdatarelay-1.streams.sunet.se
cert = ./client.crt
key = ./client.key
CAfile = ./ca.crt
verifyChain = yes
verifyPeer = no
```

Required files:

* `client.crt` – client certificate
* `client.key` – private key
* `ca.crt` – trusted CA certificate

Start stunnel:

```bash
stunnel client-stunnel.conf
```

Once running, the secure AIS feed is available locally at:

```text
127.0.0.1:5000
```

### Connecting to the TCP Relay

The TCP relay provides **raw AIS NMEA sentences** over a plain TCP socket.

No username/password authentication is required.
Access control is handled entirely by TLS and certificates at the stunnel layer.

Example using `nc`:

```bash
nc localhost 5000
```

Once connected, AIS messages will begin streaming immediately:

```text
!AIVDM,1,1,,A,15N?;P001o;...
!AIVDM,1,1,,B,33aG?o5000...
```

## 6. Database

### Live Database Location

The AIS Relay Processor maintains a live SQLite database:

```text
database/ais_database.db
```

### Database Snapshot API

A snapshot of the current database can be downloaded via HTTP:

```http
GET http://localhost:8000/db/snapshot
```

Authentication is required for this endpoint (HTTP Basic Auth or as configured).

## 7. Local / Development Certificate Setup (mTLS Test)

This section describes how to generate **self-signed certificates** for testing **mutual TLS (mTLS)** in local or development environments.

In an mTLS setup:

* A **Certificate Authority (CA)** signs all certificates
* The **server** presents a certificate to the client
* The **client** presents a certificate to the server
* Both sides verify trust using the same CA

> ⚠️ These certificates are intended for **testing only**.
> Do not use self-signed certificates in production environments.

## Step 1: Generate a Local Certificate Authority (CA)

The CA is used to sign both server and client certificates.

```bash
# Generate private key for the CA
openssl genrsa -out ca.key 4096

# Generate self-signed CA certificate
openssl req -x509 -new -nodes \
  -key ca.key \
  -sha256 \
  -days 3650 \
  -out ca.crt \
  -subj "/C=US/ST=State/L=City/O=Org/OU=IT/CN=MyRootCA"
```

**Output**

* `ca.key` – CA private key (keep secure)
* `ca.crt` – CA public certificate (distributed to clients/servers)

## Step 2: Generate Server Certificate

The server certificate identifies the AIS relay endpoint.

```bash
# Generate server private key
openssl genrsa -out server.key 4096

# Create server certificate signing request (CSR)
openssl req -new \
  -key server.key \
  -out server.csr \
  -subj "/C=US/ST=State/L=City/O=Org/OU=IT/CN=ais-data-relay.streams.sunet.se"

# Sign server certificate using the CA
openssl x509 -req \
  -in server.csr \
  -CA ca.crt \
  -CAkey ca.key \
  -CAcreateserial \
  -out server.crt \
  -days 365 \
  -sha256
```

**Output**

* `server.key` – server private key
* `server.crt` – server certificate (signed by CA)

> ✅ The `CN` should match the server hostname used by clients.

## Step 3: Generate Client Certificate

Client certificates are used by consumers (e.g. stunnel clients) to authenticate to the server.

```bash
# Generate client private key
openssl genrsa -out client.key 4096

# Create client CSR
openssl req -new \
  -key client.key \
  -out client.csr \
  -subj "/C=US/ST=State/L=City/O=Org/OU=IT/CN=client.local"

# Sign client certificate using the CA
openssl x509 -req \
  -in client.csr \
  -CA ca.crt \
  -CAkey ca.key \
  -CAcreateserial \
  -out client.crt \
  -days 365 \
  -sha256
```

**Output**

* `client.key` – client private key
* `client.crt` – client certificate (signed by CA)

## Certificate Summary

After completing the steps above, you should have:

| File         | Description            |
|  | - |
| `ca.crt`     | Trusted CA certificate |
| `ca.key`     | CA private key         |
| `server.crt` | Server certificate     |
| `server.key` | Server private key     |
| `client.crt` | Client certificate     |
| `client.key` | Client private key     |

## Step 4: Generate Multiple Client Certificates (Optional)

The script below can be used to generate certificates for multiple clients using the same CA.

### Example Usage

```bash
./generate_clients.sh client1 client2 client3
```

### Script: `generate_clients.sh`

```bash
#!/bin/bash
# Usage: ./generate_clients.sh client1 client2 client3

CA_CERT="ca.crt"
CA_KEY="ca.key"
DAYS=365

for CLIENT in "$@"; do
  echo "Generating certificate for $CLIENT..."

  mkdir -p clients/$CLIENT
  cd clients/$CLIENT || exit 1

  # Generate client private key
  openssl genrsa -out client.key 4096

  # Generate CSR
  openssl req -new \
    -key client.key \
    -out client.csr \
    -subj "/C=US/ST=State/L=City/O=Org/OU=IT/CN=$CLIENT"

  # Sign certificate using CA
  openssl x509 -req \
    -in client.csr \
    -CA ../../$CA_CERT \
    -CAkey ../../$CA_KEY \
    -CAcreateserial \
    -out client.crt \
    -days $DAYS \
    -sha256

  cd ../..
done
```

Each client will be created under:

```text
clients/<client-name>/
```
