// Outputs: 1 normal Mega Logic reevaluation, 2 HTTP proposal response.
// Applying always arrives through HA mqtt.publish (or the original HA buttons).
function remember(id, result) {
    if (!/^[a-zA-Z0-9_-]{8,80}$/.test(id || "")) return;
    const recent = (flow.get("mainComfortResults") || []).filter(r => Date.now() - r.at < 120000 && r.id !== id);
    recent.push({id, at: Date.now(), result});
    flow.set("mainComfortResults", recent.slice(-20));
}
function armHold() {
    if (node.mainComfortTimer) clearTimeout(node.mainComfortTimer);
    node.mainComfortTimer = null;
    const hold = flow.get("mainComfortHold");
    const valid = hold && Number.isFinite(hold.expiresAt) && hold.expiresAt > Date.now() &&
        hold.expiresAt <= Date.now() + 3600000;
    if (!valid) {
        flow.set("mainComfortHold", null);
        node.status({fill: "grey", shape: "ring", text: "normal schedule"});
        return;
    }
    const timer = setTimeout(() => {
        const current = flow.get("mainComfortHold");
        if (current && current.expiresAt === hold.expiresAt &&
            current.requestId === hold.requestId && current.expiresAt <= Date.now()) {
            flow.set("mainComfortHold", null);
            node.status({fill: "grey", shape: "ring", text: "normal schedule"});
            node.send([{payload: {}}, null]);
        }
        if (node.mainComfortTimer === timer) node.mainComfortTimer = null;
    }, hold.expiresAt - Date.now());
    node.mainComfortTimer = timer;
    node.status({fill: "blue", shape: "dot", text: "comfort until " + new Date(hold.expiresAt).toLocaleTimeString()});
}
if (msg.req) {
    msg.payload = comfortPlan(msg.req.query?.kind, msg.req.query?.request_id);
    msg.headers = {"Content-Type": "application/json", "Cache-Control": "no-store"};
    return [null, msg];
}
if (msg.topic === "main_comfort/recover") {
    armHold();
    return [{payload: {}}, null];
}
if (msg.retain === true) return null;
let proposal, confirmed = false;
if (msg.topic === "voice/main_comfort/apply") {
    let body;
    try { body = typeof msg.payload === "string" ? JSON.parse(msg.payload) : msg.payload; }
    catch (_) { return null; }
    proposal = body?.proposal;
    confirmed = body?.confirmed === true;
    const existing = (flow.get("mainComfortResults") || []).find(r => r.id === proposal?.requestId && Date.now() - r.at < 120000);
    if (existing) return null; // MQTT QoS duplicate must not refresh the hour.
} else if (["voice/button/main_comfort_hot", "voice/button/main_comfort_cold"].includes(msg.topic) && String(msg.payload) === "PRESS") {
    // Dashboard buttons retain the automatic tier only; they cannot bypass yes/no.
    const planned = comfortPlan(msg.topic.endsWith("_hot") ? "hot" : "cold",
        "button_" + Date.now() + "_" + Math.random().toString(36).slice(2));
    if (!planned.ok) { node.status({fill: "yellow", shape: "ring", text: planned.response}); return null; }
    proposal = planned.proposal;
} else return null;
const valid = comfortValidate(proposal, confirmed);
if (!valid.ok) {
    remember(proposal?.requestId, valid);
    node.status({fill: "yellow", shape: "ring", text: valid.response});
    return null;
}
flow.set("mainComfortHold", {...proposal, expiresAt: Date.now() + 3600000});
armHold();
remember(proposal.requestId, {ok: true, proposal, expiresAt: flow.get("mainComfortHold").expiresAt});
return [{payload: {}}, null];
