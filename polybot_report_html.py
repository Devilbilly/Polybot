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
LED = "/home/palacedeforsaken/Polybot/ledger.db"
CREDS = "/home/palacedeforsaken/.config"
DEPOSIT_START = 118.57   # first real-money balance read = deposit baseline


def realmoney():
    """Account ground truth (cash + Polymarket positions) + per-coin REAL fills from the ledger."""
    cash = posval = None
    try:
        from eth_account import Account  # noqa: F401
        from py_clob_client_v2 import ClobClient
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        key = open(CREDS + "/polybot-clob.key").read().strip()
        funder = open(CREDS + "/polybot-clob.funder").read().strip()
        cl = ClobClient("https://clob.polymarket.com", 137, key=key, signature_type=3, funder=funder)
        cl.set_api_creds(cl.derive_api_key())
        b = cl.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)).get("balance")
        cash = float(b) / 1e6 if b not in (None, "") else None
        import json
        import urllib.request
        for _ in range(3):
            try:
                r = json.load(urllib.request.urlopen(urllib.request.Request(
                    "https://data-api.polymarket.com/value?user=%s" % funder,
                    headers={"User-Agent": "Mozilla"}), timeout=8))
                posval = float(r[0]["value"]) if (isinstance(r, list) and r) else float(r.get("value"))
                break
            except Exception:
                time.sleep(1)
    except Exception:
        pass
    per = defaultdict(lambda: [0, 0.0, 0, 0, 0.0])   # coin -> fills,spent,settled,wins,realized
    phour = defaultdict(lambda: [0, 0, 0, 0.0])       # CST hour -> fills,settled,wins,realized(proxy)
    hr = [0, 0.0]
    try:
        lc = sqlite3.connect("file:%s?mode=ro" % LED, uri=True)
        mx = lc.execute("SELECT MAX(ts) FROM ledger").fetchone()[0] or 0
        for coin, side, fp, shv, ts, win in lc.execute(
                "SELECT f.coin,f.side,f.fill_price,f.fill_shares,f.ts,s.winner FROM ledger f "
                "LEFT JOIN ledger s ON s.trade_id=f.trade_id AND s.event='SETTLE' "
                "WHERE f.event='FILL' AND f.mode='LIVE' AND f.fill_price>0"):
            coin = coin if coin in ("btc", "eth", "sol", "xrp") else "?"
            fp = float(fp or 0); shv = float(shv or 0); cost = fp * shv
            a = per[coin]; a[0] += 1; a[1] += cost
            hk = time.strftime("%m-%d %H", time.gmtime(int(ts) + 8 * 3600)) if ts else "?"
            ph = phour[hk]; ph[0] += 1
            if ts and ts >= mx - 3600:
                hr[0] += 1; hr[1] += cost
            if win:
                pay = shv if side == win else 0.0
                a[2] += 1; a[3] += 1 if side == win else 0; a[4] += pay - cost
                ph[1] += 1; ph[2] += 1 if side == win else 0; ph[3] += pay - cost
        lc.close()
    except Exception:
        pass
    return cash, posval, per, hr, phour


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


