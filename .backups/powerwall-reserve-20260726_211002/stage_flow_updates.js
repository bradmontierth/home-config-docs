const fs = require("fs");

const backupDir = "/home/pi/home_config/.backups/powerwall-reserve-20260726_211002";
const teslaPath = `${backupDir}/tesla-tab.json`;
const forecastPath = `${backupDir}/forecast-tab.json`;

const tesla = JSON.parse(fs.readFileSync(teslaPath, "utf8"));
const forecast = JSON.parse(fs.readFileSync(forecastPath, "utf8"));

function node(flow, id) {
  const found = flow.nodes.find((item) => item.id === id);
  if (!found) throw new Error(`Missing node ${id}`);
  return found;
}

function removeNodes(flow, ids) {
  const wanted = new Set(ids);
  flow.nodes = flow.nodes.filter((item) => !wanted.has(item.id));
}

function makeReserveStateNode(existing, targetId, name) {
  return {
    id: existing.id,
    type: "api-current-state",
    z: existing.z,
    name,
    server: "23fd91e9137b71c5",
    version: 3,
    outputs: 1,
    halt_if: "",
    halt_if_type: "str",
    halt_if_compare: "is",
    entity_id: "sensor.powerwall_backup_reserve",
    state_type: "num",
    blockInputOverrides: false,
    outputProperties: [
      {
        property: "payload",
        propertyType: "msg",
        value: "",
        valueType: "entityState",
      },
      {
        property: "reserveEntity",
        propertyType: "msg",
        value: "",
        valueType: "entity",
      },
    ],
    for: "0",
    forType: "num",
    forUnits: "minutes",
    override_topic: false,
    state_location: "payload",
    override_payload: "msg",
    entity_location: "reserveEntity",
    override_data: "msg",
    x: existing.x,
    y: existing.y,
    wires: [[targetId]],
  };
}

// Feed local Powerwall telemetry into the existing reserve MQTT publisher.
const pw3Input = node(tesla, "c1f9c36e2279e0fa");
if (!pw3Input.wires[0].includes("pw_reserve_mqtt_build")) {
  pw3Input.wires[0].push("pw_reserve_mqtt_build");
}

// Remove the recurring cloud Fleet API site_info poll.
removeNodes(tesla, [
  "pw_reserve_mqtt_poll",
  "pw_reserve_mqtt_get_info",
  "pw_reserve_mqtt_http",
]);

const reservePublisher = node(tesla, "pw_reserve_mqtt_build");
reservePublisher.name = "publish local reserve MQTT";
reservePublisher.func = `let p = msg.payload;
if (typeof p === "string") {
    try { p = JSON.parse(p); } catch (error) { return null; }
}
if (!p || typeof p !== "object") return null;

const reserve = Number(p.backup_reserve_percent);
if (!Number.isFinite(reserve)) {
    node.warn("Local TEDAPI backup reserve missing from pw3/telemetry");
    return [null, null, null];
}

const nowMs = Date.now();
const previous = flow.get("pw3LocalReservePublish") || {};
const changed = !Number.isFinite(Number(previous.reserve)) || Number(previous.reserve) !== reserve;
const refreshDue = !Number.isFinite(Number(previous.publishedAt)) || nowMs - Number(previous.publishedAt) >= 5 * 60 * 1000;
if (!changed && !refreshDue) return [null, null, null];

flow.set("pw3LocalReservePublish", { reserve, publishedAt: nowMs });

const device = {
    identifiers: ["pw3_leader"],
    name: "Tesla Powerwall 3",
    manufacturer: "Tesla",
    model: "Powerwall 3"
};

const stateTopic = "homeassistant/sensor/pw3/backup_reserve/state";
const configTopic = "homeassistant/sensor/pw3/backup_reserve/config";
const nowIso = new Date(nowMs).toISOString();

const configMsg = {
    topic: configTopic,
    retain: true,
    payload: JSON.stringify({
        unique_id: "pw3_backup_reserve",
        default_entity_id: "sensor.powerwall_backup_reserve",
        device,
        name: "Powerwall Backup Reserve",
        state_topic: stateTopic,
        unit_of_measurement: "%",
        value_template: "{{ value_json.backup_reserve_percent }}",
        json_attributes_topic: stateTopic,
        icon: "mdi:home-battery",
        device_class: "battery",
        state_class: "measurement"
    })
};

const stateMsg = {
    topic: stateTopic,
    retain: true,
    payload: JSON.stringify({
        backup_reserve_percent: reserve,
        source: "local_tedapi",
        site_name: p.site_name || null,
        telemetry_ts: p.ts ?? null,
        telemetry_ts_iso: p.ts_iso || null,
        updated_at: nowIso
    })
};

msg.payload = {
    reason: changed ? "local reserve changed" : "local reserve periodic refresh",
    reserve,
    source: "local_tedapi",
    stateTopic,
    updated_at: nowIso
};
return [configMsg, stateMsg, msg];`;
node(tesla, "pw_reserve_mqtt_debug").name = "Powerwall local reserve MQTT publish";

