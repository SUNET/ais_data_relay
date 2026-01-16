#!/bin/bash
# Usage: ./generate_clients.sh client1 client2 client3

CA_CERT="ca.crt"
CA_KEY="ca.key"
DAYS=365

for CLIENT in "$@"; do
  echo "Generating certificate for $CLIENT..."

  mkdir -p clients/$CLIENT
  cd clients/$CLIENT || exit

  # 1. Generate private key
  openssl genrsa -out client.key 4096

  # 2. Generate CSR
  openssl req -new -key client.key -out client.csr \
    -subj "/C=US/ST=State/L=City/O=Org/OU=IT/CN=$CLIENT"

  # 3. Sign client certificate with CA
  openssl x509 -req -in client.csr -CA ../../$CA_CERT -CAkey ../../$CA_KEY -CAcreateserial \
    -out client.crt -days $DAYS -sha256

  cd ../..
done
