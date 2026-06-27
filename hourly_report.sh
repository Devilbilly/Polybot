#!/usr/bin/env bash
# Build the hourly Polybot report from the HK box: base PnL HTML + REAL-MONEY section (ledger) +
# past-hour deep analysis, merged into polybot_report.html. Email + health judgment done by caller.
set -e
cd /nfs/home/billy/test_poly_v2
B="palacedeforsaken@34.92.235.71"
O="-i $HOME/.ssh/id_rsa -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15"
ssh $O "$B" 'python3 /usr/local/bin/polybot-report-html.py 12'   > base_report.html
ssh $O "$B" 'python3 ~/Polybot/realmoney_section.py'             > realmoney_fragment.html
ssh $O "$B" 'python3 ~/Polybot/analyze_hour.py 3600'            > analysis_fragment.html
python3 merge_report.py base_report.html polybot_report.html realmoney_fragment.html analysis_fragment.html
echo "[hourly_report] merged $(wc -c < polybot_report.html) bytes"