def live_params():
    """Everything that defines the live run, so every number in this report is traceable to an exact
    config: strategy params (from the config the trader actually loads) + execution/sizing constants
    (introspected from the DEPLOYED code, not assumed) + the run mode (--live/--dryrun) + git commit."""
    import json
    import inspect
    strat = []
    exec_d = dict(order_type="FAK", min_usd=1.0, max_shares=5.0, min_price=0.5,
                  desync_tol=0.05, signature_type=3)
    run_d = dict(capital_per_market=1000.0)
    try:
        cfg = json.load(open(CFG))
        strat = [(s["id"], s.get("name", "?"), s.get("params", {})) for s in cfg["strategies"]]
    except Exception:
        pass
    try:                                  # introspect deployed defaults so the report self-updates
        sys.path.insert(0, "/home/palacedeforsaken/Polybot")
        from polybot import execution as _ex, live as _lv
        ep = inspect.signature(_ex.ClobExecutor.__init__).parameters
        for k in ("min_usd", "max_shares", "min_price", "desync_tol"):
            if k in ep and ep[k].default is not inspect.Parameter.empty:
                exec_d[k] = ep[k].default
        rp = inspect.signature(_lv.run_multi).parameters
        if rp["capital_per_market"].default is not inspect.Parameter.empty:
            run_d["capital_per_market"] = rp["capital_per_market"].default
    except Exception:
        pass
    es = sh("systemctl show polybot-trade -p ExecStart --value")
    mode = "live (real money)" if "--live" in es else ("dry-run" if "--dryrun" in es else "paper")
    am = re.search(r"--assets\s+([a-z,]+)", es)      # trader may trade a subset (recorder still all 4)
    assets = ", ".join(am.group(1).split(",")) if am else "btc, eth, sol, xrp"
    commit = sh("cd /home/palacedeforsaken/Polybot && git rev-parse --short HEAD 2>/dev/null")
    if not commit or commit == "?":          # box isn't a git checkout -> show when the config last changed
        try:
            import os
            commit = "cfg edited " + time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(os.path.getmtime(CFG)))
        except Exception:
            commit = "?"
    return dict(strat=strat, exec=exec_d, run=run_d, mode=mode, commit=commit, cfg=CFG, assets=assets)


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
    P.append(f"<div style='font-size:13px;color:#555;'>"
             f"<b style='color:#c0392b;'>PAPER signal</b> P&amp;L (all-time, ~$25/trade notional &mdash; <b>NOT real money</b>) "
             f"<b style='color:{col(grand)};font-size:15px;'>${grand:+.0f}</b> &middot; real account below &middot; disk {html.escape(disk)}</div>")

    th = "padding:6px 8px;text-align:right;font-size:13px;border-bottom:2px solid #ddd;"
    td = "padding:5px 8px;text-align:right;font-size:13px;border-bottom:1px solid #eee;"
    tdl = td.replace("right", "left")

    # ===== REAL MONEY (account ground truth) — the headline now =====
    cash, posval, rper, rhr, rphour = realmoney()
    acct = (cash + (posval or 0.0)) if cash is not None else None
    rpnl = (acct - DEPOSIT_START) if acct is not None else None
    P.append("<h3 style='margin:14px 0 4px;'>Real money - account "
             "<span style='font-size:11px;color:#999;font-weight:400;'>(ground truth)</span></h3>")
    P.append("<table style='border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden;'>")
    P.append(f"<tr><td style='{tdl}'>account value</td>"
             f"<td style='{td}'><b style='font-size:15px;'>{'$%.2f' % acct if acct is not None else 'n/a'}</b></td></tr>")
    if rpnl is not None:
        P.append(f"<tr><td style='{tdl}'>vs deposit ${DEPOSIT_START:.0f}</td>"
                 f"<td style='{td}color:{col(rpnl)};'><b>{rpnl:+.2f}</b></td></tr>")
    P.append(f"<tr><td style='{tdl}'>cash / open positions</td>"
             f"<td style='{td}'>{'$%.2f' % cash if cash is not None else '?'} / "
             f"{'$%.2f' % posval if posval is not None else '?'}</td></tr>")
    P.append(f"<tr><td style='{tdl}'>live fills (last 1h)</td><td style='{td}'>{rhr[0]} (${rhr[1]:.2f})</td></tr>")
    P.append("</table>")

    P.append("<h3 style='margin:14px 0 4px;'>Real money - per coin "
             "<span style='font-size:11px;color:#999;font-weight:400;'>(realized via WS-proxy; account above is truth)</span></h3>")
    P.append("<table style='border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden;'>")
    P.append(f"<tr><th style='{th.replace('right','left')}'>coin</th><th style='{th}'>fills</th>"
             f"<th style='{th}'>win%</th><th style='{th}'>realized $</th></tr>")
    rtot = [0, 0.0, 0, 0, 0.0]
    for cn in ("btc", "eth", "sol", "xrp"):
        a = rper.get(cn, [0, 0.0, 0, 0, 0.0])
        for i in range(5):
            rtot[i] += a[i]
        wrr = f"{round(100*a[3]/a[2])}%" if a[2] else "-"
        P.append(f"<tr><td style='{tdl}'><b>{cn}</b></td><td style='{td}'>{a[0]}</td>"
                 f"<td style='{td}'>{wrr}</td><td style='{td}color:{col(a[4])};'><b>{a[4]:+.2f}</b></td></tr>")
    wrr = f"{round(100*rtot[3]/rtot[2])}%" if rtot[2] else "-"
    P.append(f"<tr><td style='{tdl}'><b>TOTAL</b></td><td style='{td}'>{rtot[0]}</td>"
             f"<td style='{td}'>{wrr}</td><td style='{td}color:{col(rtot[4])};'><b>{rtot[4]:+.2f}</b></td></tr>")
    P.append("</table>")

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

    # ===== Hourly - PAPER vs REAL side by side (so the correlation is visible) =====
    paper_h = {hh: (sum(hourly[hh][k][0] for k, _ in SLEEVES),
                    sum(hourly[hh][k][1] for k, _ in SLEEVES),
                    sum(hourly[hh][k][2] for k, _ in SLEEVES)) for hh in hourly}
    allhh = sorted(set(paper_h) | set(rphour))
    showh = allhh[-HOURS:]
    P.append(f"<h3 style='margin:14px 0 4px;'>Hourly - Paper vs Real (last {len(showh)}h, CST)</h3>")
    P.append("<div style='overflow-x:auto;'><table style='border-collapse:collapse;width:100%;background:#fff;border-radius:8px;'>")
    P.append(f"<tr><th style='{th.replace('right','left')}'>hour</th><th style='{th}'>paper $</th>"
             f"<th style='{th}'>paper win%</th><th style='{th}'>real $</th><th style='{th}'>real win%</th>"
             f"<th style='{th}'>real n</th></tr>")
    for hh in showh:
        pp = paper_h.get(hh, (0.0, 0, 0))
        rr = rphour.get(hh, [0, 0, 0, 0.0])
        P.append(f"<tr><td style='{tdl}'>{hh}</td>"
                 f"<td style='{td}color:{col(pp[0])};'><b>{money(pp[0])}</b></td><td style='{td}'>{wr(pp[1], pp[2])}</td>"
                 f"<td style='{td}color:{col(rr[3])};'><b>{rr[3]:+.2f}</b></td><td style='{td}'>{wr(rr[1], rr[2])}</td>"
                 f"<td style='{td}'>{rr[0]}</td></tr>")
    P.append("</table></div>")
    common = [h for h in allhh if paper_h.get(h, (0, 0, 0))[1] > 0 and rphour.get(h, [0, 0, 0, 0])[1] > 0]
    if len(common) >= 3:
        xs = [paper_h[h][0] for h in common]
        ys = [rphour[h][3] for h in common]
        nn = len(xs); mx = sum(xs) / nn; my = sum(ys) / nn
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        dx = sum((x - mx) ** 2 for x in xs) ** 0.5
        dy = sum((y - my) ** 2 for y in ys) ** 0.5
        r = num / (dx * dy) if dx > 0 and dy > 0 else 0.0
        interp = "tightly track" if r > 0.6 else ("loosely track" if r > 0.2 else ("diverge" if r < -0.1 else "weak"))
        P.append(f"<div style='font-size:13px;color:#555;margin:6px 0;'>Paper &harr; Real hourly correlation "
                 f"<b style='color:{col(r)};font-size:15px;'>r = {r:+.2f}</b> over {nn}h - they {interp}. "
                 f"<span style='color:#999;font-size:11px;'>Real mirrors paper entries so they should be positively "
                 f"correlated; gap = stake size / slippage / proxy noise.</span></div>")

    # ===== Per-coin x hour (pnl / win% / n=trades / fire%) =====
    # Separate pass over rows that does NOT skip p==0, because fire% needs the opportunity count
    # (every round, traded or not). coin is the session_id suffix (multi-<ts>-btc -> btc).
    chx = defaultdict(lambda: defaultdict(lambda: [0.0, 0, 0, 0]))   # hr -> coin -> [pnl,fires,wins,opps]
    for ts, st, p, sid in rows:
        coin = (sid or "").rsplit("-", 1)[-1]
        if coin not in COIN_NAMES:
            continue
        hh = time.strftime("%m-%d %H", time.gmtime(ts + 8 * 3600))
        b = chx[hh][coin]; b[3] += 1
        if p != 0:
            b[0] += p; b[1] += 1
            if p > 0:
                b[2] += 1
    # running cumulative TOTAL pnl (all coins) per hour -> the Σcum last column (trend of the book)
    htot = {hh: sum(chx[hh][cn][0] for cn in COIN_NAMES if cn in chx[hh]) for hh in chx}
    cumrun = 0.0
    cum_at = {}
    for hh in sorted(chx):                       # accumulate over ALL hours, not just the shown window
        cumrun += htot[hh]
        cum_at[hh] = cumrun
    showc = sorted(chx)[-HOURS:]
    if showc:
        P.append(f"<h3 style='margin:14px 0 4px;'>Per-coin x hour "
                 f"<span style='font-size:11px;color:#999;font-weight:400;'>(<b>paper signal</b>, last {len(showc)}h, CST &middot; "
                 f"pnl / win% &middot; n &middot; fire% &middot; &Sigma;cum = paper running total, <b>not real money</b>)</span></h3>")
        P.append("<div style='overflow-x:auto;'><table style='border-collapse:collapse;width:100%;background:#fff;border-radius:8px;'>")
        hdr = f"<tr><th style='{tdl.replace('1px solid #eee','2px solid #ddd')}'>hour</th>"
        for cn in COIN_NAMES:
            hdr += f"<th style='{th}'>{cn}</th>"
        hdr += f"<th style='{th}'>&Sigma;cum</th>"
        P.append(hdr + "</tr>")
        for hh in showc:
            line = f"<tr><td style='{tdl}'>{hh}</td>"
            for cn in COIN_NAMES:
                pnl, fires, wins, opps = chx[hh].get(cn, [0.0, 0, 0, 0])
                if opps:
                    inner = (f"<b>{money(pnl)}</b><br><span style='font-size:10px;color:#999;'>"
                             f"{wr(fires, wins)} &middot; {fires} &middot; {wr(opps, fires)}</span>")
                    line += f"<td style='{td}color:{col(pnl)};'>{inner}</td>"
                else:
                    line += f"<td style='{td}color:#bbb;'>-</td>"
            cv = cum_at[hh]
            line += f"<td style='{td}color:{col(cv)};'><b>{money(cv)}</b></td>"
            P.append(line + "</tr>")
        P.append("</table></div>")

    # ===== LIVE PARAMETERS (so every number above is traceable to an exact config) =====
    lp = live_params()
    modecol = "#067d06" if "live" in lp["mode"] else "#888"
    P.append("<h3 style='margin:16px 0 4px;'>Live parameters "
             "<span style='font-size:11px;color:#999;font-weight:400;'>(what produced the numbers above)</span></h3>")
    P.append(f"<div style='font-size:12px;color:#555;margin:2px 0 6px;'>mode "
             f"<b style='color:{modecol};'>{html.escape(lp['mode'])}</b> &middot; config <code>{html.escape(lp['cfg'].split('/')[-1])}</code>"
             f" &middot; code <code>{html.escape(lp['commit'])}</code></div>")
    pk = ["buy_p", "sell_p", "stop_p", "time_cutoff", "max_buy", "bullet_pct", "lookback", "flat_tol"]
    P.append("<div style='overflow-x:auto;'><table style='border-collapse:collapse;width:100%;background:#fff;border-radius:8px;'>")
    hdr = f"<tr><th style='{th.replace('right','left')}'>sleeve</th><th style='{th.replace('right','left')}'>strategy</th>"
    for k in pk:
        hdr += f"<th style='{th}'>{k}</th>"
    P.append(hdr + "</tr>")
    for sid, nm, pr in lp["strat"]:
        row = (f"<tr><td style='{tdl}'><b>{html.escape(str(sid))}</b></td>"
               f"<td style='{tdl}'><code>{html.escape(str(nm))}</code></td>")
        for k in pk:
            v = pr.get(k, "-")
            hl = "color:#067d06;font-weight:700;" if k in ("buy_p", "flat_tol") else ""
            row += f"<td style='{td}{hl}'>{v}</td>"
        P.append(row + "</tr>")
    P.append("</table></div>")
    e = lp["exec"]; rn = lp["run"]
    P.append(f"<div style='font-size:12px;color:#555;margin:6px 0;'>"
             f"<b>execution</b>: order <code>{e['order_type']}</code> (fill-and-kill) &middot; "
             f"sizing integer shares min <code>${e['min_usd']:.0f}</code> / max <code>{e['max_shares']:.0f} sh</code> &middot; "
             f"min price <code>{e['min_price']}</code> &middot; stale-book gate <code>{e['desync_tol']}</code> &middot; "
             f"signature_type <code>{e['signature_type']}</code> &middot; BUY-only<br>"
             f"<b>run</b>: capital <code>${rn['capital_per_market']:.0f}</code>/market &middot; "
             f"assets <code>{html.escape(lp['assets'])}</code> "
             f"<span style='color:#999;'>(trader; recorder still records all 4)</span> &middot; "
             f"real stake ~$1/trade (1-share probe)</div>")

    P.append("<p style='font-size:11px;color:#999;margin-top:14px;'>cum = running realized P&amp;L from the very start (reset-independent). "
             "The per-session $4000 total resets every 6h. Coins are current-session only.</p>")
    P.append("</div></body></html>")
    sys.stdout.write("\n".join(P))


if __name__ == "__main__":
    main()
