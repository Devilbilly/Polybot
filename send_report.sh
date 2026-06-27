#!/usr/bin/env bash
# Email a report body (stdin) to the user via Gmail SMTP.
#   usage:  some_command | bash send_report.sh "Subject line"
# Reads the Gmail App Password from ~/.config/polybot-mail.pass (chmod 600).
# Exits 2 (and prints a notice) if the password file is missing, so callers can fall back.
set -uo pipefail
TO="palacedeforsaken@gmail.com"
PASS_FILE="$HOME/.config/polybot-mail.pass"
SUBJ="${1:-Polybot status}"
if [ ! -f "$PASS_FILE" ]; then
  echo "[send_report] no $PASS_FILE — email NOT configured yet (skipping send)"; exit 2
fi
BODY="$(cat)"
MSG="$(mktemp)"
{ printf 'From: Polybot <%s>\r\n' "$TO"
  printf 'To: %s\r\n' "$TO"
  printf 'Subject: %s\r\n' "$SUBJ"
  printf 'Content-Type: text/plain; charset=UTF-8\r\n'
  printf '\r\n'
  printf '%s\r\n' "$BODY" | sed 's/$/\r/'
} > "$MSG"
if curl --silent --show-error --ssl-reqd --url 'smtps://smtp.gmail.com:465' \
     --mail-from "$TO" --mail-rcpt "$TO" \
     --user "$TO:$(tr -d '[:space:]' < "$PASS_FILE")" --upload-file "$MSG"; then
  echo "[send_report] email sent to $TO"
else
  echo "[send_report] curl SMTP send FAILED (check the app password)"; rm -f "$MSG"; exit 1
fi
rm -f "$MSG"