// Use the local Home Assistant reserve sensor for post-command verification.
const verificationNodes = [
  ["pw_reserve_verify_build", "pw_reserve_verify_eval", "verify local reserve reset"],
  ["pw_tou_lower_verify_build", "pw_tou_lower_verify_eval", "verify local temporary reserve"],
  ["pw_tou_restore_verify_build", "pw_tou_restore_verify_eval", "verify local reserve restore"],
];
for (const [id, targetId, name] of verificationNodes) {
  const index = tesla.nodes.findIndex((item) => item.id === id);
  tesla.nodes[index] = makeReserveStateNode(tesla.nodes[index], targetId, name);
}
removeNodes(tesla, [
  "pw_reserve_verify_http",
  "pw_tou_lower_verify_http",
  "pw_tou_restore_verify_http",
]);

for (const id of [
  "pw_reserve_verify_eval",
  "pw_tou_lower_verify_eval",
  "pw_tou_restore_verify_eval",
]) {
  const item = node(tesla, id);
  const before = "const reserve = Number(msg.payload?.response?.backup_reserve_percent);";
  if (!item.func.includes(before)) throw new Error(`Reserve parser not found in ${id}`);
  item.func = item.func.replace(before, "const reserve = Number(msg.payload);");
}

// Read the current reserve before consuming the forecast sensor chain.
const touPoll = node(tesla, "pw_tou_poll");
touPoll.wires = [["pw_tou_current_reserve"]];
tesla.nodes.push({
  id: "pw_tou_current_reserve",
  type: "api-current-state",
  z: tesla.id,
  name: "Current local Powerwall reserve",
  server: "23fd91e9137b71c5",
  version: 3,
  outputs: 1,
  halt_if: "",
  halt_if_type: "str",
  halt_if_compare: "is",
  entity_id: "sensor.powerwall_backup_reserve",
  state_type: "num",
  blockInputOverrides: false,
  outputProperties: [
    {
      property: "powerwall_backup_reserve",
      propertyType: "msg",
      value: "",
      valueType: "entityState",
    },
  ],
  for: "0",
  forType: "num",
  forUnits: "minutes",
  override_topic: false,
  state_location: "powerwall_backup_reserve",
  override_payload: "msg",
  entity_location: "currentReserveData",
  override_data: "msg",
  x: 430,
  y: 3820,
  wires: [["pw_tou_eta"]],
});

const touEval = node(tesla, "pw_tou_eval");
const unitHelper = `function powerKwText(value) {
    const watts = parseNumber(value);
    return watts === null ? 'unknown' : \`\${(watts / 1000).toFixed(2)} kW\`;
}

function percentText(value) {
    const percent = parseNumber(value);
    return percent === null ? 'unknown' : \`\${percent}%\`;
}

`;
const helperAnchor = "function diagnostic(reason, extra = {}) {";
if (!touEval.func.includes(helperAnchor)) throw new Error("TOU helper anchor missing");
touEval.func = touEval.func.replace(helperAnchor, unitHelper + helperAnchor);

