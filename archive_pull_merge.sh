#!/usr/bin/env bash
# Pull a fresh consistent snapshot of both box DBs and merge into the permanent master archive.
# Idempotent (archive_merge dedups), so safe to run on any cadence < the box's 48h prune window.
# Uses the reliable sandbox->box SSH path (id_rsa, port 22) — NOT the fragile box->lab VPN sync.
set -e
cd /nfs/home/billy/test_poly_v2
mkdir -p archive
TS=$(date -u +%Y%m%dT%H%M%SZ)
# Switched to the new Hong Kong box (asia-east2-c, 34.92.235.71) — the canonical box going forward.
# UserKnownHostsFile=/dev/null avoids the stale host-key entry for the reused IP.
BOX=palacedeforsaken@34.92.235.71
OPTS="-o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15"

# 1) consistent online backup on the box (safe during live writes)
ssh -i ~/.ssh/id_rsa $OPTS "$BOX" 'python3 - <<PY
import sqlite3
for f in ("market_data.db","polymarket.db"):
    s=sqlite3.connect("/home/palacedeforsaken/Polybot/"+f,timeout=120); s.execute("PRAGMA busy_timeout=120000")
    d=sqlite3.connect("/tmp/"+f+".arch"); s.backup(d); d.close(); s.close()
PY'

# 2) pull
scp -i ~/.ssh/id_rsa $OPTS "$BOX:/tmp/market_data.db.arch" "archive/pull_md_$TS.db"
scp -i ~/.ssh/id_rsa $OPTS "$BOX:/tmp/polymarket.db.arch"  "archive/pull_pm_$TS.db"
ssh -i ~/.ssh/id_rsa $OPTS "$BOX" 'rm -f /tmp/market_data.db.arch /tmp/polymarket.db.arch'

# 3) merge into the permanent master (dedup)
python3 archive_merge.py archive/master_polybot.db "archive/pull_md_$TS.db" "archive/pull_pm_$TS.db"

# 4) keep only the last 6 raw pulls (master already holds everything deduped)
ls -t archive/pull_md_*.db 2>/dev/null | tail -n +7 | xargs -r rm -f
ls -t archive/pull_pm_*.db 2>/dev/null | tail -n +7 | xargs -r rm -f
echo "[archive_pull_merge] done $TS"
