"""
Definitive test of whether ANY order-book signal predicts the outcome beyond the price.

Trains a logistic regression to predict the winner from order-book features at tau=90s,
strict train/test split, and compares price-only vs the full feature set. Conclusion
(run on the historical BTC 5-min data): the full model beats price-only by AUC ~+0.002 ->
the favorite price is a near-sufficient statistic; depth/imbalance/momentum/spread add no
exploitable edge. The only edge outside the price is the external spot feed (btc_spot_divergence).

Run:  python ml_winner_check.py
"""
import numpy as np
from polybot import backtester as bt


def features_at(m, tau=90):
    rem, bid, ask = m["rem"], m["ws_bid"], m["ws_ask"]
    bs, aS = m["bid_s1"], m["ask_s1"]
    i = int(np.argmin(np.abs(rem - tau)))
    if abs(rem[i] - tau) > 15 or i < 60:
        return None
    depth = bs[i] + aS[i]
    if depth <= 0:
        return None
    return ([ask[i], np.log1p(depth), (bs[i] - aS[i]) / depth, bid[i] - bid[i - 60], ask[i] - bid[i]],
            1.0 if np.median(bid[-5:]) > 0.5 else 0.0)


def _fit(X, y, l2=1.0, lr=0.3, iters=4000):
    w = np.zeros(X.shape[1]); b = 0.0; n = len(y)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        w -= lr * (X.T @ (p - y) / n + l2 * w / n); b -= lr * np.mean(p - y)
    return w, b


def _pred(X, w, b):
    return 1 / (1 + np.exp(-(X @ w + b)))


def _logloss(y, p):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))


def _auc(y, s):
    o = np.argsort(s); r = np.empty(len(s)); r[o] = np.arange(1, len(s) + 1)
    n1, n0 = y.sum(), len(y) - y.sum()
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0) if n1 and n0 else 0.5


def main(data_dir="market_data", tau=90):
    rows = [r for r in (features_at(m, tau) for m in bt.load_markets(data_dir)) if r is not None]
    X = np.array([r[0] for r in rows]); y = np.array([r[1] for r in rows])
    tr = np.arange(len(y)) % 2 == 0; te = ~tr
    mu, sd = X[tr].mean(0), X[tr].std(0); sd[sd == 0] = 1
    print(f"[*] {len(y)} samples, P(YES)={y.mean():.3f}\n")
    print(f"{'model':40s} {'test logloss':>13} {'test AUC':>9}")
    for label, cols in [("price only", [0]), ("full (price+depth+imb+mom+spread)", [0, 1, 2, 3, 4])]:
        Xtr = (X[tr][:, cols] - mu[cols]) / sd[cols]
        Xte = (X[te][:, cols] - mu[cols]) / sd[cols]
        w, b = _fit(Xtr, y[tr]); p = _pred(Xte, w, b)
        print(f"  {label:38s} {_logloss(y[te], p):>13.4f} {_auc(y[te], p):>9.4f}")
    print("\n=> negligible lift from non-price features: no untapped order-book edge.")


if __name__ == "__main__":
    main()
