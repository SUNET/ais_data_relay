# TODO

- [x] Simplify relay to only do one thing, relay tcp ais message nothing else + db access + simple hhttp page
- [x] Test the new setup
- [x] Build docker images
- [x] Deploy to the server
- [x] open ports on safespring & test locally to connect, 5000, 443 
- [x] Update the ais-data code & ais-sn, clause
- [x] Finish the project by tomorrow
- [x] Copy stunnel docs to Desktop/Notes
- [x] Update the web doc & the readme doc
- [ ] install the new version on asn servers

openssl s_client -connect ais-data-relay.streams.sunet.se:5000 \
  -cert /opt/stunnel/client.crt \
  -key /opt/stunnel/client.key \
  -CAfile /opt/stunnel/ca.crt


openssl s_client -connect ais-data-relay.streams.sunet.se:5000 \
  -cert ./client.crt \
  -key ./client.key \
  -CAfile ./ca.crt
  