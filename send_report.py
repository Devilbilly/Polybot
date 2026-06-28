"""Email an HTML report with a PNG plot (inline via CID + attached). Gmail SMTP.
    cat report.html | ... no — call:  python3 send_report.py "Subject" report.html plot.png
The report HTML may contain a {{PLOT}} placeholder, replaced by the inline image.
"""
import os
import smtplib
import sys
from email.message import EmailMessage

TO = "palacedeforsaken@gmail.com"
PASS = os.path.expanduser("~/.config/polybot-mail.pass")


def main():
    subject, html_path, png_path = sys.argv[1], sys.argv[2], sys.argv[3]
    if not os.path.exists(PASS):
        sys.exit("[send_report] no %s — email not configured" % PASS)
    pw = "".join(open(PASS).read().split())
    html = open(html_path, encoding="utf-8").read()
    img = open(png_path, "rb").read()
    cid = "sweepplot"
    html = html.replace("{{PLOT}}",
                        '<img src="cid:%s" style="max-width:100%%;border:1px solid #ccc">' % cid)
    msg = EmailMessage()
    msg["From"] = TO; msg["To"] = TO; msg["Subject"] = subject
    msg.set_content("Polybot sweep report — view in an HTML-capable client (plot attached).")
    msg.add_alternative(html, subtype="html")
    msg.get_payload()[1].add_related(img, maintype="image", subtype="png", cid="<%s>" % cid)
    msg.add_attachment(img, maintype="image", subtype="png", filename="sweep_plot.png")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(TO, pw)
        s.send_message(msg)
    print("[send_report] sent to", TO)


if __name__ == "__main__":
    main()
