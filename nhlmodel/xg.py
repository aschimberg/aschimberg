"""Expected-goals model: logistic regression on unblocked shots (pure Python)."""
import math

from .shots import SHOT_TYPES

FEATURES = ["dist", "dist2", "angle", "rebound", "rush", "pp", "sh", "reb_angle"] + [f"type_{t}" for t in SHOT_TYPES[1:]] + ["type_other"]


def features(s):
    side, period, sec, dist, angle, tidx, rebound, rush, pp, sh, en, goal, goalie = s
    f = [dist / 30.0, (dist / 30.0) ** 2, angle / 45.0, rebound, rush, pp, sh, rebound * angle / 45.0]
    onehot = [0.0] * len(SHOT_TYPES)  # wrist is the baseline
    onehot[min(tidx, len(SHOT_TYPES)) - 1 if tidx >= 1 else 0] = 1.0 if tidx >= 1 else 0.0
    return f + onehot


def _sigmoid(z):
    if z < -30:
        return 0.0
    if z > 30:
        return 1.0
    return 1 / (1 + math.exp(-z))


def _solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        p = m[c][c] or 1e-12
        for k in range(c, n + 1):
            m[c][k] /= p
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] for i in range(n)]


def train(shots, iters=8, l2=1.0, max_rows=150000):
    rows = [s for s in shots if not s[10]]  # exclude empty-net attempts
    if len(rows) > max_rows:
        step = len(rows) / max_rows
        rows = [rows[int(i * step)] for i in range(max_rows)]
    X = [[1.0] + features(s) for s in rows]
    y = [s[11] for s in rows]
    d = len(X[0])
    w = [0.0] * d
    base = sum(y) / len(y) if y else 0.08
    w[0] = math.log(base / (1 - base)) if 0 < base < 1 else -2.4
    for _ in range(iters):
        g = [0.0] * d
        H = [[0.0] * d for _ in range(d)]
        for xi, yi in zip(X, y):
            p = _sigmoid(sum(a * b for a, b in zip(w, xi)))
            r = yi - p
            wt = p * (1 - p)
            for j in range(d):
                xj = xi[j]
                if xj == 0:
                    continue
                g[j] += r * xj
                hj = H[j]
                v = wt * xj
                for k in range(j, d):
                    if xi[k]:
                        hj[k] += v * xi[k]
        for j in range(d):
            for k in range(j):
                H[j][k] = H[k][j]
            H[j][j] += l2
            g[j] -= l2 * w[j] if j else 0
        step = _solve(H, g)
        w = [a + b for a, b in zip(w, step)]
        if max(abs(s) for s in step) < 1e-6:
            break
    ll = 0.0
    for xi, yi in zip(X, y):
        p = min(max(_sigmoid(sum(a * b for a, b in zip(w, xi))), 1e-9), 1 - 1e-9)
        ll -= yi * math.log(p) + (1 - yi) * math.log(1 - p)
    return {"features": ["intercept"] + FEATURES, "coef": [round(v, 6) for v in w], "n": len(X),
            "goal_rate": round(base, 4), "logloss": round(ll / len(X), 5) if X else None}


def predict(model, s):
    if s[10]:  # empty net
        return 0.85 if s[3] < 100 else 0.6
    xi = [1.0] + features(s)
    return _sigmoid(sum(a * b for a, b in zip(model["coef"], xi)))
