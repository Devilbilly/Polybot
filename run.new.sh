#!/usr/bin/env bash
# run.new.sh — one-shot setup of the box's networking. Idempotent + reboot-proof.
#
# WHAT IT DOES (and why each piece matters):
#   * default route stays on ens4 (GCP)        -> bot keeps Polymarket + general internet
#   * 140.112.0.0/16 routed via ppp0 (the VPN) -> box can:  ssh -p 10073 billy@140.112.170.37
#   * `ip rule sport 22 -> ens4`               -> inbound SSH survives while the VPN is up
#     (the ssh-out target 140.112.170.37 is ALSO the inbound SSH source IP, so without this
#      the tunnel would swallow the reply packets and kill your session)
#   * installed as systemd unit `polybot-vpn`  -> auto-restores on reboot / crash
#
# THE BUG THAT COST DAYS: the FortiGate trusted-cert sha256 must be the FULL 64 hex chars
# ending ...457bccb9 — a one-char-short digest matches only intermittently.
#
# !! CONTAINS THE VPN PASSWORD — keep this file private; do NOT commit it to the public repo !!
#
# Run on the box:   bash run.new.sh
set -euo pipefail

GW=10.140.0.1          # GCP subnet gateway (NOT the box's own IP 10.140.0.10)

echo "[run.new] 1/4  writing VPN credentials -> /etc/openfortivpn/ntu.conf (0600)"
sudo mkdir -p /etc/openfortivpn
sudo tee /etc/openfortivpn/ntu.conf >/dev/null <<'CONF'
host = 140.112.20.243
port = 43443
username = phsu
password = irislab
trusted-cert = aabff38e7e975608a7dcced72a7425ca5ae358e80a2b6ed014fd6663457bccb9
set-routes = 0
set-dns = 0
CONF
sudo chmod 600 /etc/openfortivpn/ntu.conf

echo "[run.new] 2/4  writing systemd unit -> /etc/systemd/system/polybot-vpn.service"
sudo tee /etc/systemd/system/polybot-vpn.service >/dev/null <<'UNIT'
[Unit]
Description=NTU FortiGate VPN (split tunnel + sport-22 policy routing for SSH)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStartPre=/bin/bash -c 'ip route replace 140.112.20.243/32 via 10.140.0.1 dev ens4'
ExecStart=/usr/bin/openfortivpn -c /etc/openfortivpn/ntu.conf
ExecStartPost=/bin/bash -c 'for i in $(seq 1 25); do ip -4 addr show ppp0 2>/dev/null | grep -q inet && break; sleep 1; done; ip route replace 140.112.0.0/16 dev ppp0; ip route replace default via 10.140.0.1 dev ens4 table 200; ip rule list | grep -q "sport 22 lookup 200" || ip rule add sport 22 lookup 200 priority 100'
ExecStopPost=-/bin/bash -c 'ip rule del sport 22 lookup 200 priority 100 2>/dev/null; ip route del 140.112.0.0/16 dev ppp0 2>/dev/null'
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

echo "[run.new] 3/4  enabling + (re)starting service"
sudo systemctl daemon-reload
sudo systemctl enable polybot-vpn.service >/dev/null 2>&1 || true
sudo systemctl restart polybot-vpn.service

echo "[run.new] 4/4  verifying (waiting up to 25s for the tunnel) ..."
for i in $(seq 1 25); do ip -4 addr show ppp0 2>/dev/null | grep -q inet && break; sleep 1; done
set +e
echo "-----------------------------------------------------------"
printf "  service       : %s\n" "$(systemctl is-active polybot-vpn)"
if ip -4 addr show ppp0 2>/dev/null | grep -q inet; then
  printf "  ppp0          : UP  %s\n" "$(ip -4 addr show ppp0 | awk '/inet /{print $2}')"
else
  printf "  ppp0          : DOWN -> journalctl -u polybot-vpn -n 30\n"
fi
printf "  target route  : %s\n" "$(ip route get 140.112.170.37 2>/dev/null | head -1)"
printf "  sport-22 rule : %s\n" "$(ip rule list | grep 'sport 22' || echo MISSING)"
printf "  ssh-out :10073: "; timeout 6 bash -c 'cat </dev/null >/dev/tcp/140.112.170.37/10073' 2>/dev/null && echo OPEN || echo unreachable
printf "  bot internet  : "; curl -s -o /dev/null -w "gamma HTTP %{http_code}\n" -m8 "https://gamma-api.polymarket.com/events?slug=x" 2>/dev/null || echo "(curl failed)"
echo "-----------------------------------------------------------"
echo "[run.new] done. VPN auto-starts on boot; then:  ssh -p 10073 billy@140.112.170.37"
echo "[run.new] stop:  sudo systemctl stop polybot-vpn    logs:  journalctl -u polybot-vpn -f"
