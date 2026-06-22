#!/usr/bin/env bash
# Pull the latest Polybot from GitHub and (re)start the live paper trader.
# Designed for a box with NO git installed (uses the GitHub tarball) and the deps already pip-installed.
#
# Usage on the box:
#     bash update.sh
#
# Private repo? Provide a GitHub token (fine-grained, "Contents: read") first:
#     export GH_TOKEN=github_pat_xxx ; bash update.sh
#
# Override defaults via env: POLYBOT_REPO, POLYBOT_BRANCH, POLYBOT_DIR, POLYBOT_CONFIG
set -euo pipefail
REPO="${POLYBOT_REPO:-Devilbilly/Polybot}"
BRANCH="${POLYBOT_BRANCH:-master}"
DEST="${POLYBOT_DIR:-$HOME/Polybot}"
LOG="$HOME/live_overnight.log"
MODE="${1:-single}"        # 'single' (default: one BTC market, favorites) or 'multi' (BTC/ETH/SOL/XRP, two-edge)
case "$MODE" in
  multi)  LAUNCH="python3 -u -m polybot.live --multi" ;;
  *)      LAUNCH="python3 -u -m polybot.live" ;;
esac
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "[update] fetching $REPO@$BRANCH ..."
if [ -n "${GH_TOKEN:-}" ]; then            # private repo via authenticated tarball
  curl -fsSL -H "Authorization: Bearer $GH_TOKEN" \
       "https://api.github.com/repos/$REPO/tarball/$BRANCH" -o "$TMP/pb.tgz"
else                                       # public repo (no auth)
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" -o "$TMP/pb.tgz" \
    || { echo "[update] download failed — repo is private? run:  export GH_TOKEN=<PAT> ; bash update.sh"; exit 1; }
fi
tar xz -C "$TMP" -f "$TMP/pb.tgz"
SRC="$(find "$TMP" -maxdepth 1 -type d -name '*olybot*' | head -1)"
[ -d "$SRC/polybot" ] || { echo "[update] unexpected archive layout under $TMP"; exit 1; }

# replace ONLY the package dir (keeps your ~/live_overnight.log and any local *.db)
mkdir -p "$DEST"
rm -rf "$DEST/polybot"
cp -r "$SRC/polybot" "$DEST/"
[ -f "$SRC/requirements.txt" ] && cp "$SRC/requirements.txt" "$DEST/" || true

echo "[update] updated. ping fix present (want 1): $(grep -c 'ping_timeout=None' "$DEST/polybot/live.py")"

echo "[update] restarting (supervised; survives logout + auto-restarts on crash) ..."
# kill SUPERVISORS first (else they respawn a bot), then the bots. Two patterns because the
# supervisor cmdline has capital-P 'Polybot' (case-sensitive) while the bot has 'polybot.live'.
pkill -9 -f "supervise.sh" 2>/dev/null || true
sleep 1
pkill -9 -f "polybot.live" 2>/dev/null || true
sleep 2
pkill -9 -f "supervise.sh" 2>/dev/null || true   # belt-and-suspenders: catch any respawn race
pkill -9 -f "polybot.live" 2>/dev/null || true
sleep 1
loginctl enable-linger "$USER" 2>/dev/null && echo "[update] linger ON (survives SSH logout)" \
                                            || echo "[update] note: couldn't enable linger"
# write a tiny supervisor that auto-restarts the bot on ANY exit, then launch it fully detached
echo "[update] mode: $MODE  ->  $LAUNCH"
cat > "$DEST/supervise.sh" <<SUP
#!/usr/bin/env bash
cd "\$(dirname "\$0")"
while true; do
  echo "[supervisor] \$(date '+%F %T') starting bot ($MODE)"
  $LAUNCH
  echo "[supervisor] \$(date '+%F %T') bot exited (\$?); restarting in 5s"
  sleep 5
done
SUP
chmod +x "$DEST/supervise.sh"
setsid nohup bash "$DEST/supervise.sh" >> "$LOG" 2>&1 </dev/null &
disown 2>/dev/null || true
sleep 12
if pgrep -af "[m] polybot.live" | grep -q python; then
  echo "[update] RUNNING (supervised):"; pgrep -af "polybot" | grep -vi "pgrep\|update.sh" | head -3
else
  echo "[update] NOT RUNNING — check $LOG"
fi
echo "[update] watch:  tail -f $LOG      stop:  pkill -9 -f polybot"
echo "[update] --- recent log ---"
tail -10 "$LOG"
