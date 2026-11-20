// One shared, persistent one-hour comfort request. Timers are runtime-only;
// the timestamp is authoritative and is recovered after a tab deploy/restart.
const commands = {
    "voice/button/main_comfort_hot": -1,
    "voice/button/main_comfort_cold": 1
};
const recovery = msg.topic === "main_comfort/recover";
if (!recovery) {
    if (!Object.prototype.hasOwnProperty.call(commands, msg.topic) ||
        String(msg.payload) !== "PRESS" || msg.retain === true) return null;
    flow.set("mainComfortHold", {
        delta: commands[msg.topic],
        expiresAt: Date.now() + 60 * 60 * 1000
    });
}

if (node.mainComfortTimer) clearTimeout(node.mainComfortTimer);
node.mainComfortTimer = null;
const hold = flow.get("mainComfortHold");
const valid = hold && [-1, 1].includes(hold.delta) &&
    Number.isFinite(hold.expiresAt) && hold.expiresAt > Date.now() &&
    hold.expiresAt <= Date.now() + 60 * 60 * 1000;
if (!valid) {
    flow.set("mainComfortHold", null);
    node.status({fill: "grey", shape: "ring", text: "normal schedule"});
    return {payload: {}};
}

node.status({fill: "blue", shape: "dot", text:
    (hold.delta < 0 ? "cooler" : "warmer") + " until " + new Date(hold.expiresAt).toLocaleTimeString()});
node.mainComfortTimer = setTimeout(() => {
    const current = flow.get("mainComfortHold");
    // An older callback must never cancel a newer request.
    if (current && current.expiresAt === hold.expiresAt && current.delta === hold.delta &&
        current.expiresAt <= Date.now()) {
        flow.set("mainComfortHold", null);
        node.status({fill: "grey", shape: "ring", text: "normal schedule"});
        node.send({payload: {}});
    }
    node.mainComfortTimer = null;
}, hold.expiresAt - Date.now());
// Re-evaluate through the usual outage read and Mega Logic immediately.
return {payload: {}};