const windowAnchor = `if (hour < 20 || now >= touEnd) {
    return [null, diagnostic('outside 8-10 PM window', { touEnd }), null];
}

`;
const reserveGuard = `const forecastAttrs = msg.data?.attributes || {};
const forecastReserve = parseNumber(forecastAttrs.reserve_percent);
const currentReserve = parseNumber(msg.powerwall_backup_reserve);
if (
    forecastReserve !== null &&
    currentReserve !== null &&
    Math.abs(forecastReserve - currentReserve) > 0.1
) {
    return [null, diagnostic('reserve mismatch; waiting for fresh forecast', { touEnd }), null];
}

`;
if (!touEval.func.includes(windowAnchor)) throw new Error("TOU window anchor missing");
touEval.func = touEval.func.replace(windowAnchor, windowAnchor + reserveGuard);

const diagnosticAnchor = `            usableEnergy: msg.powerwall_usable_energy_to_reserve
`;
const diagnosticReplacement = `            usableEnergy: msg.powerwall_usable_energy_to_reserve,
            currentReserve: msg.powerwall_backup_reserve,
            forecastReserve: msg.data?.attributes?.reserve_percent,
            batteryPercent: msg.data?.attributes?.battery_percent
`;
if (!touEval.func.includes(diagnosticAnchor)) throw new Error("TOU diagnostic anchor missing");
touEval.func = touEval.func.replace(diagnosticAnchor, diagnosticReplacement);

const oldAlertLines = `    \`Usable to reserve: \${valueWithUnit(msg.powerwall_usable_energy_to_reserve, 'kWh')}\`,
    \`Avg discharge (10m): \${valueWithUnit(msg.powerwall_avg_discharge_power_10m, 'kW')}\`,
`;
const newAlertLines = `    \`Battery / reserve used: \${percentText(forecastAttrs.battery_percent)} / \${percentText(forecastReserve)}\`,
    \`Usable to reserve: \${valueWithUnit(msg.powerwall_usable_energy_to_reserve, 'kWh')}\`,
    \`Avg discharge (10m): \${powerKwText(msg.powerwall_avg_discharge_power_10m)}\`,
`;
if (!touEval.func.includes(oldAlertLines)) throw new Error("TOU alert lines missing");
touEval.func = touEval.func.replace(oldAlertLines, newAlertLines);

// Recalculate the forecast immediately when the local reserve changes.
forecast.nodes.push({
  id: "pwr_reserve_changed",
  type: "server-state-changed",
  z: forecast.id,
  name: "Powerwall reserve changed",
  server: "23fd91e9137b71c5",
  version: 6,
  outputs: 1,
  exposeAsEntityConfig: "",
  entities: {
    entity: ["sensor.powerwall_backup_reserve"],
    substring: [],
    regex: [],
  },
  outputInitially: false,
  stateType: "num",
  ifState: "",
  ifStateType: "str",
  ifStateOperator: "is",
  outputOnlyOnStateChange: true,
  for: "0",
  forType: "num",
  forUnits: "minutes",
  ignorePrevStateNull: false,
  ignorePrevStateUnknown: false,
  ignorePrevStateUnavailable: false,
  ignoreCurrentStateUnknown: true,
  ignoreCurrentStateUnavailable: true,
  outputProperties: [
    {
      property: "triggerReservePct",
      propertyType: "msg",
      value: "",
      valueType: "entityState",
    },
  ],
  x: 180,
  y: 280,
  wires: [["pwr_battery"]],
});

for (const flow of [tesla, forecast]) {
  const ids = flow.nodes.map((item) => item.id);
  if (new Set(ids).size !== ids.length) throw new Error(`Duplicate node id in ${flow.label}`);
  for (const item of flow.nodes) {
    if (item.type === "function") {
      new Function("msg", "node", "flow", "global", "context", item.func);
    }
  }
}

fs.writeFileSync(`${backupDir}/tesla-tab.staged.json`, JSON.stringify(tesla));
fs.writeFileSync(`${backupDir}/forecast-tab.staged.json`, JSON.stringify(forecast));

console.log(JSON.stringify({
  teslaNodes: tesla.nodes.length,
  forecastNodes: forecast.nodes.length,
  removedCloudPoll: !tesla.nodes.some((item) => item.id === "pw_reserve_mqtt_poll"),
  localReservePublisherWired: node(tesla, "c1f9c36e2279e0fa").wires[0].includes("pw_reserve_mqtt_build"),
  localVerificationNodes: verificationNodes.map(([id]) => node(tesla, id).type),
  reserveForecastTrigger: node(forecast, "pwr_reserve_changed").entities.entity,
}, null, 2));
