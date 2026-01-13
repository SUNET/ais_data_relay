# Dockerfile
FROM alpine:latest

RUN apk add --no-cache stunnel

# Create expected runtime directories
RUN mkdir -p /etc/stunnel /etc/stunnel/certs /var/log/stunnel

# Run stunnel in foreground
CMD ["stunnel", "/etc/stunnel/stunnel.conf"]
