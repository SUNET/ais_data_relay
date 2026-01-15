# Dockerfile
# FROM alpine:latest
FROM alpine:3.23.2

RUN apk add --no-cache stunnel

# Create expected runtime directories
RUN mkdir -p /etc/stunnel /etc/stunnel/certs /var/log/stunnel

# Run stunnel in foreground
CMD ["stunnel", "/etc/stunnel/stunnel.conf"]
