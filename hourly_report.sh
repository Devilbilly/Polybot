#!/usr/bin/env bash
# Unified hourly Polybot report (ONE style) from the HK box + archive each report with a timestamp.
# Account headline = ground truth (cash + Polymarket positions vs deposit). Read-only.
set -e
cd /nfs/home/billy/test_poly_v2
B="palacedeforsaken@34.92.235.71"
O="-i $HOME/.ssh/id_rsa -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15"
ssh $O "$B" 'python3 ~/Polybot/unified_report.py'   > polybot_report.html
mkdir -p archive/reports
TS=$(date -u +%Y%m%dT%H%M%SZ)
cp polybot_report.html "archive/reports/report_$TS.html"
echo "[hourly_report] unified -> polybot_report.html + archive/reports/report_$TS.html ($(wc -c < polybot_report.html) bytes)"
