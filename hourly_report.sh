#!/usr/bin/env bash
# Hourly Polybot report in the EXISTING paper-report style (now with a real-money/account headline)
# from the HK box, + OOS rolling validation appended in the SAME style, + archive each report. Read-only.
set -e
cd /nfs/home/billy/test_poly_v2
B="palacedeforsaken@34.92.235.71"
O="-i $HOME/.ssh/id_rsa -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15"
ssh $O "$B" 'python3 /usr/local/bin/polybot-report-html.py 12'   > polybot_report.html
# OOS rolling validation (pull fresh recorder data, validate on an unseen split, append) — best-effort
{
  ssh $O "$B" 'python3 - <<PY
import sqlite3
s=sqlite3.connect("/home/palacedeforsaken/Polybot/market_data.db",timeout=120); s.execute("PRAGMA busy_timeout=120000")
d=sqlite3.connect("/tmp/md_oos.bak"); s.backup(d); d.close(); s.close()
PY'
  scp $O "$B:/tmp/md_oos.bak" archive/recent_market_data.db
  ssh $O "$B" 'rm -f /tmp/md_oos.bak'
  python3 hourly_ab.py archive/recent_market_data.db > ab_hourly_fragment.html
  python3 oos_validate.py archive/recent_market_data.db > oos_fragment.html
  python3 merge_report.py polybot_report.html polybot_report.html ab_hourly_fragment.html oos_fragment.html
} || echo "[hourly_report] OOS step skipped (non-fatal)"
mkdir -p archive/reports
TS=$(date -u +%Y%m%dT%H%M%SZ)
cp polybot_report.html "archive/reports/report_$TS.html"
echo "[hourly_report] -> polybot_report.html + archive/reports/report_$TS.html ($(wc -c < polybot_report.html) bytes)"
