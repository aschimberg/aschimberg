"""Opponent-adjusted team ratings via weighted ridge regression.

Each observation is one team's offensive output in one game:

    y = mu + off[team] - dfn[opp] + hfa * is_home

`dfn` is defensive strength (positive = good defense). Ridge penalties pull
each rating toward a prior (e.g. last season's rating, regressed), which is
what keeps early-season ratings sane.
"""


def _solve(a, b):
    """Gaussian elimination with partial pivoting (small dense systems)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            continue
        m[col], m[piv] = m[piv], m[col]
        p = m[col][col]
        for c in range(col, n + 1):
            m[col][c] /= p
        for r in range(n):
            if r != col and m[r][col] != 0:
                f = m[r][col]
                rowc = m[col]
                rowr = m[r]
                for c in range(col, n + 1):
                    rowr[c] -= f * rowc[c]
    return [m[i][n] for i in range(n)]


def fit(obs, teams, lam, prior_off=None, prior_def=None, fit_hfa=True, hfa_prior=0.0, hfa_lam=0.0):
    """obs: iterable of (off_team, def_team, is_home(0/1), y, weight).

    lam: ridge strength in observation-weight units (e.g. plays or games).
    Returns {"mu", "hfa", "off": {t: v}, "def": {t: v}}.
    """
    teams = sorted(teams)
    idx = {t: i for i, t in enumerate(teams)}
    n_t = len(teams)
    # params: [mu, hfa, off_0..off_n-1, def_0..def_n-1]
    n = 2 + 2 * n_t
    ata = [[0.0] * n for _ in range(n)]
    atb = [0.0] * n
    for o, d, h, y, w in obs:
        if o not in idx or d not in idx or w <= 0:
            continue
        cols = [(0, 1.0), (2 + idx[o], 1.0), (2 + n_t + idx[d], -1.0)]
        if fit_hfa and h:
            cols.append((1, float(h)))
        for i, xi in cols:
            atb[i] += w * xi * y
            for j, xj in cols:
                ata[i][j] += w * xi * xj
    prior_off = prior_off or {}
    prior_def = prior_def or {}
    for t, i in idx.items():
        ata[2 + i][2 + i] += lam
        atb[2 + i] += lam * prior_off.get(t, 0.0)
        ata[2 + n_t + i][2 + n_t + i] += lam
        atb[2 + n_t + i] += lam * prior_def.get(t, 0.0)
    if fit_hfa:
        ata[1][1] += hfa_lam
        atb[1] += hfa_lam * hfa_prior
    else:
        ata[1][1] = 1.0
        atb[1] = hfa_prior
    ata[0][0] += 1e-9
    x = _solve(ata, atb)
    return {
        "mu": x[0],
        "hfa": x[1],
        "off": {t: x[2 + i] for t, i in idx.items()},
        "def": {t: x[2 + n_t + i] for t, i in idx.items()},
    }
