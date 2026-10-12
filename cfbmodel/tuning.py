"""Weekly re-weighting of the SP+/FPI/Elo blend and home field.

Each logged pick stores every source's home-minus-away rating difference.
After grading, fit  actual_margin ~ sum(w_k * diff_k) + hfa * home_flag
over this season's graded games (weights on the simplex, hfa in [0, 5]),
then set  live = (1 - alpha) * season-start + alpha * fit,  alpha = n / (n + prior_games),
so a single noisy week can't swing the model (and weekly steps don't compound).
"""
from datetime import datetime, timezone

from sportscommon.tuning import season_anchor

from . import config, store
from .model import SOURCES

STEP = 0.05


def path():
    return config.DATA_DIR / "model_weights.json"


def load():
    return store.load(path(), None)


def apply():
    """Load tuned weights/home field into config (no-op if absent or disabled)."""
    doc = load()
    if not doc or not config.TUNING:
        return None
    config.WEIGHTS.update({k: float(v) for k, v in doc["weights"].items() if k in config.WEIGHTS})
    config.HOME_FIELD = float(doc["home_field"])
    return doc


def _samples(picks, season):
    rows = []
    for p in picks.values():
        res = p.get("result")
        if p.get("season") != season or not res or not p.get("diffs"):
            continue
        if any(k not in p["diffs"] for k in SOURCES):
            continue
        rows.append((p["diffs"], p.get("hfa_flag", 0 if p.get("neutral") else 1),
                     res["home_pts"] - res["away_pts"], p.get("line")))
    return rows


def _fit_hfa(rows, w):
    num = den = 0.0
    for d, f, y, _ in rows:
        pred = sum(w[k] * d[k] for k in SOURCES)
        num += (y - pred) * f
        den += f * f
    h = num / den if den else config.HOME_FIELD
    return max(0.0, min(5.0, h))


def _mse(rows, w, h):
    return sum((y - (sum(w[k] * d[k] for k in SOURCES) + h * f)) ** 2 for d, f, y, _ in rows) / len(rows)


def _ats(rows, w, h):
    """Cover rate of picking the side this weighting favors vs the logged line."""
    wins = n = 0
    for d, f, y, line in rows:
        if line is None:
            continue
        exp = sum(w[k] * d[k] for k in SOURCES) + h * f + line
        act = y + line
        if exp == 0 or act == 0:
            continue
        n += 1
        wins += (exp > 0) == (act > 0)
    return round(wins / n, 3) if n else None


def _grid():
    steps = int(round(1 / STEP))
    for i in range(steps + 1):
        for j in range(steps + 1 - i):
            yield {"sp": i * STEP, "fpi": j * STEP, "elo": (steps - i - j) * STEP}


def tune(picks, season, now=None, log=print):
    rows = _samples(picks, season)
    current = {"weights": {k: config.WEIGHTS[k] for k in SOURCES}, "home_field": config.HOME_FIELD}
    doc = load() or {"history": []}
    entry = {
        "at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "season": season, "games": len(rows), "before": current,
    }
    if len(rows) < config.MIN_TUNE_GAMES:
        log(f"tuning: {len(rows)} graded games with source margins (< {config.MIN_TUNE_GAMES}); weights unchanged")
        entry.update({"status": "insufficient", "after": current})
        doc.update({**current, "updated": entry["at"], "games": len(rows)})
        doc["history"] = (doc.get("history") or [])[-51:] + [entry]
        store.save(path(), doc)
        return entry

    best = None
    for w in _grid():
        h = _fit_hfa(rows, w)
        m = _mse(rows, w, h)
        if best is None or m < best[0]:
            best = (m, w, h)
    _, fit_w, fit_h = best

    anchor = season_anchor(doc, season, current["weights"], current["home_field"])
    alpha = len(rows) / (len(rows) + config.TUNE_PRIOR_GAMES)
    new_w = {k: (1 - alpha) * anchor["weights"].get(k, current["weights"][k]) + alpha * fit_w[k] for k in SOURCES}
    tot = sum(new_w.values())
    new_w = {k: round(v / tot, 3) for k, v in new_w.items()}
    new_h = round((1 - alpha) * anchor["home_field"] + alpha * fit_h, 2)

    solo = {}
    for k in SOURCES:
        w = {s: 1.0 if s == k else 0.0 for s in SOURCES}
        h = _fit_hfa(rows, w)
        solo[k] = {"rmse": round(_mse(rows, w, h) ** 0.5, 2), "ats": _ats(rows, w, h)}

    entry.update({
        "status": "updated",
        "alpha": round(alpha, 3),
        "anchor": {"weights": anchor["weights"], "home_field": anchor["home_field"]},
        "fit": {"weights": {k: round(v, 3) for k, v in fit_w.items()}, "home_field": round(fit_h, 2)},
        "after": {"weights": new_w, "home_field": new_h},
        "rmse_before": round(_mse(rows, current["weights"], current["home_field"]) ** 0.5, 2),
        "rmse_after": round(_mse(rows, new_w, new_h) ** 0.5, 2),
        "ats_before": _ats(rows, current["weights"], current["home_field"]),
        "ats_after": _ats(rows, new_w, new_h),
        "by_source": solo,
    })
    doc.update({"weights": new_w, "home_field": new_h, "updated": entry["at"], "games": len(rows)})
    doc["history"] = (doc.get("history") or [])[-51:] + [entry]
    store.save(path(), doc)
    log(
        "tuning: n={games} weights {b} -> {a} (fit {f}), HFA {hb} -> {ha}, RMSE {rb} -> {ra}".format(
            games=len(rows), b=current["weights"], a=new_w, f=entry["fit"]["weights"],
            hb=current["home_field"], ha=new_h, rb=entry["rmse_before"], ra=entry["rmse_after"])
    )
    return entry
