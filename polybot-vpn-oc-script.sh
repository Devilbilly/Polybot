#!/bin/sh
# openconnect vpnc-script: SPLIT tunnel for the NTU FortiGate on the HK box.
# Only 140.112.0.0/16 goes via the tunnel; the default route stays on ens4 (no full tunnel).
# Inbound SSH is protected: replies from source-port 22 are forced out ens4 via table 200,
# so an admin SSHing from a 140.112.x source is not black-holed into the tunnel.
GW=10.170.0.1
case "$reason" in
  connect|reconnect)
    ip link set dev "$TUNDEV" up mtu "${INTERNAL_IP4_MTU:-1400}"
    [ -n "$INTERNAL_IP4_ADDRESS" ] && ip addr replace "$INTERNAL_IP4_ADDRESS/32" dev "$TUNDEV"
    ip route replace 140.112.0.0/16 dev "$TUNDEV"
    ip route replace default via "$GW" dev ens4 table 200
    ip rule list | grep -q "sport 22 lookup 200" || ip rule add sport 22 lookup 200 priority 100
    ;;
  disconnect)
    ip rule del sport 22 lookup 200 priority 100 2>/dev/null
    ip route del 140.112.0.0/16 dev "$TUNDEV" 2>/dev/null
    ;;
esac
exit 0
