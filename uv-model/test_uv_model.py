"""Sanity checks for the modeled-UV publisher. Run: python3 test_uv_model.py"""
import datetime as dt
import math

import uv_model as m

TZ = m.TZ


def approx(a, b, tol):
    assert abs(a - b) <= tol, f"{a} != {b} ± {tol}"


def test_solstice_noon_elevations():
    # noon elevation = 90 - lat + declination
    approx(m.solar_elevation_deg(m.solar_noon(dt.date(2026, 6, 21))), 90 - 40.76 + 23.44, 0.3)
    approx(m.solar_elevation_deg(m.solar_noon(dt.date(2026, 12, 21))), 90 - 40.76 - 23.44, 0.3)


def test_solar_noon_is_around_1pm_mdt():
    noon = m.solar_noon(dt.date(2026, 9, 23))
    approx(noon.hour + noon.minute / 60, 13.32, 0.1)  # MDT: 27 min west of 105W, +60 DST, -7.5 eqt => ~13:19


def test_threshold_elevation():
    approx(m.threshold_elevation_deg(), 34.6, 0.1)
    approx(m.uvi_for_elevation(m.threshold_elevation_deg()), 3.0, 0.01)


def test_model_shape():
    assert m.uvi_for_elevation(0) == 0
    assert m.uvi_for_elevation(-5) == 0
    assert m.uvi_for_elevation(30) < m.uvi_for_elevation(60) < m.uvi_for_elevation(90)
    approx(m.uvi_for_elevation(90), 11.15, 1e-9)


def test_protection_window():
    start, end = m.protection_window(dt.date(2026, 6, 21))
    noon = m.solar_noon(dt.date(2026, 6, 21))
    assert start and end and start < noon < end
    approx((end - start).total_seconds() / 3600, 8.5, 0.2)  # 2 * acos(...)/15 h
    approx(m.uvi_at(start), 3.0, 0.05)
    approx(m.uvi_at(end), 3.0, 0.05)
    assert m.protection_window(dt.date(2026, 12, 21)) == (None, None)
    assert m.protection_window(dt.date(2026, 11, 15)) == (None, None)
    assert m.protection_window(dt.date(2026, 2, 20))[0] is not None


def test_season_edges():
    # The winter no-sunscreen season predicted by the fit: Nov 3 -> Feb 9 (noon peak < 3).
    assert m.protection_window(dt.date(2026, 11, 2))[0] is not None
    assert m.protection_window(dt.date(2026, 11, 3))[0] is None
    assert m.protection_window(dt.date(2027, 2, 9))[0] is None
    assert m.protection_window(dt.date(2027, 2, 10))[0] is not None


def test_payload():
    p = m.build_payload(dt.datetime(2026, 9, 23, 10, 0, tzinfo=TZ))
    assert p["date"] == "2026-09-23"
    assert 0 < p["uvi"] < p["uvi_peak"] < 8
    assert p["uv3_start"] < p["ts"] < p["uv3_end"] or p["protection"] is False
    assert len(p["curve"]) == (21 - 5) * 4 + 1
    assert max(pt["uvi"] for pt in p["curve"]) <= p["uvi_peak"] + 0.01
    assert p["curve"][0]["t"] == "05:00" and p["curve"][-1]["t"] == "21:00"
    night = m.build_payload(dt.datetime(2026, 9, 23, 23, 0, tzinfo=TZ))
    assert night["uvi"] == 0 and night["protection"] is False and night["level"] == "low"


def test_discovery_is_self_consistent():
    cfgs = m.discovery_configs()
    ids = [c["unique_id"] for _, c in cfgs]
    assert len(ids) == len(set(ids)) == 5
    for topic, c in cfgs:
        assert topic.startswith(m.DISCOVERY_PREFIX + "/") and topic.endswith("/config")
        assert c["state_topic"] == m.TOPIC_STATE and c["availability_topic"] == m.TOPIC_AVAIL


if __name__ == "__main__":
    import sys
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print("FAIL", name, "->", exc)
    sys.exit(1 if failed else 0)
