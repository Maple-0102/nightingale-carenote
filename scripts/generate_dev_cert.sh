#!/bin/sh
set -eu
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 7 \
  -keyout certs/key.pem \
  -out certs/cert.pem \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
chmod 600 certs/key.pem
echo "Development certificate created in certs/. It is intentionally not committed."
