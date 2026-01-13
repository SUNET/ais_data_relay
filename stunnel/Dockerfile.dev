# Dockerfile
FROM alpine:latest

RUN apk add --no-cache stunnel

# Copy stunnel config and certs
COPY stunnel-server.conf /etc/stunnel/stunnel.conf
COPY certs/ /etc/stunnel/certs/

# Create log folder
RUN mkdir -p /var/log/stunnel

# Run stunnel in foreground
CMD ["stunnel", "/etc/stunnel/stunnel.conf"]
