#!/bin/bash
### **Step 2a: Create a Certificate Authority (CA)**
# Create private key for CA
sudo openssl genrsa -out ca.key 4096

# Create self-signed CA certificate
sudo openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -out ca.crt \
  -subj "/C=US/ST=State/L=City/O=Org/OU=IT/CN=MyRootCA"


### **Step 2b: Generate Server Certificate**
# Server private key
sudo openssl genrsa -out server.key 4096

# Server certificate signing request (CSR)
sudo openssl req -new -key server.key -out server.csr \
  -subj "/C=US/ST=State/L=City/O=Org/OU=IT/CN=ais-data-relay.das.sunet.se"

# Sign server certificate with CA
sudo openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 365 -sha256


### **Step 2c: Generate Client Certificate**

# Client private key
sudo openssl genrsa -out client.key 4096

# Client CSR
sudo openssl req -new -key client.key -out client.csr \
  -subj "/C=US/ST=State/L=City/O=Org/OU=IT/CN=client.local"

# Sign client certificate with CA
sudo openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out client.crt -days 365 -sha256

