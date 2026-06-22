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
# (to run the two-edge spot config instead, change the `-m polybot.live` line below)
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

echo "[update] restarting live trader ..."
pkill -f "polybot[.]live" 2>/dev/null || true
sleep 2
cd "$DEST"
setsid nohup python3 -m polybot.live > "$LOG" 2>&1 </dev/null &
sleep 8
if pgrep -af "[m] polybot.live" | grep -q python; then
  echo "[update] RUNNING:"; pgrep -af "[m] polybot.live" | grep python
else
  echo "[update] NOT RUNNING — check $LOG"
fi
echo "[update] --- recent log ---"
tail -10 "$LOG"
