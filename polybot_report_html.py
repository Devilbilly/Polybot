#!/usr/bin/env python3
"""Generate a mobile-friendly HTML Polybot report (status + per-coin + per-sleeve + hourly).

Prints HTML to stdout. Run on the box (has the live DB, log, systemctl).
Round-level pnl is RESET-INDEPENDENT (summed across all trader sessions).
Usage:  python3 polybot_report_html.py [hours]      # default 12
"""
import html
import re
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict

DB = "/home/palacedeforsaken/Polybot/polymarket.db"
LOG = "/home/palacedeforsaken/live_overnight.log"
CFG = "/home/palacedeforsaken/Polybot/polybot/portfolio.json"
HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 12


def _sleeves():
    try:
        import json
        cfg = json.load(open(CFG))
        return [(s["id"], s["id"].replace("fav_", "")) for s in cfg["strategies"]]
    except Exception:
        return [("fav_hold", "hold")]


SLEEVES = _sleeves()


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "?"


def latest_log():
    pat = re.compile(r"TOTAL \$([\d.]+).*?btc=\$([\d.]+).*?eth=\$([\d.]+).*?sol=\$([\d.]+).*?xrp=\$([\d.]+)")
    total, coins = None, None
    try:
        with open(LOG, errors="ignore") as f:
            for ln in f:
                m = pat.search(ln)
                if m:
                    total = float(m.group(1))
                    coins = [float(m.group(i)) for i in (2, 3, 4, 5)]
    except OSError:
        pass
    return total, coins


def col(v):
    return "#067d06" if v > 0.5 else ("#c0392b" if v < -0.5 else "#777")


