#!/usr/bin/env python3
"""Send HTML (read from stdin) as an HTML email to the user via Gmail SMTP.

    some_command_producing_html | python3 send_html.py "Subject line"

Reads the Gmail App Password from ~/.config/polybot-mail.pass (chmod 600).
Sends a multipart message: plain-text fallback + the HTML alternative.
"""
import os
import smtplib
import sys
from email.message import EmailMessage

TO = "palacedeforsaken@gmail.com"
PASS_FILE = os.path.expanduser("~/.config/polybot-mail.pass")


def main():
    if not os.path.exists(PASS_FILE):
        sys.exit(f"[send_html] no {PASS_FILE} — email not configured")
    pw = "".join(open(PASS_FILE).read().split())  # strip whitespace
    subject = sys.argv[1] if len(sys.argv) > 1 else "Polybot report"
    body_html = sys.stdin.read()
    if not body_html.strip():
        sys.exit("[send_html] empty HTML on stdin")

    msg = EmailMessage()
    msg["From"] = TO
    msg["To"] = TO
    msg["Subject"] = subject
    msg.set_content("This is the Polybot report. View it in an HTML-capable mail client.")
    msg.add_alternative(body_html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(TO, pw)
        s.send_message(msg)
    print(f"[send_html] HTML email sent to {TO}")


if __name__ == "__main__":
    main()
