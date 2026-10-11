from datetime import datetime
from app import rules

def fare(origin, dep, ret, stops, price, route="X", elapsed=480):
    return {"origin": origin, "dep": dep, "ret": ret, "nights": 7, "stops": stops, "price": price,
            "route": route, "airlines": "DL", "elapsed_min": elapsed, "url": "u"}

R = {"nonstop_max": 600, "connecting_max": 450, "new_low_pct": 10, "new_low_window_days": 14,
     "combined_max": 1100, "realert_drop": 50, "realert_days": 7}
O = ["SLC", "BOI"]

def test_thresholds_fire_once_each():
    fares = [fare("SLC", "2027-01-30", "2027-02-06", 0, 590), fare("SLC", "2027-01-31", "2027-02-07", 0, 599),
             fare("BOI", "2027-01-30", "2027-02-06", 1, 440)]
    a = rules.evaluate(fares, {}, R, O, {})
    assert {x["rule"] for x in a} == {"nonstop_max", "connecting_max", "combined"}
    assert next(x for x in a if x["rule"] == "nonstop_max")["price"] == 590

def test_dedupe_until_drop_or_age():
    fares = [fare("SLC", "2027-01-30", "2027-02-06", 0, 590)]
    now = datetime(2027, 1, 1)
    state = {"nonstop_max:SLC": (600, "2026-12-31T00:00:00")}
    assert rules.evaluate(fares, {}, R, ["SLC"], state, now) == []
    state = {"nonstop_max:SLC": (650, "2026-12-31T00:00:00")}
    assert len(rules.evaluate(fares, {}, R, ["SLC"], state, now)) == 1
    state = {"nonstop_max:SLC": (600, "2026-12-20T00:00:00")}
    assert len(rules.evaluate(fares, {}, R, ["SLC"], state, now)) == 1

def test_new_low_vs_window():
    fares = [fare("SLC", "2027-01-30", "2027-02-06", 1, 800)]
    assert rules.evaluate(fares, {("SLC", "connecting"): 850}, R, ["SLC"], {}) == []
    a = rules.evaluate(fares, {("SLC", "connecting"): 900}, R, ["SLC"], {})
    assert [x["rule"] for x in a] == ["new_low"]

def test_combined_needs_same_dates():
    fares = [fare("SLC", "2027-01-30", "2027-02-06", 1, 500), fare("BOI", "2027-02-01", "2027-02-08", 1, 460)]
    assert rules.combined_best(fares, O) is None
    fares.append(fare("BOI", "2027-01-30", "2027-02-06", 1, 700))
    assert rules.combined_best(fares, O)[0] == 1200
    assert rules.evaluate(fares, {}, R, O, {}) == []  # 1200 > combined_max, no thresholds hit


def test_date_pairs_return_window():
    from app.scanner import date_pairs
    cfg = {"depart_from": "2027-01-31", "depart_to": "2027-02-04", "return_from": "2027-02-07", "return_to": "2027-02-11"}
    pairs = date_pairs(cfg)
    assert len(pairs) == 25 and ("2027-02-02", "2027-02-09") in pairs
    assert date_pairs({"depart_from": "2027-02-01", "depart_to": "2027-02-01", "nights": [7]}) == [("2027-02-01", "2027-02-08")]