def money(v):
    return f"{v:+.0f}"


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    c.execute("PRAGMA busy_timeout=20000")
    rows = c.execute(
        "SELECT s.ts, ss.strategy, ss.pnl, ss.session_id FROM session_strategy ss "
        "JOIN sessions s ON ss.session_id=s.session_id AND ss.round_no=s.round_no"
    ).fetchall()

    total, coins = latest_log()
    svc = {s: sh(f"systemctl is-active {s}") for s in ("polybot-record", "polybot-trade", "polybot-vpn")}
    disk = sh("df -h / | awk 'NR==2{print $5\" used, \"$4\" free\"}'")
    now = sh("TZ=Asia/Taipei date '+%Y-%m-%d %H:%M'") or "now"

    COIN_NAMES = ("btc", "eth", "sol", "xrp")
    cum = defaultdict(lambda: [0.0, 0, 0])
    hourly = defaultdict(lambda: defaultdict(lambda: [0.0, 0, 0]))
    cs = defaultdict(lambda: defaultdict(lambda: [0.0, 0, 0]))   # coin -> sleeve -> [pnl,fired,wins]
    for ts, st, p, sid in rows:
        if p == 0:
            continue
        cum[st][0] += p; cum[st][1] += 1; cum[st][2] += 1 if p > 0 else 0
        hr = time.strftime("%m-%d %H", time.gmtime(ts + 8 * 3600))
        b = hourly[hr][st]; b[0] += p; b[1] += 1; b[2] += 1 if p > 0 else 0
        coin = (sid or "").rsplit("-", 1)[-1]      # session_id "multi-<ts>-btc" -> "btc" (legacy -> a number)
        if coin in COIN_NAMES:
            d = cs[coin][st]; d[0] += p; d[1] += 1; d[2] += 1 if p > 0 else 0
    all_hours = sorted(hourly)
    run = defaultdict(lambda: [0.0, 0, 0]); cum_at = {}
    for hr in all_hours:
        for k, _ in SLEEVES:
            b = hourly[hr][k]; r = run[k]
            r[0] += b[0]; r[1] += b[1]; r[2] += b[2]
        cum_at[hr] = {k: list(run[k]) for k, _ in SLEEVES}
    show = all_hours[-HOURS:]
    grand = sum(cum[k][0] for k, _ in SLEEVES)

    def wr(f, w):
        return f"{round(100*w/f)}%" if f else "-"

    P = []
    P.append("<!doctype html><html><head><meta charset='utf-8'>"
             "<meta name='viewport' content='width=device-width, initial-scale=1'></head>")
    P.append("<body style='margin:0;background:#f4f5f7;font-family:-apple-system,Segoe UI,Roboto,sans-serif;'>")
    P.append("<div style='max-width:680px;margin:0 auto;padding:12px;color:#222;'>")
    P.append(f"<h2 style='margin:6px 0;'>Polybot report <span style='font-size:13px;color:#888;font-weight:400;'>{now} CST</span></h2>")

    # status line
    badges = "".join(
        f"<span style='display:inline-block;padding:2px 8px;margin:2px;border-radius:10px;font-size:12px;"
        f"background:{'#e6f5e6' if v=='active' else '#fdecea'};color:{'#067d06' if v=='active' else '#c0392b'};'>"
        f"{html.escape(s.replace('polybot-',''))}: {html.escape(v)}</span>" for s, v in svc.items())
    P.append(f"<div style='margin:4px 0;'>{badges}</div>")
    P.append(f"<div style='font-size:13px;color:#555;'>realized P&amp;L (all-time, reset-independent) "
             f"<b style='color:{col(grand)};font-size:15px;'>${grand:+.0f}</b> - disk {html.escape(disk)}</div>")

    th = "padding:6px 8px;text-align:right;font-size:13px;border-bottom:2px solid #ddd;"
    td = "padding:5px 8px;text-align:right;font-size:13px;border-bottom:1px solid #eee;"
    tdl = td.replace("right", "left")

    # COINS - all-time cumulative (reset-independent); the pnl column sums to the Sleeves TOTAL.
    # "now $" is the current-session cash (resets only on a VM reboot now).
    P.append("<h3 style='margin:14px 0 4px;'>Coins - cumulative (all-time)</h3>")
    P.append("<table style='border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden;'>")
    P.append(f"<tr><th style='{th.replace('right','left')}'>coin</th><th style='{th}'>pnl $</th>"
             f"<th style='{th}'>win%</th><th style='{th}'>trades</th></tr>")
    for cn in COIN_NAMES:
        pnl = sum(cs[cn][k][0] for k, _ in SLEEVES)
        fired = sum(cs[cn][k][1] for k, _ in SLEEVES)
        wins = sum(cs[cn][k][2] for k, _ in SLEEVES)
        P.append(f"<tr><td style='{tdl}'><b>{cn}</b></td>"
                 f"<td style='{td}color:{col(pnl)};'><b>{money(pnl)}</b></td>"
                 f"<td style='{td}'>{wr(fired, wins)}</td><td style='{td}'>{fired}</td></tr>")
    P.append("</table>")

    # SLEEVES cumulative
    P.append("<h3 style='margin:14px 0 4px;'>Sleeves - cumulative (all-time)</h3>")
    P.append("<table style='border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden;'>")
    P.append(f"<tr><th style='{th.replace('right','left')}'>sleeve</th><th style='{th}'>pnl $</th><th style='{th}'>win%</th><th style='{th}'>trades</th></tr>")
    for k, short in SLEEVES:
        p, f, w = cum[k]
        tag = " (!)" if p < -5 else ""
        P.append(f"<tr><td style='{tdl}'>{short}{tag}</td><td style='{td}color:{col(p)};'><b>{money(p)}</b></td>"
                 f"<td style='{td}'>{wr(f,w)}</td><td style='{td}'>{f}</td></tr>")
    P.append(f"<tr><td style='{tdl}'><b>TOTAL</b></td><td style='{td}color:{col(grand)};'><b>{money(grand)}</b></td><td style='{td}'></td><td style='{td}'></td></tr>")
    P.append("</table>")

    # COIN x SLEEVE matrix (cumulative) - needs per-coin session ids (new data)
    P.append("<h3 style='margin:14px 0 4px;'>Coin x Sleeve - cumulative pnl $ <span style='font-size:11px;color:#999;font-weight:400;'>(win%)</span></h3>")
    if not any(cs[cn] for cn in COIN_NAMES):
        P.append("<p style='font-size:12px;color:#999;'>populates from new rounds after the per-coin logging fix - give it ~1h</p>")
    else:
        P.append("<div style='overflow-x:auto;'><table style='border-collapse:collapse;width:100%;background:#fff;border-radius:8px;'>")
        hdr = f"<tr><th style='{tdl.replace('1px solid #eee','2px solid #ddd')}'>coin</th>"
        for _, short in SLEEVES:
            hdr += f"<th style='{th}'>{short}</th>"
        hdr += f"<th style='{th}'>TOT</th></tr>"
        P.append(hdr)
        for cn in COIN_NAMES:
            if not cs[cn]:
                continue
            line = f"<tr><td style='{tdl}'><b>{cn}</b></td>"
            ctot = 0.0
            for k, _ in SLEEVES:
                pp, ff, ww = cs[cn][k]; ctot += pp
                inner = (f"<b>{money(pp)}</b><br><span style='font-size:10px;color:#999;'>{wr(ff,ww)}</span>") if ff else "<span style='color:#bbb'>-</span>"
                line += f"<td style='{td}color:{col(pp)};'>{inner}</td>"
            line += f"<td style='{td}color:{col(ctot)};'><b>{money(ctot)}</b></td></tr>"
            P.append(line)
        P.append("</table></div>")

    # HOURLY grid (rows=hour, cols=sleeve pnl + TOTAL + cum)
    P.append(f"<h3 style='margin:14px 0 4px;'>Hourly P&amp;L (last {len(show)}h, CST)</h3>")
    P.append("<div style='overflow-x:auto;'><table style='border-collapse:collapse;width:100%;background:#fff;border-radius:8px;'>")
    hdr = f"<tr><th style='{th.replace('right','left')}'>hour</th>"
    for _, short in SLEEVES:
        hdr += f"<th style='{th}'>{short}</th>"
    hdr += f"<th style='{th}'>TOT</th><th style='{th}'>cum</th></tr>"
    P.append(hdr)
    for hr in show:
        line = f"<tr><td style='{tdl}'>{hr}</td>"
        tot = 0.0
        for k, _ in SLEEVES:
            p, f, w = hourly[hr][k]; tot += p
            inner = (f"<b>{money(p)}</b><br><span style='font-size:10px;color:#999;'>{wr(f,w)}-{f}</span>") if f else "<span style='color:#bbb'>-</span>"
            line += f"<td style='{td}color:{col(p)};'>{inner}</td>"
        ctot = sum(cum_at[hr][k][0] for k, _ in SLEEVES)
        line += f"<td style='{td}color:{col(tot)};'><b>{money(tot)}</b></td><td style='{td}color:{col(ctot)};'>{money(ctot)}</td></tr>"
        P.append(line)
    P.append("</table></div>")

    P.append("<p style='font-size:11px;color:#999;margin-top:14px;'>cum = running realized P&amp;L from the very start (reset-independent). "
             "The per-session $4000 total resets every 6h. Coins are current-session only.</p>")
    P.append("</div></body></html>")
    sys.stdout.write("\n".join(P))


if __name__ == "__main__":
    main()
