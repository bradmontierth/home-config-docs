#!/home/pi/.venvs/pypowerwall/bin/python
import json
import os
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import pypowerwall

PW_HOST = os.getenv("PW_HOST", "192.168.91.1")
PW_GATEWAY_PASSWORD = os.getenv("PW_GATEWAY_PASSWORD", "")
MQTT_HOST = os.getenv("MQTT_HOST", "192.168.10.217")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "pw3/telemetry")
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "pw3-publisher")
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_QOS = int(os.getenv("MQTT_QOS", "1"))
MQTT_RETAIN = os.getenv("MQTT_RETAIN", "false").lower() in ("1", "true", "yes", "on")
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "15"))


def _num(v):
    return v if isinstance(v, (int, float)) else None


def _sum_or_none(values):
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    return sum(nums)


def _first_non_none(values):
    for v in values:
        if v is not None:
            return v
    return None


def _norm_state(values):
    states = [v for v in values if isinstance(v, str) and v]
    unique = sorted(set(states))
    if not unique:
        return None
    if len(unique) == 1:
        return unique[0]
    return "mixed"


def _section_vin(key):
    if "--" in key:
        return key.split("--", 1)[1]
    return key


def collect_snapshot() -> dict:
    pw = pypowerwall.Powerwall(host=PW_HOST, password="", gw_pwd=PW_GATEWAY_PASSWORD, timeout=8, pwcacheexpire=1)

    cfg = pw.tedapi.get_config() or {}
    vitals = pw.tedapi.get_pw3_vitals() or {}
    status = pw.tedapi.get_status() or {}
    backup_reserve_percent = pw.get_reserve()
    site_info = cfg.get("site_info", {})
    control = status.get("control", {})
    islanding = control.get("islanding", {})
    islander = status.get("esCan", {}).get("bus", {}).get("ISLANDER", {})
    island_ac = islander.get("ISLAND_AcMeasurements", {})
    island_connection = islander.get("ISLAND_GridConnection", {})

    grid_ok = islanding.get("gridOK")
    grid_outage = not grid_ok if isinstance(grid_ok, bool) else None
    control_alerts = control.get("alerts", {}).get("active", [])
    meter_aggregates = {
        item.get("location"): _num(item.get("realPowerW"))
        for item in control.get("meterAggregates", [])
        if isinstance(item, dict) and item.get("location")
    }

    pod_by_vin = {}
    pinv_by_vin = {}
    pvac_by_vin = {}
    pvs_by_vin = {}

    for k, v in vitals.items():
        vin = _section_vin(k)
        if k.startswith("TEPOD--"):
            pod_by_vin[vin] = v
        elif k.startswith("TEPINV--"):
            pinv_by_vin[vin] = v
        elif k.startswith("PVAC--"):
            pvac_by_vin[vin] = v
        elif k.startswith("PVS--"):
            pvs_by_vin[vin] = v

    all_battery_vins = sorted(set(pod_by_vin) | set(pinv_by_vin) | set(pvac_by_vin) | set(pvs_by_vin))

    battery_metrics = []
    for idx, bvin in enumerate(all_battery_vins, start=1):
        pod = pod_by_vin.get(bvin, {})
        pinv = pinv_by_vin.get(bvin, {})
        pvac = pvac_by_vin.get(bvin, {})

        full_wh = _num(pod.get("POD_nom_full_pack_energy"))
        rem_wh = _num(pod.get("POD_nom_energy_remaining"))
        to_charge_wh = _num(pod.get("POD_nom_energy_to_be_charged"))
        soe = round((rem_wh / full_wh) * 100, 2) if full_wh and rem_wh is not None else None

        pv_powers = {s: _num(pvac.get(f"PVAC_PVMeasuredPower_{s}")) for s in "ABCDEF"}
        solar_in_w = _sum_or_none([pv_powers[s] for s in "ABCDEF"])

        pvac_pout_w = _num(pvac.get("PVAC_Pout"))
        pinv_pout_kw = _num(pinv.get("PINV_Pout"))
        if pvac_pout_w is not None:
            power_out_w = round(pvac_pout_w)
        elif pinv_pout_kw is not None:
            power_out_w = round(pinv_pout_kw * 1000)
        else:
            power_out_w = None

        battery_metrics.append({
            "battery_index": idx,
            "battery_vin": bvin,
            "soe_percent_est": soe,
            "energy_remaining_wh": rem_wh,
            "energy_full_wh": full_wh,
            "energy_to_be_charged_wh": to_charge_wh,
            "alerts": pod.get("alerts", []),
            "alerts_count": len(pod.get("alerts", [])),
            "pinv_state": pinv.get("PINV_State"),
            "pvac_state": pvac.get("PVAC_State"),
            "pinv_pout_kw": pinv_pout_kw,
            "pvac_pout_w": pvac_pout_w,
            "power_out_w": power_out_w,
            "solar_in_w": solar_in_w,
            "pv_power_a_w": pv_powers["A"],
            "pv_power_b_w": pv_powers["B"],
            "pv_power_c_w": pv_powers["C"],
            "pv_power_d_w": pv_powers["D"],
            "pv_power_e_w": pv_powers["E"],
            "pv_power_f_w": pv_powers["F"],
        })

    pod_sections = list(pod_by_vin.values())
    pinv_sections = list(pinv_by_vin.values())
    pvac_sections = list(pvac_by_vin.values())
    pvs_sections = list(pvs_by_vin.values())

    full_wh = _sum_or_none([_num(x.get("POD_nom_full_pack_energy")) for x in pod_sections])
    rem_wh = _sum_or_none([_num(x.get("POD_nom_energy_remaining")) for x in pod_sections])
    to_charge_wh = _sum_or_none([_num(x.get("POD_nom_energy_to_be_charged")) for x in pod_sections])
    soe = round((rem_wh / full_wh) * 100, 2) if full_wh and rem_wh is not None else None

    alerts = []
    for x in pod_sections:
        for a in x.get("alerts", []):
            if a not in alerts:
                alerts.append(a)

    payload = {
        "ts": int(time.time()),
        "ts_iso": datetime.now(timezone.utc).isoformat(),
        "pw_time": status.get("system", {}).get("time"),
        "site_name": site_info.get("site_name"),
        "vin": cfg.get("vin"),
        "utility": site_info.get("utility"),
        "timezone": site_info.get("timezone"),
        "nominal_system_energy_ac_kwh": site_info.get("nominal_system_energy_ac"),
        "nominal_system_power_ac_kw": site_info.get("nominal_system_power_ac"),
        "backup_reserve_percent": backup_reserve_percent,
        "battery_blocks_count": len(cfg.get("battery_blocks", [])),
        "tepod_sections_count": len(pod_sections),
        "pvac_sections_count": len(pvac_sections),
        "soe_percent_est": soe,
        "energy_remaining_wh": rem_wh,
        "energy_full_wh": full_wh,
        "energy_to_be_charged_wh": to_charge_wh,
        "alerts": alerts,
        "alerts_count": len(alerts),
        "grid_outage": grid_outage,
        "grid_ok": grid_ok,
        "microgrid_ok": islanding.get("microGridOK"),
        "grid_contactor_closed": islanding.get("contactorClosed"),
        "customer_island_mode": islanding.get("customerIslandMode"),
        "grid_connection_state": island_connection.get("ISLAND_GridConnected"),
        "grid_state": island_ac.get("ISLAND_GridState"),
        "grid_l1_voltage_v": _num(island_ac.get("ISLAND_VL1N_Main")),
        "grid_l2_voltage_v": _num(island_ac.get("ISLAND_VL2N_Main")),
        "load_l1_voltage_v": _num(island_ac.get("ISLAND_VL1N_Load")),
        "load_l2_voltage_v": _num(island_ac.get("ISLAND_VL2N_Load")),
        "control_alerts": control_alerts,
        "control_alerts_count": len(control_alerts),
        "site_power_w": meter_aggregates.get("SITE"),
        "load_power_w": meter_aggregates.get("LOAD"),
        "solar_power_w": meter_aggregates.get("SOLAR"),
        "battery_power_w": meter_aggregates.get("BATTERY"),
        "pinv_state": _norm_state([x.get("PINV_State") for x in pinv_sections]),
        "pinv_pout_kw": _sum_or_none([_num(x.get("PINV_Pout")) for x in pinv_sections]),
        "pinv_fout_hz": _first_non_none([_num(x.get("PINV_Fout")) for x in pinv_sections]),
        "pinv_vout_v": _first_non_none([_num(x.get("PINV_Vout")) for x in pinv_sections]),
        "pinv_vsplit1_v": _first_non_none([_num(x.get("PINV_VSplit1")) for x in pinv_sections]),
        "pinv_vsplit2_v": _first_non_none([_num(x.get("PINV_VSplit2")) for x in pinv_sections]),
        "pvac_state": _norm_state([x.get("PVAC_State") for x in pvac_sections]),
        "pvac_pout_w": _sum_or_none([_num(x.get("PVAC_Pout")) for x in pvac_sections]),
        "pvac_fout_hz": _first_non_none([_num(x.get("PVAC_Fout")) for x in pvac_sections]),
        "pvac_vout_v": _first_non_none([_num(x.get("PVAC_Vout")) for x in pvac_sections]),
        "battery_metrics": battery_metrics,
    }

    for s in "ABCDEF":
        payload[f"pvs_string_{s.lower()}_connected"] = any(
            bool(x.get(f"PVS_String{s}_Connected")) for x in pvs_sections
        )
        payload[f"pvac_pv_state_{s.lower()}"] = _norm_state(
            [x.get(f"PVAC_PvState_{s}") for x in pvac_sections]
        )
        payload[f"pvac_pv_power_{s.lower()}_w"] = _sum_or_none(
            [_num(x.get(f"PVAC_PVMeasuredPower_{s}")) for x in pvac_sections]
        )
        payload[f"pvac_pv_voltage_{s.lower()}_v"] = _first_non_none(
            [_num(x.get(f"PVAC_PVMeasuredVoltage_{s}")) for x in pvac_sections]
        )
        payload[f"pvac_pv_current_{s.lower()}_a"] = _sum_or_none(
            [_num(x.get(f"PVAC_PVCurrent_{s}")) for x in pvac_sections]
        )

    return payload


def publish_payload(payload: dict) -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    client.connect(MQTT_HOST, MQTT_PORT, 10)
    client.loop_start()
    info = client.publish(MQTT_TOPIC, json.dumps(payload), qos=MQTT_QOS, retain=MQTT_RETAIN)
    info.wait_for_publish(timeout=8)
    client.loop_stop()
    client.disconnect()

    if not info.is_published() or info.rc != 0:
        raise RuntimeError(f"MQTT publish failed rc={info.rc}")


def main() -> None:
    if not PW_GATEWAY_PASSWORD:
        raise SystemExit("PW_GATEWAY_PASSWORD is required")

    while True:
        start = time.time()
        try:
            payload = collect_snapshot()
            publish_payload(payload)
            print(
                "ok ts={} soe={} blocks={} topic={} alerts={}".format(
                    payload.get("ts"),
                    payload.get("soe_percent_est"),
                    payload.get("battery_blocks_count"),
                    MQTT_TOPIC,
                    payload.get("alerts_count"),
                ),
                flush=True,
            )
        except Exception as exc:
            print(f"error: {type(exc).__name__}: {exc}", flush=True)

        elapsed = time.time() - start
        sleep_for = max(1.0, POLL_SECONDS - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
