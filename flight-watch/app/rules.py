"""Deal rules evaluated after each run. Pure functions over fare rows so they are testable."""
from datetime import datetime, timedelta

NONSTOP, CONNECTING = "nonstop", "connecting"


def cls(stops: int) -> str:
    return NONSTOP if stops == 0 else CONNECTING


def best_per_pair(fares):
    """{(origin, dep, ret): {cls: fare_dict}} — cheapest fare per class per date pair."""
    out = {}
    for f in fares:
        key = (f["origin"], f["dep"], f["ret"])
        slot = out.setdefault(key, {})
        c = cls(f["stops"])
        if c not in slot or f["price"] < slot[c]["price"]:
            slot[c] = dict(f)
    return out


def best_per_origin(fares):
    """{origin: {cls: fare}} — cheapest across the whole grid."""
    out = {}
    for f in fares:
        slot = out.setdefault(f["origin"], {})
        c = cls(f["stops"])
        if c not in slot or f["price"] < slot[c]["price"]:
            slot[c] = dict(f)
    return out


def combined_best(fares, origins):
    """Cheapest SLC+BOI (any class, in cap) for the SAME dep/ret. Returns (total, dep, ret, {origin: fare}) or None."""
    if len(origins) < 2:
        return None
    per = best_per_pair(fares)
    by_dates = {}
    for (o, dep, ret), slots in per.items():
        cheapest = min(slots.values(), key=lambda f: f["price"])
        by_dates.setdefault((dep, ret), {})[o] = cheapest
    best = None
    for (dep, ret), m in by_dates.items():
        if all(o in m for o in origins):
            total = sum(m[o]["price"] for o in origins)
            if best is None or total < best[0]:
                best = (total, dep, ret, m)
    return best


def describe(f) -> str:
    stops = "nonstop" if f["stops"] == 0 else f"{f['stops']}-stop"
    h, m = divmod(int(f["elapsed_min"]), 60)
    return f"${f['price']} {f['origin']} {f['dep']}→{f['ret']} {stops} {f['route']} {f['airlines']} {h}h{m:02d}"


def evaluate(fares, prior_min, rules, origins, state, now=None):
    """Return a list of alert dicts for this run.

    fares:     this run's fare rows (dicts)
    prior_min: {(origin, cls): min price over the trailing window, excluding this run} (None if no history)
    state:     {key: (price, ts_iso)} last alert per rule key, for de-duplication
    """
    now = now or datetime.now()
    alerts = []
    drop, days = int(rules.get("realert_drop", 50)), int(rules.get("realert_days", 7))

    def should_fire(key, price):
        last = state.get(key)
        if not last:
            return True
        last_price, last_ts = last
        return price <= last_price - drop or now - datetime.fromisoformat(last_ts) >= timedelta(days=days)

    def emit(key, rule, f, msg):
        if should_fire(key, f["price"]):
            alerts.append({"key": key, "rule": rule, "origin": f.get("origin"), "dep": f["dep"], "ret": f["ret"],
                           "route": f.get("route"), "stops": f.get("stops"), "price": f["price"],
                           "message": msg, "url": f.get("url")})

    per_origin = best_per_origin(fares)
    per_pair = best_per_pair(fares)
    for origin, slots in per_origin.items():
        # absolute thresholds: cheapest qualifying, plus the next two date pairs for context
        for c, limit_key in ((NONSTOP, "nonstop_max"), (CONNECTING, "connecting_max")):
            limit = rules.get(limit_key)
            if c in slots and limit is not None and slots[c]["price"] <= int(limit):
                others = sorted((s[c] for k, s in per_pair.items() if k[0] == origin and c in s and s[c]["price"] <= int(limit)),
                                key=lambda f: f["price"])[:3]
                msg = "\n".join(describe(f) for f in others)
                emit(f"{c}_max:{origin}", f"{c}_max", slots[c], msg)
        # new low vs trailing window
        pct = float(rules.get("new_low_pct", 10))
        for c, f in slots.items():
            prev = prior_min.get((origin, c))
            if prev and f["price"] <= prev * (1 - pct / 100):
                emit(f"new_low:{origin}:{c}", "new_low", f, f"New {c} low: {describe(f)}\n(was ${prev} over the last {rules.get('new_low_window_days', 14)} days)")

    cb = combined_best(fares, origins)
    if cb:
        total, dep, ret, m = cb
        f = {"price": total, "dep": dep, "ret": ret, "origin": "+".join(origins), "route": None, "stops": None,
             "url": m[origins[0]]["url"], "elapsed_min": 0, "airlines": ""}
        lines = "\n".join(describe(m[o]) for o in origins)
        limit = rules.get("combined_max")
        prev = prior_min.get(("combined", "any"))
        if (limit is not None and total <= int(limit)) or (prev and total <= prev * (1 - float(rules.get("new_low_pct", 10)) / 100)):
            emit("combined", "combined", f, f"Both families ${total}/adult for {dep}→{ret}:\n{lines}")
    return alerts
