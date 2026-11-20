// Shared by the adaptive handler. Node-RED owns the policy and bounds.
function comfortError(response) { return {ok: false, response}; }
function comfortSnapshot() {
    const mode = flow.get("mode") ?? "Home";
    if (global.get("thermostatoverride"))
        return comfortError("The manual thermostat override is on. I left it alone.");
    if ((global.get("windowsExclBasement") ?? "closed") === "open")
        return comfortError("The windows are open, so I left the thermostat alone.");
    if (mode === "Away" || mode === "Night" || new Date().getHours() >= 18)
        return comfortError("The house is in its away or nighttime schedule. I left it alone.");
    if (![false, 0, "0", "off", "false"].includes(msg.gridOutage))
        return comfortError("Grid power is out or unavailable. I left the thermostat alone.");
    const climate = msg.comfortClimate;
    const hvac = climate?.state;
    if (!["heat", "cool"].includes(hvac))
        return comfortError("The thermostat needs to be in heat or cool mode first.");
    const raw = hvac === "cool" ? msg.comfortWeather?.state : climate?.attributes?.current_temperature;
    const current = raw === null || raw === undefined || raw === "" ? NaN : Number(raw);
    const baseline = hvac === "cool" ? 79 : Number(global.get("heatHomeSP") ?? 67);
    if (!Number.isFinite(current) || current < 45 || current > 100 ||
        !Number.isFinite(baseline) || baseline < 45 || baseline > 92)
        return comfortError("I couldn't get a valid temperature. I left the thermostat alone.");
    return {ok: true, hvac, current, baseline};
}
function comfortPlan(kind, requestId) {
    const s = comfortSnapshot();
    if (!s.ok) return s;
    if (!["hot", "cold"].includes(kind) || !/^[a-zA-Z0-9_-]{8,80}$/.test(requestId || ""))
        return comfortError("Invalid comfort request.");
    const p = {version: 2, kind, requestId, createdAt: Date.now(),
        hvac: s.hvac, baseline: s.baseline};
    if (s.hvac === "cool") {
        // Start cooling at or below the current sensor reading when hot.
        // When cold, lift the stopping point to at least the current reading.
        p.startAt = kind === "hot" ? Math.min(78, Math.floor(s.current * 10 + 1e-7) / 10)
            : Math.max(80, Math.ceil((s.current + 1) * 10 - 1e-7) / 10);
        p.stopAt = Math.round((p.startAt - 1) * 10) / 10;
    } else {
        // Whole Fahrenheit targets match the existing thermostat writer.
        p.heatTarget = kind === "cold" ? Math.max(s.baseline + 1, Math.ceil(s.current + 1))
            : Math.min(s.baseline - 1, Math.floor(s.current - 1));
    }
    const result = comfortValidate(p, true);
    if (!result.ok) return result;
    return {ok: true, proposal: p, requires_confirmation: result.adjustment > 2,
        adjustment: result.adjustment};
}
function comfortValidate(p, confirmed) {
    const s = comfortSnapshot();
    if (!s.ok) return s;
    if (!p || p.version !== 2 || !["hot", "cold"].includes(p.kind) ||
        !/^[a-zA-Z0-9_-]{8,80}$/.test(p.requestId || "") ||
        !Number.isFinite(p.createdAt) || p.createdAt > Date.now() + 5000 ||
        Date.now() - p.createdAt > 90000)
        return comfortError("That temperature request expired. Please ask again.");
    if (p.hvac !== s.hvac || p.baseline !== s.baseline)
        return comfortError("The thermostat settings changed. Please ask again.");
    const target = p.hvac === "cool" ? p.startAt : p.heatTarget;
    if (!Number.isFinite(target) || target < 45 || target > 92 ||
        (p.hvac === "cool" && (!Number.isFinite(p.stopAt) || p.stopAt < 45 ||
            Math.abs(p.startAt - p.stopAt - 1) > 0.00001)))
        return comfortError("Invalid comfort temperatures.");
    const shift = (target - s.baseline) * (p.kind === "hot" ? -1 : 1);
    if (shift < 1 - 0.00001) return comfortError("Invalid comfort adjustment.");
    if (shift > 4 + 0.00001)
        return comfortError("That would need more than a four-degree adjustment, which is the limit. I left it alone.");
    if (shift > 2 + 0.00001 && confirmed !== true)
        return comfortError("That is a larger temperature adjustment. Please confirm it by voice first.");
    return {ok: true, adjustment: Math.round(shift * 10) / 10};
}
