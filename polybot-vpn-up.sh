#!/bin/bash
# Polybot NTU VPN via openconnect (TUN-based Fortinet client). The HK box runs GCP's cloud kernel
# which ships no ppp_generic module, so openfortivpn (needs PPP) can't run — openconnect uses /dev/net/tun.
# Pins the gateway route, then execs openconnect with the password (from ntu.conf) on stdin.
ip route replace 140.112.20.243/32 via 10.170.0.1 dev ens4
PW=$(grep "^password" /etc/openfortivpn/ntu.conf | sed -E "s/^password[[:space:]]*=[[:space:]]*//")
exec /usr/sbin/openconnect --protocol=fortinet --user=phsu --passwd-on-stdin \
  --servercert=pin-sha256:gPlsl91jg8jPUZcPDP4H1vSQbzHvFF+jD6CsNFb1KoQ= \
  --script=/usr/local/bin/polybot-vpn-oc-script.sh 140.112.20.243:43443 <<< "$PW"
